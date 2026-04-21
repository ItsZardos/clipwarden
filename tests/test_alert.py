"""Alert system tests.

Exercises :mod:`clipwarden.alert` end-to-end without touching the
real Tk runtime or the real Windows notification stack. All I/O is
mocked through injection: the popup channel takes a ``tk_factory``,
a ``play_sound``, and a ``thread_factory``; the toast channel takes
an arbitrary notifier-shaped object.

The security-critical invariant pinned here: **a single channel
raising must not stop the dispatcher from calling remaining
channels.** A dispatcher that short-circuits on the first exception
would silently drop every alert after the one that happens to fail
first, which for a security tool is the worst failure mode.
"""

from __future__ import annotations

from typing import Any

import pytest

from clipwarden import alert as alert_mod
from clipwarden.alert import (
    AlertDispatcher,
    AlertEvent,
    PopupChannel,
    SoundChannel,
    ToastChannel,
    TrayFlashChannel,
    build_dispatcher_for_headless,
    build_dispatcher_for_tray,
    redact,
)
from clipwarden.config import AlertConfig
from clipwarden.detector import DetectionEvent


def _sample_event() -> AlertEvent:
    return AlertEvent(
        ts_ms=100,
        chain="BTC",
        before="bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4",
        after="BC1QW508D6QEJXTDG4Y5R3ZARVARY0C5XW7KV8F3T4",
        elapsed_ms=250,
    )


class _RecordingChannel:
    def __init__(self, *, raise_on_fire: BaseException | None = None) -> None:
        self.calls: list[AlertEvent] = []
        self._raise = raise_on_fire

    def fire(self, event: AlertEvent) -> None:
        self.calls.append(event)
        if self._raise is not None:
            raise self._raise


class _SyncThread:
    def __init__(
        self,
        *,
        target: Any,
        args: tuple = (),
        name: str | None = None,
        daemon: bool | None = None,
    ) -> None:
        self.target = target
        self.args = args
        self.name = name
        self.daemon = daemon
        self.started = False

    def start(self) -> None:
        self.started = True
        self.target(*self.args)


class _RecordingTkPopup:
    """Test double for ``_TkPopup`` that just captures arguments."""

    instances: list[_RecordingTkPopup] = []

    def __init__(self) -> None:
        self.built_with: dict | None = None
        self.ran = False
        _RecordingTkPopup.instances.append(self)

    def build(self, **kwargs) -> None:
        self.built_with = kwargs

    def run(self) -> None:
        self.ran = True


@pytest.fixture(autouse=True)
def _reset_tk_recorder():
    _RecordingTkPopup.instances.clear()
    yield
    _RecordingTkPopup.instances.clear()


class TestAlertEvent:
    def test_from_detection_copies_fields(self) -> None:
        d = DetectionEvent(
            ts_ms=42,
            chain="ETH",
            before="0xaaa",
            after="0xbbb",
            elapsed_ms=100,
            whitelisted=False,
        )
        event = AlertEvent.from_detection(d)
        assert event.ts_ms == 42
        assert event.chain == "ETH"
        assert event.before == "0xaaa"
        assert event.after == "0xbbb"
        assert event.elapsed_ms == 100
        # whitelisted is intentionally NOT on AlertEvent; a detection
        # that reaches the dispatcher is by construction non-whitelisted.
        assert not hasattr(event, "whitelisted")


class TestRedact:
    def test_long_address_is_redacted(self) -> None:
        assert redact("bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4") == "bc1qw5\u2026v8f3t4"

    def test_short_address_is_returned_unchanged(self) -> None:
        assert redact("abcd") == "abcd"

    def test_custom_head_tail(self) -> None:
        assert redact("0xabcdef1234567890", head=4, tail=4) == "0xab\u20267890"


