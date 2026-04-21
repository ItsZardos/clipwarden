"""Tray state-machine tests.

Exercises :class:`clipwarden.tray.TrayApp` against a recording
``_FakeIcon`` so the full menu wiring, pause timer, icon swaps, and
About dialog can be asserted without spinning up a real pystray
backend. Win32 and filesystem side-effects are intercepted via
injected callables (``message_box``, ``open_path``, ``timer_factory``)
on the constructor, so the tests are hermetic on CI.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pystray
import pytest

from clipwarden import tray


class _FakeIcon:
    """Records constructor args and every ``icon`` mutation.

    Matches the positional signature ``TrayApp.run()`` uses:
    ``Icon(name, icon, title, menu)``.
    """

    def __init__(self, name: str, icon: Any, title: str, menu: Any) -> None:
        self.name = name
        self._image = icon
        self.title = title
        self.menu = menu
        self.icon_history: list[Any] = [icon]
        self.ran = False
        self.stopped = False

    @property
    def icon(self) -> Any:
        return self._image

    @icon.setter
    def icon(self, value: Any) -> None:
        self._image = value
        self.icon_history.append(value)

    def run(self) -> None:
        self.ran = True

    def stop(self) -> None:
        self.stopped = True


class _FakeRuntime:
    def __init__(self) -> None:
        self.starts = 0
        self.stops = 0

    def start(self) -> None:
        self.starts += 1

    def stop(self) -> None:
        self.stops += 1


class _FakeTimer:
    """Captures ``threading.Timer`` args; ``fire`` drives the callback."""

    def __init__(self, seconds: float, callback: Any) -> None:
        self.seconds = seconds
        self.callback = callback
        self.started = False
        self.cancelled = False
        self.daemon = False

    def start(self) -> None:
        self.started = True

    def cancel(self) -> None:
        self.cancelled = True

    def fire(self) -> None:
        self.callback()


@pytest.fixture
def timers() -> list[_FakeTimer]:
    return []


@pytest.fixture
def timer_factory(timers):
    def _make(seconds: float, callback: Any) -> _FakeTimer:
        t = _FakeTimer(seconds, callback)
        timers.append(t)
        return t

    return _make


@pytest.fixture
def runtime() -> _FakeRuntime:
    return _FakeRuntime()


@pytest.fixture
def paths(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        config=tmp_path / "config.json",
        whitelist=tmp_path / "whitelist.json",
        log=tmp_path / "log.jsonl",
    )


@pytest.fixture
def message_box_calls():
    calls: list[tuple] = []

    def fn(hwnd, body, title, flags):
        calls.append((hwnd, body, title, flags))
        return 1  # IDOK

    fn.calls = calls  # type: ignore[attr-defined]
    return fn


@pytest.fixture
def open_path_calls():
    calls: list[str] = []

    def fn(path):
        calls.append(path)

    fn.calls = calls  # type: ignore[attr-defined]
    return fn


@pytest.fixture
def app(
    runtime,
    paths,
    message_box_calls,
    open_path_calls,
    timer_factory,
    monkeypatch,
):
    # Skip the real Pillow load; tests only care about identity of
    # the swapped-in value.
    monkeypatch.setattr(tray, "_load_image", lambda name: f"image:{name}")
    a = tray.TrayApp(
        runtime=runtime,
        notifier=None,
        rt_paths=paths,
        version="1.0.0",
        icon_factory=_FakeIcon,
        message_box=message_box_calls,
        timer_factory=timer_factory,
        open_path=open_path_calls,
    )
    a.run()
    return a


def _labels(menu) -> list[str]:
    return [it.text for it in menu]


def test_run_constructs_icon_with_enabled_image(app):
    assert isinstance(app._icon, _FakeIcon)
    assert app._icon.icon == "image:icon.ico"
    assert app._icon.ran is True


def test_top_level_menu_layout(app):
    labels = _labels(app._icon.menu)
    sep = pystray.Menu.SEPARATOR.text
    assert labels == [
        "Enable",
        "Pause",
        sep,
        "Open Config",
        "Open Log Folder",
        "Open History Folder",
        sep,
        "About ClipWarden",
        "Quit ClipWarden",
    ]


def test_pause_submenu_layout(app):
    pause = next(it for it in app._icon.menu if it.text == "Pause")
    labels = _labels(pause.submenu)
    sep = pystray.Menu.SEPARATOR.text
    assert labels == ["15 minutes", "1 hour", "Until I resume", sep, "Resume now"]


def test_enable_item_reflects_state(app):
    enable = next(it for it in app._icon.menu if it.text == "Enable")
    assert enable.checked is True
    app._disable()
    assert enable.checked is False


def test_resume_now_is_disabled_until_paused(app):
    pause = next(it for it in app._icon.menu if it.text == "Pause")
    resume = next(it for it in pause.submenu if it.text == "Resume now")
    assert resume.enabled is False
    app._on_pause_indefinite(None, None)
    assert resume.enabled is True


def test_enable_toggle_disables_and_swaps_icon(app, runtime):
    assert app._enabled is True

    app._on_toggle_enabled(None, None)
    assert app._enabled is False
    assert runtime.stops == 1
    assert app._icon.icon == "image:icon-disabled.ico"

    app._on_toggle_enabled(None, None)
    assert app._enabled is True
    assert runtime.starts == 1
    assert app._icon.icon == "image:icon.ico"


def test_pause_15m_disables_runtime_and_arms_timer(app, runtime, timers):
    app._on_pause_15m(None, None)

    assert app._enabled is False
    assert runtime.stops == 1
    assert app._paused_until_ms is not None
    assert app._paused_until_ms != tray.PAUSE_INDEFINITE
    assert app._icon.icon == "image:icon-disabled.ico"
    assert len(timers) == 1
    timer = timers[0]
    assert timer.seconds == 15 * 60
    assert timer.started is True
    assert timer.daemon is True


def test_pause_1h_uses_correct_duration(app, timers):
    app._on_pause_1h(None, None)
    assert timers[-1].seconds == 60 * 60


def test_pause_indefinite_does_not_arm_timer(app, timers):
    app._on_pause_indefinite(None, None)
    assert app._paused_until_ms == tray.PAUSE_INDEFINITE
    assert timers == []
    assert app._enabled is False


def test_pause_cancels_prior_timer(app, timers):
    app._on_pause_15m(None, None)
    first = timers[-1]
    app._on_pause_1h(None, None)
    assert first.cancelled is True
    assert len(timers) == 2


def test_auto_resume_restores_enabled_state(app, runtime, timers):
    app._on_pause_15m(None, None)
    assert app._enabled is False

    timers[-1].fire()

    assert app._enabled is True
    assert runtime.starts == 1
    assert app._paused_until_ms is None
    assert app._icon.icon == "image:icon.ico"


def test_resume_now_cancels_timer_and_re_enables(app, runtime, timers):
    app._on_pause_1h(None, None)
    timer = timers[-1]

    app._on_resume_now(None, None)

    assert timer.cancelled is True
    assert app._paused_until_ms is None
    assert app._enabled is True
    assert runtime.starts == 1


def test_toggle_while_paused_cancels_timer(app, timers):
    app._on_pause_1h(None, None)
    timer = timers[-1]
    app._on_toggle_enabled(None, None)
    assert timer.cancelled is True
    assert app._paused_until_ms is None
    assert app._enabled is True


def test_about_invokes_message_box_with_brand_first(app, message_box_calls):
    app._on_about(None, None)

    assert len(message_box_calls.calls) == 1
    hwnd, body, title, flags = message_box_calls.calls[0]
    assert hwnd == 0
    assert title == "About ClipWarden"
    # MB_OK | MB_ICONINFORMATION.
    assert flags == 0x00000040
    lines = body.splitlines()
    assert lines[0] == "ClipWarden 1.0.0"
    assert "Windows clipboard hijacking monitor" in body
    assert "Copyright (c) 2026 Ethan Tharp" in body
    assert "Released under the MIT License" in body
    assert "https://ethantharp.dev" in body


@pytest.mark.parametrize(
    ("action", "expected_path_attr"),
    [
        ("_on_open_config", "config"),
        ("_on_open_log_folder", "_log_parent"),
        ("_on_open_history_folder", "_log_parent"),
    ],
)
def test_folder_items_call_open_path(app, paths, open_path_calls, action, expected_path_attr):
    if expected_path_attr == "_log_parent":
        expected = str(paths.log.parent)
    else:
        expected = str(getattr(paths, expected_path_attr))
    getattr(app, action)(None, None)
    assert open_path_calls.calls == [expected]


def test_quit_stops_runtime_and_icon(app, runtime):
    app._on_quit(None, None)

    assert runtime.stops >= 1
    assert app._icon.stopped is True
    assert app._enabled is False


def test_quit_cancels_pending_pause_timer(app, timers):
    app._on_pause_1h(None, None)
    timer = timers[-1]
    app._on_quit(None, None)
    assert timer.cancelled is True
