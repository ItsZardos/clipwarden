"""Runtime integration tests.

End-to-end wiring from a synthetic :class:`ClipboardEvent` through
classifier, detector, logger, and notifier. The watcher is replaced
with a fake that exposes ``emit(event)`` so tests can feed any
sequence of clipboard events without touching Windows.

The runtime is the only module that depends on ``ctypes``;
``last_input_ts_ms`` is patched to return a controllable value.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from clipwarden import runtime as rt_module
from clipwarden.config import Config
from clipwarden.detector import DetectionEvent
from clipwarden.runtime import Runtime, RuntimePaths
from clipwarden.watcher import ClipboardEvent

BTC_A = "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"
BTC_B = "bc1qrp33g0q5c5txsp9arysrx4k6zdkfs4nce4xj0gdcccefvpysxf3qccfmv3"
ETH_A = "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"


class _FakeWatcher:
    def __init__(self, on_event) -> None:
        self._on_event = on_event
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self, *, timeout: float = 2.0) -> None:
        self.stopped = True

    def emit(self, ev: ClipboardEvent) -> None:
        self._on_event(ev)


class _RecordingNotifier:
    def __init__(self) -> None:
        self.substitutions: list[DetectionEvent] = []
        self.infos: list[tuple[str, str]] = []

    def notify_substitution(self, event: DetectionEvent) -> None:
        self.substitutions.append(event)

    def notify_info(self, title: str, body: str) -> None:
        self.infos.append((title, body))


@pytest.fixture
def tmp_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPWARDEN_APPDATA", str(tmp_path))
    yield tmp_path


@pytest.fixture
def frozen_last_input(monkeypatch):
    """Freeze GetLastInputInfo so tests can drive the user-activity gate."""
    current = {"ts": 0}

    def _sample():
        return current["ts"]

    monkeypatch.setattr(rt_module, "last_input_ts_ms", _sample, raising=True)
    return current


def _read_log_lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _build(
    tmp_appdata, cfg=None, notifier=None
) -> tuple[Runtime, _FakeWatcher, _RecordingNotifier, Path]:
    rt_paths = RuntimePaths(
        config=tmp_appdata / "config.json",
        whitelist=tmp_appdata / "whitelist.json",
        log=tmp_appdata / "log.jsonl",
    )
    captured: dict = {}

    def factory(on_event):
        w = _FakeWatcher(on_event)
        captured["watcher"] = w
        return w

    rec = notifier if notifier is not None else _RecordingNotifier()
    from clipwarden.classifier import Chain  # noqa: PLC0415
    from clipwarden.detector import Detector  # noqa: PLC0415
    from clipwarden.logger import get_logger  # noqa: PLC0415
    from clipwarden.whitelist import Whitelist  # noqa: PLC0415

    cfg = cfg or Config()
    wl = Whitelist()
    enabled = frozenset(Chain(c) for c in cfg.enabled_chains if c in Chain.__members__)
    rt = Runtime(
        cfg=cfg,
        rt_paths=rt_paths,
        detector=Detector(
            substitution_window_ms=cfg.substitution_window_ms,
            is_whitelisted=wl.contains,
            enabled_chains=enabled,
        ),
        notifier=rec,
        logger=get_logger(rt_paths.log),
        watcher_factory=factory,
    )
    return rt, captured["watcher"], rec, rt_paths.log


def _cleanup(rt: Runtime) -> None:
    rt.stop()


def test_substitution_fires_log_and_toast(tmp_appdata, frozen_last_input):
    rt, watcher, rec, log_path = _build(tmp_appdata)
    rt.start()
    try:
        watcher.emit(ClipboardEvent(text=BTC_A, ts_ms=1000, seq=1))
        watcher.emit(ClipboardEvent(text=BTC_B, ts_ms=1200, seq=2))
    finally:
        _cleanup(rt)

    assert len(rec.substitutions) == 1
    ev = rec.substitutions[0]
    assert ev.chain == "BTC"
    assert ev.before == BTC_A
    assert ev.after == BTC_B
    assert ev.whitelisted is False

    lines = _read_log_lines(log_path)
    assert len(lines) == 1
    assert lines[0]["kind"] == "detection"
    assert lines[0]["chain"] == "BTC"
    assert lines[0]["before"] == BTC_A
    assert lines[0]["after"] == BTC_B


def test_whitelisted_pair_logs_but_does_not_toast(tmp_appdata, frozen_last_input):
    from clipwarden.whitelist import Whitelist

    wl_path = tmp_appdata / "whitelist.json"
    wl = Whitelist()
    wl.add("BTC", BTC_B, note="test")
    wl.save(wl_path)

    rec = _RecordingNotifier()
    rt_paths = RuntimePaths(
        config=tmp_appdata / "config.json",
        whitelist=wl_path,
        log=tmp_appdata / "log.jsonl",
    )
    captured: dict = {}

    def factory(on_event):
        w = _FakeWatcher(on_event)
        captured["watcher"] = w
        return w

    from clipwarden.detector import Detector
    from clipwarden.logger import get_logger

    wl_loaded = Whitelist.load(wl_path)
    rt = Runtime(
        cfg=Config(),
        rt_paths=rt_paths,
        detector=Detector(
            substitution_window_ms=Config().substitution_window_ms,
            is_whitelisted=wl_loaded.contains,
        ),
        notifier=rec,
        logger=get_logger(rt_paths.log),
        watcher_factory=factory,
    )
    watcher = captured["watcher"]

    rt.start()
    try:
        watcher.emit(ClipboardEvent(text=BTC_A, ts_ms=1000, seq=1))
        watcher.emit(ClipboardEvent(text=BTC_B, ts_ms=1200, seq=2))
    finally:
        _cleanup(rt)

    lines = _read_log_lines(rt_paths.log)
    assert len(lines) == 1
    assert lines[0]["kind"] == "whitelisted_skip"
    assert rec.substitutions == []


def test_cross_chain_transition_does_not_alert(tmp_appdata, frozen_last_input):
    rt, watcher, rec, log_path = _build(tmp_appdata)
    rt.start()
    try:
        watcher.emit(ClipboardEvent(text=BTC_A, ts_ms=1000, seq=1))
        watcher.emit(ClipboardEvent(text=ETH_A, ts_ms=1200, seq=2))
    finally:
        _cleanup(rt)

    assert rec.substitutions == []
    assert _read_log_lines(log_path) == []


def test_user_input_between_copies_suppresses_alert(tmp_appdata, frozen_last_input):
    rt, watcher, rec, log_path = _build(tmp_appdata)
    rt.start()
    try:
        watcher.emit(ClipboardEvent(text=BTC_A, ts_ms=1000, seq=1))
        frozen_last_input["ts"] = 1100
        watcher.emit(ClipboardEvent(text=BTC_B, ts_ms=1200, seq=2))
    finally:
        _cleanup(rt)

    assert rec.substitutions == []
    assert _read_log_lines(log_path) == []


def test_notifications_disabled_skips_toast(tmp_appdata, frozen_last_input):
    rt, watcher, rec, log_path = _build(tmp_appdata, cfg=Config(notifications_enabled=False))
    rt.start()
    try:
        watcher.emit(ClipboardEvent(text=BTC_A, ts_ms=1000, seq=1))
        watcher.emit(ClipboardEvent(text=BTC_B, ts_ms=1200, seq=2))
    finally:
        _cleanup(rt)

    assert rec.substitutions == []
    lines = _read_log_lines(log_path)
    assert len(lines) == 1
    assert lines[0]["kind"] == "detection"


def test_build_runtime_from_disk_works_end_to_end(tmp_appdata, frozen_last_input, monkeypatch):
    captured: dict = {}

    def factory(on_event):
        w = _FakeWatcher(on_event)
        captured["watcher"] = w
        return w

    monkeypatch.setattr(rt_module, "Watcher", factory, raising=True)
    rec = _RecordingNotifier()
    rt = rt_module.build_runtime(notifier=rec)
    rt.start()
    try:
        watcher = captured["watcher"]
        assert watcher.started
        watcher.emit(ClipboardEvent(text=BTC_A, ts_ms=1000, seq=1))
        watcher.emit(ClipboardEvent(text=BTC_B, ts_ms=1200, seq=2))
    finally:
        rt.stop()
    assert len(rec.substitutions) == 1
    assert Path(os.environ["CLIPWARDEN_APPDATA"]).joinpath("log.jsonl").exists()


def test_disabled_chain_produces_no_alert_end_to_end(tmp_appdata, frozen_last_input):
    # Finding 1: a user-disabled chain must not alert at any layer of
    # the pipeline, not the logger, not the notifier, not the
    # dispatcher. Pin this end-to-end so a future refactor that
    # moves the gate out of the classifier still holds the invariant.
    cfg = Config(enabled_chains=("ETH",))  # BTC intentionally off
    rt, watcher, rec, log_path = _build(tmp_appdata, cfg=cfg)
    rt.start()
    try:
        watcher.emit(ClipboardEvent(text=BTC_A, ts_ms=1000, seq=1))
        watcher.emit(ClipboardEvent(text=BTC_B, ts_ms=1200, seq=2))
    finally:
        _cleanup(rt)

    assert rec.substitutions == []
    assert _read_log_lines(log_path) == []


def test_build_runtime_honors_enabled_chains(tmp_appdata, frozen_last_input, monkeypatch):
    # The factory path (the one the real app uses) must thread
    # enabled_chains from disk through to the detector. If someone
    # adds a code path that forgets the plumbing, this test catches
    # it even if the in-test `_build` helper keeps working.
    import json  # noqa: PLC0415

    captured: dict = {}

    def factory(on_event):
        w = _FakeWatcher(on_event)
        captured["watcher"] = w
        return w

    monkeypatch.setattr(rt_module, "Watcher", factory, raising=True)
    cfg_path = tmp_appdata / "config.json"
    cfg_path.write_text(json.dumps({"enabled_chains": ["ETH"]}), encoding="utf-8")

    rec = _RecordingNotifier()
    rt = rt_module.build_runtime(notifier=rec)
    rt.start()
    try:
        watcher = captured["watcher"]
        watcher.emit(ClipboardEvent(text=BTC_A, ts_ms=1000, seq=1))
        watcher.emit(ClipboardEvent(text=BTC_B, ts_ms=1200, seq=2))
    finally:
        rt.stop()

    assert rec.substitutions == []


class _RecordingDispatcher:
    """Stand-in for :class:clipwarden.alert.AlertDispatcher."""

    def __init__(self) -> None:
        self.dispatched: list = []

    def dispatch(self, event) -> None:
        self.dispatched.append(event)


def _build_with_dispatcher(tmp_appdata, dispatcher, cfg=None):
    rt_paths = RuntimePaths(
        config=tmp_appdata / "config.json",
        whitelist=tmp_appdata / "whitelist.json",
        log=tmp_appdata / "log.jsonl",
    )
    captured: dict = {}

    def factory(on_event):
        w = _FakeWatcher(on_event)
        captured["watcher"] = w
        return w

    rec = _RecordingNotifier()
    from clipwarden.classifier import Chain  # noqa: PLC0415
    from clipwarden.detector import Detector  # noqa: PLC0415
    from clipwarden.logger import get_logger  # noqa: PLC0415
    from clipwarden.whitelist import Whitelist  # noqa: PLC0415

    cfg = cfg or Config()
    wl = Whitelist()
    enabled = frozenset(Chain(c) for c in cfg.enabled_chains if c in Chain.__members__)
    rt = Runtime(
        cfg=cfg,
        rt_paths=rt_paths,
        detector=Detector(
            substitution_window_ms=cfg.substitution_window_ms,
            is_whitelisted=wl.contains,
            enabled_chains=enabled,
        ),
        notifier=rec,
        logger=get_logger(rt_paths.log),
        alert_dispatcher=dispatcher,
        watcher_factory=factory,
    )
    return rt, captured["watcher"], rec, rt_paths.log


def test_dispatcher_receives_alert_event_instead_of_notifier(tmp_appdata, frozen_last_input):
    from clipwarden.alert import AlertEvent  # noqa: PLC0415

    dispatcher = _RecordingDispatcher()
    rt, watcher, rec, log_path = _build_with_dispatcher(tmp_appdata, dispatcher)
    rt.start()
    try:
        watcher.emit(ClipboardEvent(text=BTC_A, ts_ms=1000, seq=1))
        watcher.emit(ClipboardEvent(text=BTC_B, ts_ms=1200, seq=2))
    finally:
        _cleanup(rt)

    assert len(dispatcher.dispatched) == 1
    ev = dispatcher.dispatched[0]
    assert isinstance(ev, AlertEvent)
    assert ev.chain == "BTC"
    assert ev.before == BTC_A
    assert ev.after == BTC_B
    # Notifier was NOT called directly when a dispatcher was wired;
    # the toast path now goes through a ToastChannel inside the
    # dispatcher rather than being invoked as a side-effect here.
    assert rec.substitutions == []
    assert len(_read_log_lines(log_path)) == 1


def test_dispatcher_is_skipped_when_notifications_disabled(tmp_appdata, frozen_last_input):
    dispatcher = _RecordingDispatcher()
    rt, watcher, rec, log_path = _build_with_dispatcher(
        tmp_appdata, dispatcher, cfg=Config(notifications_enabled=False)
    )
    rt.start()
    try:
        watcher.emit(ClipboardEvent(text=BTC_A, ts_ms=1000, seq=1))
        watcher.emit(ClipboardEvent(text=BTC_B, ts_ms=1200, seq=2))
    finally:
        _cleanup(rt)

    # Legacy kill-switch honored: no dispatch, no notifier, audit
    # entry still written.
    assert dispatcher.dispatched == []
    assert rec.substitutions == []
    assert len(_read_log_lines(log_path)) == 1


def test_dispatcher_not_called_for_whitelisted_pair(tmp_appdata, frozen_last_input):
    from clipwarden.classifier import Chain  # noqa: PLC0415
    from clipwarden.detector import Detector  # noqa: PLC0415
    from clipwarden.logger import get_logger  # noqa: PLC0415
    from clipwarden.whitelist import Whitelist  # noqa: PLC0415

    wl = Whitelist()
    wl.add("BTC", BTC_B, note="test")
    wl.save(tmp_appdata / "whitelist.json")

    rt_paths = RuntimePaths(
        config=tmp_appdata / "config.json",
        whitelist=tmp_appdata / "whitelist.json",
        log=tmp_appdata / "log.jsonl",
    )
    captured: dict = {}

    def factory(on_event):
        w = _FakeWatcher(on_event)
        captured["watcher"] = w
        return w

    dispatcher = _RecordingDispatcher()
    wl_loaded = Whitelist.load(rt_paths.whitelist)
    enabled = frozenset(Chain(c) for c in Config().enabled_chains if c in Chain.__members__)
    rt = Runtime(
        cfg=Config(),
        rt_paths=rt_paths,
        detector=Detector(
            substitution_window_ms=Config().substitution_window_ms,
            is_whitelisted=wl_loaded.contains,
            enabled_chains=enabled,
        ),
        notifier=_RecordingNotifier(),
        logger=get_logger(rt_paths.log),
        alert_dispatcher=dispatcher,
        watcher_factory=factory,
    )
    watcher = captured["watcher"]

    rt.start()
    try:
        watcher.emit(ClipboardEvent(text=BTC_A, ts_ms=1000, seq=1))
        watcher.emit(ClipboardEvent(text=BTC_B, ts_ms=1200, seq=2))
    finally:
        _cleanup(rt)

    assert dispatcher.dispatched == []
    lines = _read_log_lines(rt_paths.log)
    assert len(lines) == 1
    assert lines[0]["kind"] == "whitelisted_skip"
