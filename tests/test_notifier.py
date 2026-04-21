"""Notifier tests.

The underlying ``winotify`` library is stubbed because it shells out to
Windows toast COM infrastructure that we don't want active in CI.
"""

from __future__ import annotations

import pytest

from clipwarden import notifier as n
from clipwarden.detector import DetectionEvent
from clipwarden.notifier import Notifier, _redact_address


class _FakeToast:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.shown = False

    def show(self):
        self.shown = True


@pytest.fixture
def capture_toasts(monkeypatch):
    """Replace ``winotify.Notification`` with a recording fake."""
    captured: list[_FakeToast] = []

    def factory(**kwargs):
        t = _FakeToast(**kwargs)
        captured.append(t)
        return t

    monkeypatch.setattr(n, "Notification", factory, raising=True)
    return captured


def _make_event(*, whitelisted: bool = False) -> DetectionEvent:
    return DetectionEvent(
        ts_ms=1_700_000_000_000,
        chain="BTC",
        before="bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4",
        after="bc1qrp33g0q5c5txsp9arysrx4k6zdkfs4nce4xj0gdcccefvpysxf3qccfmv3",
        elapsed_ms=420,
        whitelisted=whitelisted,
    )


def test_notify_substitution_fires_toast(capture_toasts):
    notifier = Notifier(enabled=True)
    notifier.notify_substitution(_make_event())
    assert len(capture_toasts) == 1
    t = capture_toasts[0]
    assert t.shown
    assert t.kwargs["app_id"] == "ClipWarden"
    assert "hijack" in t.kwargs["title"].lower()
    assert "BTC" in t.kwargs["msg"]
    assert "420" in t.kwargs["msg"]


def test_notify_substitution_redacts_addresses(capture_toasts):
    notifier = Notifier(enabled=True)
    ev = _make_event()
    notifier.notify_substitution(ev)
    msg = capture_toasts[0].kwargs["msg"]
    # Full addresses should not appear; redacted head/tail should.
    assert ev.before not in msg
    assert ev.after not in msg
    assert ev.before[:6] in msg
    assert ev.before[-4:] in msg


def test_disabled_notifier_is_a_noop(capture_toasts):
    notifier = Notifier(enabled=False)
    notifier.notify_substitution(_make_event())
    notifier.notify_info("title", "body")
    assert capture_toasts == []


def test_notify_info_fires_toast(capture_toasts):
    notifier = Notifier(enabled=True)
    notifier.notify_info("Hello", "World")
    assert len(capture_toasts) == 1
    assert capture_toasts[0].kwargs["title"] == "Hello"
    assert capture_toasts[0].kwargs["msg"] == "World"


def test_notifier_swallows_winotify_exceptions(monkeypatch):
    def boom(**_kwargs):
        raise RuntimeError("COM not initialised")

    monkeypatch.setattr(n, "Notification", boom, raising=True)
    notifier = Notifier(enabled=True)
    # Must not raise - toasts are best-effort.
    notifier.notify_substitution(_make_event())


def test_redact_address_short_passes_through():
    assert _redact_address("short") == "short"


def test_redact_address_long_is_masked():
    addr = "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"
    out = _redact_address(addr)
    assert out.startswith("bc1qw5")
    assert out.endswith("f3t4")
    assert "\u2026" in out
    assert len(out) < len(addr)