class TestAlertDispatcher:
    def test_dispatch_fires_every_channel_in_order(self) -> None:
        a = _RecordingChannel()
        b = _RecordingChannel()
        c = _RecordingChannel()
        d = AlertDispatcher([a, b, c])
        event = _sample_event()

        d.dispatch(event)

        assert a.calls == [event]
        assert b.calls == [event]
        assert c.calls == [event]

    def test_one_channel_raising_does_not_stop_others(self) -> None:
        first = _RecordingChannel()
        broken = _RecordingChannel(raise_on_fire=RuntimeError("boom"))
        last = _RecordingChannel()
        d = AlertDispatcher([first, broken, last])
        event = _sample_event()

        d.dispatch(event)

        assert len(first.calls) == 1
        assert len(broken.calls) == 1
        assert len(last.calls) == 1

    def test_add_appends_channel(self) -> None:
        a = _RecordingChannel()
        d = AlertDispatcher()
        d.add(a)
        assert d.channels == (a,)

    def test_channels_property_is_a_snapshot(self) -> None:
        a = _RecordingChannel()
        d = AlertDispatcher([a])
        snap = d.channels
        d.add(_RecordingChannel())
        assert snap == (a,)

    def test_empty_dispatcher_is_safe(self) -> None:
        AlertDispatcher().dispatch(_sample_event())

    def test_dispatch_emits_trace_log_per_channel(self, caplog) -> None:
        # The diagnostic log is the only window we have into a
        # silently-failing channel in the packaged --noconsole build.
        # Pin the contract that every dispatch produces breadcrumbs
        # identifying exactly which channels ran and which raised.
        import logging  # noqa: PLC0415

        a = _RecordingChannel()
        broken = _RecordingChannel(raise_on_fire=RuntimeError("nope"))
        d = AlertDispatcher([a, broken])

        with caplog.at_level(logging.INFO, logger="clipwarden.alert"):
            d.dispatch(_sample_event())

        messages = [rec.message for rec in caplog.records]
        # One "dispatch" line with the channel list.
        assert any("dispatch" in m and "_RecordingChannel" in m for m in messages)
        # One "fired ok" for the healthy channel.
        assert any("fired ok" in m for m in messages)
        # One "raised" for the broken channel.
        assert any("raised" in m for m in messages)


class TestPopupChannel:
    def test_fire_spawns_daemon_thread_with_correct_name(self, monkeypatch) -> None:
        monkeypatch.setattr(alert_mod, "_default_tk_factory", _RecordingTkPopup)
        spawned: list[_SyncThread] = []

        def factory(**kwargs) -> _SyncThread:
            t = _SyncThread(**kwargs)
            spawned.append(t)
            return t

        ch = PopupChannel(
            thread_factory=factory,
            tk_factory=_RecordingTkPopup,
        )

        ch.fire(_sample_event())

        assert len(spawned) == 1
        thread = spawned[0]
        assert thread.daemon is True
        assert thread.name == "clipwarden-alert-popup"
        assert thread.started is True

    def test_popup_builds_with_event_data(self) -> None:
        ch = PopupChannel(
            thread_factory=_SyncThread,
            tk_factory=_RecordingTkPopup,
        )
        event = _sample_event()

        ch.fire(event)

        assert len(_RecordingTkPopup.instances) == 1
        popup = _RecordingTkPopup.instances[0]
        assert popup.ran is True
        args = popup.built_with
        assert args is not None
        assert args["chain"] == "BTC"
        assert args["before"] == event.before
        assert args["after"] == event.after
        assert args["elapsed_ms"] == 250
        assert "ClipWarden" in args["title"]
        assert "hijacking" in args["warning"].lower()
        assert args["button_label"] == "Got it"

    def test_popup_exception_does_not_escape(self) -> None:
        class _ExplodingPopup:
            def build(self, **_kwargs) -> None:
                raise RuntimeError("Tk is angry")

            def run(self) -> None:
                pass

        ch = PopupChannel(
            thread_factory=_SyncThread,
            tk_factory=_ExplodingPopup,
        )
        ch.fire(_sample_event())


class TestSoundChannel:
    def test_fire_spawns_daemon_thread_with_correct_name(self) -> None:
        spawned: list[_SyncThread] = []

        def factory(**kwargs) -> _SyncThread:
            t = _SyncThread(**kwargs)
            spawned.append(t)
            return t

        ch = SoundChannel(
            thread_factory=factory,
            play_sound=lambda: None,
        )

        ch.fire(_sample_event())

        assert len(spawned) == 1
        thread = spawned[0]
        assert thread.daemon is True
        assert thread.name == "clipwarden-alert-sound"
        assert thread.started is True

    def test_play_sound_is_invoked(self) -> None:
        calls: list[None] = []
        ch = SoundChannel(
            thread_factory=_SyncThread,
            play_sound=lambda: calls.append(None),
        )
        ch.fire(_sample_event())
        assert len(calls) == 1

    def test_play_sound_exception_does_not_escape(self) -> None:
        def boom() -> None:
            raise RuntimeError("no audio device")

        ch = SoundChannel(
            thread_factory=_SyncThread,
            play_sound=boom,
        )
        ch.fire(_sample_event())


