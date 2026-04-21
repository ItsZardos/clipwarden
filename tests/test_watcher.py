"""Watcher tests.

Three layers are exercised:

1. Queue behaviour (``_enqueue`` plus worker drain) without starting
   the Win32 pump. Covers ordering, drop-oldest, and poison-pill
   shutdown.

2. The clipboard-update handler logic with stubbed ``win32clipboard``
   calls. Covers self-write sequence suppression and non-text paths.

3. A real start/stop lifecycle round-trip with the listener calls
   patched, so no real clipboard subscription is created. Covers
   shutdown timing.

Real ``WM_CLIPBOARDUPDATE`` delivery against a message-only window is
intentionally out of scope here; that path is covered by the manual
smoke harness.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pytest

from clipwarden import watcher as w
from clipwarden.watcher import (
    ClipboardEvent,
    Watcher,
    WatcherStartError,
    read_clipboard_text,
)

# --- Enqueue / worker behaviour -------------------------------------------


def _make_watcher(capacity: int = 4):
    received: list[ClipboardEvent] = []
    wx = Watcher(on_event=received.append, queue_max=capacity)
    return wx, received


def test_enqueue_preserves_order():
    wx, got = _make_watcher(capacity=4)
    worker = threading.Thread(target=wx._worker_run, daemon=True)
    worker.start()
    try:
        for i in range(3):
            wx._enqueue(ClipboardEvent(text=f"v{i}", ts_ms=i, seq=i + 1))
        time.sleep(0.05)
        assert [e.text for e in got] == ["v0", "v1", "v2"]
    finally:
        wx._queue.put_nowait(None)
        worker.join(timeout=1.0)


def test_enqueue_drops_oldest_when_full():
    wx, _ = _make_watcher(capacity=2)
    # Don't start a worker; let the queue saturate.
    wx._enqueue(ClipboardEvent(text="a", ts_ms=1, seq=1))
    wx._enqueue(ClipboardEvent(text="b", ts_ms=2, seq=2))
    wx._enqueue(ClipboardEvent(text="c", ts_ms=3, seq=3))
    # Newest kept, oldest dropped.
    items: list[ClipboardEvent] = []
    while not wx._queue.empty():
        ev = wx._queue.get_nowait()
        assert ev is not None
        items.append(ev)
    assert [e.text for e in items] == ["b", "c"]
    assert wx.dropped_count == 1


def test_worker_survives_callback_exception():
    errors = [RuntimeError("boom"), None, None]

    def flaky(ev: ClipboardEvent) -> None:
        err = errors.pop(0)
        if err is not None:
            raise err

    wx = Watcher(on_event=flaky, queue_max=8)
    worker = threading.Thread(target=wx._worker_run, daemon=True)
    worker.start()
    try:
        for i in range(3):
            wx._enqueue(ClipboardEvent(text=f"v{i}", ts_ms=i, seq=i))
        time.sleep(0.05)
    finally:
        wx._queue.put_nowait(None)
        worker.join(timeout=1.0)
    assert not worker.is_alive()


# --- Clipboard-update handler ---------------------------------------------


def _install_clipboard_stubs(monkeypatch, *, seq: int, text: str | None):
    """Stub the clipboard-reading APIs used by Watcher._on_clipboard_update."""
    monkeypatch.setattr(
        w.win32clipboard,
        "GetClipboardSequenceNumber",
        lambda: seq,
        raising=True,
    )
    monkeypatch.setattr(w, "read_clipboard_text", lambda: text, raising=True)


def test_clipboard_update_enqueues_event(monkeypatch):
    _install_clipboard_stubs(monkeypatch, seq=5, text="bc1qx...")

    wx, _ = _make_watcher()
    wx._on_clipboard_update()
    assert wx._queue.qsize() == 1
    ev = wx._queue.get_nowait()
    assert ev is not None
    assert ev.text == "bc1qx..."
    assert ev.seq == 5


def test_clipboard_update_skips_on_self_write(monkeypatch):
    _install_clipboard_stubs(monkeypatch, seq=42, text="whatever")
    wx, _ = _make_watcher()

    wx.mark_self_write(42)
    wx._on_clipboard_update()
    assert wx._queue.empty()
    # Suppression is one-shot: a follow-up event with a different seq
    # still flows through.
    assert wx.self_write_seq is None

    _install_clipboard_stubs(monkeypatch, seq=43, text="next")
    wx._on_clipboard_update()
    assert wx._queue.qsize() == 1


def test_clipboard_update_non_text_emits_none(monkeypatch):
    _install_clipboard_stubs(monkeypatch, seq=7, text=None)
    wx, _ = _make_watcher()
    wx._on_clipboard_update()
    ev = wx._queue.get_nowait()
    assert ev is not None
    assert ev.text is None
    assert ev.seq == 7


def test_mark_self_write_rejects_negative():
    wx, _ = _make_watcher()
    with pytest.raises(ValueError):
        wx.mark_self_write(-1)


# --- read_clipboard_text --------------------------------------------------


def test_read_clipboard_text_returns_none_on_open_failure(monkeypatch):
    # Make OpenClipboard always raise a pywintypes error.
    err = w.pywintypes.error(5, "OpenClipboard", "Access is denied")

    def boom(_hwnd):  # noqa: ANN001
        raise err

    monkeypatch.setattr(w.win32clipboard, "OpenClipboard", boom, raising=True)
    assert read_clipboard_text(retries=2, backoff_s=0.0) is None


def test_read_clipboard_text_returns_string(monkeypatch):
    calls = {"open": 0, "close": 0}

    def _open(_hwnd):  # noqa: ANN001
        calls["open"] += 1

    def _close():
        calls["close"] += 1

    monkeypatch.setattr(w.win32clipboard, "OpenClipboard", _open, raising=True)
    monkeypatch.setattr(w.win32clipboard, "CloseClipboard", _close, raising=True)
    monkeypatch.setattr(
        w.win32clipboard,
        "GetClipboardData",
        lambda _fmt: "0xdeadbeef",
        raising=True,
    )
    assert read_clipboard_text() == "0xdeadbeef"
    assert calls == {"open": 1, "close": 1}


def test_read_clipboard_text_returns_none_for_non_string(monkeypatch):
    monkeypatch.setattr(w.win32clipboard, "OpenClipboard", lambda _h: None, raising=True)
    monkeypatch.setattr(w.win32clipboard, "CloseClipboard", lambda: None, raising=True)
    monkeypatch.setattr(
        w.win32clipboard,
        "GetClipboardData",
        lambda _fmt: b"binary-payload",
        raising=True,
    )
    assert read_clipboard_text() is None


# --- Lifecycle: bounded shutdown ------------------------------------------


def test_start_stop_joins_within_timeout():
    """Start + stop must complete promptly even if no clipboard event ever fires.

    We patch the two ctypes listener calls so no real clipboard
    subscription is created; everything else (window creation, pump
    loop, event-handle teardown) runs for real.
    """
    wx, _ = _make_watcher()
    with (
        patch.object(w, "_add_clipboard_listener", lambda hwnd: None),
        patch.object(w, "_remove_clipboard_listener", lambda hwnd: None),
    ):
        wx.start()
        # Let the pump thread reach its wait loop at least once.
        time.sleep(0.05)
        t0 = time.monotonic()
        wx.stop(timeout=2.0)
        elapsed = time.monotonic() - t0
    assert elapsed < 1.5, f"stop() took too long: {elapsed:.2f}s"


def test_stop_is_idempotent():
    wx, _ = _make_watcher()
    with (
        patch.object(w, "_add_clipboard_listener", lambda hwnd: None),
        patch.object(w, "_remove_clipboard_listener", lambda hwnd: None),
    ):
        wx.start()
        wx.stop(timeout=2.0)
        wx.stop(timeout=2.0)  # second call must not raise


def test_start_is_idempotent():
    wx, _ = _make_watcher()
    with (
        patch.object(w, "_add_clipboard_listener", lambda hwnd: None),
        patch.object(w, "_remove_clipboard_listener", lambda hwnd: None),
    ):
        wx.start()
        wx.start()  # second start is a no-op
        wx.stop(timeout=2.0)


# --- Start-time handshake -------------------------------------------------


def test_start_blocks_until_listener_subscribes():
    """``start()`` returns only after the pump reaches the ready signal.

    Stalling the ctypes listener call until the main thread releases
    a gate lets us observe that ``start()`` is still waiting while
    the pump is mid-init, and that the event becomes observable only
    after the listener returns.
    """
    gate = threading.Event()

    def slow_add(_hwnd):
        gate.wait(timeout=2.0)

    wx, _ = _make_watcher()
    with (
        patch.object(w, "_add_clipboard_listener", slow_add),
        patch.object(w, "_remove_clipboard_listener", lambda _h: None),
    ):
        caller_done = threading.Event()

        def run_start():
            wx.start(timeout=2.0)
            caller_done.set()

        t = threading.Thread(target=run_start, daemon=True)
        t.start()
        # Give the pump a moment to enter slow_add; start() should
        # still be blocked on _ready.wait while the gate is closed.
        time.sleep(0.1)
        assert not caller_done.is_set(), "start() returned before listener subscribed"
        gate.set()
        t.join(timeout=2.0)
        assert caller_done.is_set()
        wx.stop(timeout=2.0)


def test_start_raises_when_listener_fails():
    """Listener registration failures surface as WatcherStartError.

    The pump's ``AddClipboardFormatListener`` raising should abort
    ``start()`` with a clean exception rather than returning a
    silently-broken watcher that never sees clipboard events.
    """

    def boom(_hwnd):
        raise OSError("simulated listener failure")

    wx, _ = _make_watcher()
    with (
        patch.object(w, "_add_clipboard_listener", boom),
        patch.object(w, "_remove_clipboard_listener", lambda _h: None),
        pytest.raises(WatcherStartError),
    ):
        wx.start(timeout=1.0)
    # Teardown must leave the watcher in a non-running state so a
    # fresh Watcher created by the caller's retry path starts clean.
    assert wx._running is False
    assert wx._pump_thread is None
    assert wx._worker_thread is None


def test_start_raises_on_handshake_timeout():
    """If the pump never reaches ready, start() times out and raises.

    Holding the listener indefinitely models a wedged Win32 call. The
    caller must see a WatcherStartError rather than blocking forever
    or silently proceeding.
    """
    stuck = threading.Event()  # never set

    def never_return(_hwnd):
        stuck.wait(timeout=10.0)

    wx, _ = _make_watcher()
    with (
        patch.object(w, "_add_clipboard_listener", never_return),
        patch.object(w, "_remove_clipboard_listener", lambda _h: None),
        pytest.raises(WatcherStartError),
    ):
        wx.start(timeout=0.2)
    stuck.set()