class TestToastChannel:
    def test_fires_notifier_with_detection_shaped_object(self) -> None:
        seen: list[Any] = []

        class _FakeNotifier:
            def notify_substitution(self, ev: Any) -> None:
                seen.append(ev)

        ch = ToastChannel(_FakeNotifier())
        event = _sample_event()
        ch.fire(event)

        assert len(seen) == 1
        shim = seen[0]
        assert shim.chain == event.chain
        assert shim.before == event.before
        assert shim.after == event.after
        assert shim.elapsed_ms == event.elapsed_ms
        assert shim.ts_ms == event.ts_ms


class TestTrayFlashChannel:
    def test_unbound_channel_is_noop(self) -> None:
        ch = TrayFlashChannel()
        # Must not raise; the dispatcher itself tolerates exceptions,
        # but an unbound flash is a normal transient state (tray not
        # yet built) and should be silent.
        ch.fire(_sample_event())

    def test_bound_channel_calls_flash_with_seconds(self) -> None:
        calls: list[float] = []
        ch = TrayFlashChannel()
        ch.bind(calls.append)
        ch.fire(_sample_event())
        assert len(calls) == 1
        # Five-second flash is the documented contract.
        assert calls[0] == pytest.approx(5.0)

    def test_construct_with_flash_callable(self) -> None:
        calls: list[float] = []
        ch = TrayFlashChannel(flash=calls.append)
        ch.fire(_sample_event())
        assert len(calls) == 1


class _StubNotifier:
    def notify_substitution(self, _event: Any) -> None:  # pragma: no cover - stub
        pass


class TestBuildDispatcherForTray:
    def test_all_channels_on_by_default(self) -> None:
        cfg = AlertConfig()
        flash = TrayFlashChannel()
        d = build_dispatcher_for_tray(
            alert_cfg=cfg,
            notifier=_StubNotifier(),
            tray_flash_channel=flash,
        )
        types = [type(ch).__name__ for ch in d.channels]
        assert types == [
            "PopupChannel",
            "SoundChannel",
            "ToastChannel",
            "TrayFlashChannel",
        ]

    def test_disabled_channels_are_omitted(self) -> None:
        cfg = AlertConfig(popup=False, toast=True, sound=False, tray_flash=False)
        flash = TrayFlashChannel()
        d = build_dispatcher_for_tray(
            alert_cfg=cfg,
            notifier=_StubNotifier(),
            tray_flash_channel=flash,
        )
        types = [type(ch).__name__ for ch in d.channels]
        assert types == ["ToastChannel"]

    def test_sound_channel_is_independent_of_popup(self) -> None:
        # Finding 3: SoundChannel must fire even when popup is off.
        cfg = AlertConfig(popup=False, sound=True, toast=False, tray_flash=False)
        d = build_dispatcher_for_tray(
            alert_cfg=cfg,
            notifier=_StubNotifier(),
            tray_flash_channel=None,
        )
        types = [type(ch).__name__ for ch in d.channels]
        assert types == ["SoundChannel"]

    def test_tray_flash_skipped_without_channel(self) -> None:
        cfg = AlertConfig()
        d = build_dispatcher_for_tray(
            alert_cfg=cfg,
            notifier=_StubNotifier(),
            tray_flash_channel=None,
        )
        types = [type(ch).__name__ for ch in d.channels]
        assert "TrayFlashChannel" not in types


class TestBuildDispatcherForHeadless:
    def test_no_popup_or_tray_flash_in_headless(self) -> None:
        cfg = AlertConfig()
        d = build_dispatcher_for_headless(alert_cfg=cfg, notifier=_StubNotifier())
        types = [type(ch).__name__ for ch in d.channels]
        assert "PopupChannel" not in types
        assert "TrayFlashChannel" not in types

    def test_headless_default_rings_sound_and_toast(self) -> None:
        # Finding 3: headless users with sound on must still get an
        # audible cue alongside the toast.
        cfg = AlertConfig()
        d = build_dispatcher_for_headless(alert_cfg=cfg, notifier=_StubNotifier())
        types = [type(ch).__name__ for ch in d.channels]
        assert types == ["SoundChannel", "ToastChannel"]

    def test_toast_respects_config(self) -> None:
        off = AlertConfig(toast=False, sound=False)
        d = build_dispatcher_for_headless(alert_cfg=off, notifier=_StubNotifier())
        assert d.channels == ()

        on = AlertConfig(toast=True, sound=False)
        d = build_dispatcher_for_headless(alert_cfg=on, notifier=_StubNotifier())
        types = [type(ch).__name__ for ch in d.channels]
        assert types == ["ToastChannel"]
