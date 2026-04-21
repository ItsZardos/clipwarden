"""Windows clipboard watcher.

The watcher isolates all Win32 clipboard plumbing from the rest of the
application. It runs two threads:

* A pump thread that owns a message-only window (``HWND_MESSAGE``)
  subscribed via ``AddClipboardFormatListener`` and drives a short
  ``MsgWaitForMultipleObjects`` loop. The short wait slice keeps
  :meth:`Watcher.stop` responsive; ``PumpMessages`` would otherwise
  block in ``GetMessage`` until the next clipboard change.

* A worker thread that drains an internal bounded queue and invokes
  the caller's ``on_event`` callback. Separating the worker from the
  pump matters because ``WM_CLIPBOARDUPDATE`` is delivered to the
  thread that owns the window and the detector is documented as
  single-threaded (see :class:`clipwarden.detector.Detector`); having
  exactly one consumer preserves that contract downstream.

Self-write suppression
----------------------
The listener fires once per clipboard change and every change advances
``GetClipboardSequenceNumber``. That yields a race-free O(1) way for
first-party writes (a future "Restore previous address" action, for
example) to avoid tripping the detector. After writing, call
:meth:`Watcher.mark_self_write` with the sequence number the write
produced; the next ``WM_CLIPBOARDUPDATE`` carrying that number is
dropped on the pump thread before it reaches the queue. The token is
guarded by an internal lock so ``mark_self_write`` is safe to call
from any thread.

The current release never writes to the clipboard. The suppression
hook is part of the watcher's public contract and is documented here
so the pattern is clear when the hook is exercised.

Clipboard reads are deliberately minimal. If another process holds the
clipboard open, we retry three times with a 10 ms back-off and then
drop that event; the next change will arrive with a fresh sequence
number. Non-text payloads (images, CF_HDROP, anything other than
CF_UNICODETEXT) surface as ``text=None`` rather than being hidden,
because the detector uses "non-address text between two addresses" as
a laundering signal.
"""

from __future__ import annotations

import contextlib
import ctypes
import logging
import queue
import threading
import time
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass

import pywintypes
import win32api
import win32clipboard
import win32con
import win32event
import win32gui

log = logging.getLogger(__name__)

# pywin32 311 does not expose these symbols via win32con. Defining them
# locally keeps the module self-contained.
WM_CLIPBOARDUPDATE = 0x031D
HWND_MESSAGE = -3

# Dedicated wake message in the WM_APP user range, used to unblock the
# pump during shutdown without colliding with foreign messages.
WM_WAKE = win32con.WM_APP + 1

_DEFAULT_QUEUE_MAX = 256
_CLIPBOARD_OPEN_RETRIES = 3
_CLIPBOARD_OPEN_BACKOFF_S = 0.010
_PUMP_TIMEOUT_MS = 100
_DEFAULT_START_TIMEOUT_S = 5.0


ClipboardEventCallback = Callable[["ClipboardEvent"], None]


class WatcherStartError(RuntimeError):
    """Raised when the watcher fails to come up within the start timeout.

    Either the pump thread could not create its message-only window,
    ``AddClipboardFormatListener`` failed, or the pump never reached
    the signal point before the caller's deadline. Callers surface
    this to the user (MessageBox plus ``crash.log``) rather than
    silently continuing with a runtime that never sees any clipboard
    events.
    """


@dataclass(frozen=True)
class ClipboardEvent:
    """One clipboard state snapshot, timestamped in the monotonic frame.

    ``text`` is ``None`` for non-Unicode-text payloads. The worker
    still receives those events so the detector observes every change.
    """

    text: str | None
    ts_ms: int
    seq: int


def monotonic_ms() -> int:
    """Absolute monotonic timestamp in the frame the detector expects."""
    return time.monotonic_ns() // 1_000_000


_user32 = ctypes.WinDLL("user32", use_last_error=True)

_AddClipboardFormatListener = _user32.AddClipboardFormatListener
_AddClipboardFormatListener.argtypes = [wintypes.HWND]
_AddClipboardFormatListener.restype = wintypes.BOOL

_RemoveClipboardFormatListener = _user32.RemoveClipboardFormatListener
_RemoveClipboardFormatListener.argtypes = [wintypes.HWND]
_RemoveClipboardFormatListener.restype = wintypes.BOOL


def _add_clipboard_listener(hwnd: int) -> None:
    if not _AddClipboardFormatListener(wintypes.HWND(hwnd)):
        raise ctypes.WinError(ctypes.get_last_error())


def _remove_clipboard_listener(hwnd: int) -> None:
    if not _RemoveClipboardFormatListener(wintypes.HWND(hwnd)):
        log.debug("RemoveClipboardFormatListener failed: %s", ctypes.get_last_error())


def read_clipboard_text(
    *,
    retries: int = _CLIPBOARD_OPEN_RETRIES,
    backoff_s: float = _CLIPBOARD_OPEN_BACKOFF_S,
) -> str | None:
    """Read the clipboard's Unicode text or return ``None``.

    ``None`` covers three cases the watcher treats identically: the
    clipboard held a non-text payload, another process kept it open
    past the retry budget, or a transient Win32 failure. The sequence
    number still advances in each case, so the detector's laundering
    state machinery still applies.
    """
    opened = False
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            win32clipboard.OpenClipboard(0)
            opened = True
            break
        except pywintypes.error as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(backoff_s)
    if not opened:
        log.debug("OpenClipboard failed after %d retries: %s", retries, last_err)
        return None
    try:
        try:
            data = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
        except (pywintypes.error, TypeError):
            return None
        if not isinstance(data, str):
            return None
        return data
    finally:
        with contextlib.suppress(pywintypes.error):
            win32clipboard.CloseClipboard()


class Watcher:
    """Clipboard watcher with start/stop lifecycle.

    Thread ownership:
        * ``start`` and ``stop`` are called from the main thread.
        * The pump thread owns the HWND and the listener registration;
          Windows ties the HWND to its creating thread.
        * The worker thread owns all downstream state (detector,
          logger, notifier). A single consumer keeps the detector's
          single-threaded contract intact without locks.
    """

    def __init__(
        self,
        on_event: ClipboardEventCallback,
        *,
        queue_max: int = _DEFAULT_QUEUE_MAX,
    ) -> None:
        self._on_event = on_event
        self._queue: queue.Queue[ClipboardEvent | None] = queue.Queue(maxsize=queue_max)
        self._pump_thread: threading.Thread | None = None
        self._worker_thread: threading.Thread | None = None
        self._pump_tid: int = 0
        self._stop_handle = win32event.CreateEvent(None, True, False, None)
        # Protects ``_self_write_seq`` so ``mark_self_write`` can be
        # called from any thread without racing the pump's consume.
        self._lock = threading.Lock()
        self._self_write_seq: int | None = None
        self._running = False
        self._dropped_count = 0
        # Start-time handshake: the pump thread signals ``_ready`` once
        # its message window exists and the clipboard listener is
        # subscribed. ``start()`` blocks on this so a caller that gets
        # a non-exceptional return is guaranteed to be receiving
        # clipboard updates. ``_start_error`` carries the pump's
        # initialisation exception back across the thread boundary.
        self._ready = threading.Event()
        self._start_error: BaseException | None = None
        # Sticky "previous stop() could not join" flag. Set when one
        # of the background threads outlived its join timeout so a
        # subsequent start() cannot spawn a second pair that would
        # compete with the first for the HWND and event queue.
        self._stopping = False
        # Win32 requires a strong reference to the WndProc callable for
        # the lifetime of the window; without it the closure is GC'd
        # and the next dispatched message causes an access violation.
        self._wnd_proc_ref: Callable[..., int] | None = None
        self._hwnd: int = 0
        self._class_atom: int = 0
        self._class_name: str = ""

    @property
    def dropped_count(self) -> int:
        """Events dropped because the worker queue was saturated."""
        return self._dropped_count

    @property
    def self_write_seq(self) -> int | None:
        with self._lock:
            return self._self_write_seq

    def mark_self_write(self, next_seq: int) -> None:
        """Arm self-write suppression for the given clipboard sequence.

        Call this immediately after writing to the clipboard from
        first-party code, passing the value that
        ``GetClipboardSequenceNumber()`` will return for that write.
        The next ``WM_CLIPBOARDUPDATE`` carrying that sequence number
        is dropped before it reaches the worker queue. Suppression is
        one-shot; the token is consumed on match.
        """
        if next_seq < 0:
            raise ValueError("sequence numbers are non-negative")
        with self._lock:
            self._self_write_seq = next_seq

    def start(self, *, timeout: float = _DEFAULT_START_TIMEOUT_S) -> None:
        """Start the pump and worker threads, blocking until ready.

        Returns only after the pump thread has created its
        message-only window and subscribed to clipboard updates. If
        setup fails or the handshake does not arrive within
        ``timeout`` seconds, raises :class:`WatcherStartError` after
        best-effort teardown so the caller sees a clean failure
        instead of a silently-broken runtime.
        """
        if self._running:
            return
        if self._stopping:
            # A previous stop() could not join one of the background
            # threads. Spawning a fresh pump/worker pair now would
            # race the stranded pair for the clipboard listener slot
            # and the event queue, so refuse the start and let the
            # caller surface the failure (typically via crash.log and
            # a MessageBox) instead.
            raise WatcherStartError(
                "Clipboard watcher is still stopping from a previous session; "
                "restart ClipWarden to recover"
            )
        if self._stop_handle is None:
            # A prior clean stop freed the event handle; reallocate so
            # this start has a fresh signal to the pump loop.
            self._stop_handle = win32event.CreateEvent(None, True, False, None)
        self._ready.clear()
        self._start_error = None
        self._running = True
        win32event.ResetEvent(self._stop_handle)
        self._worker_thread = threading.Thread(
            target=self._worker_run,
            name="clipwarden-worker",
            daemon=True,
        )
        self._worker_thread.start()
        self._pump_thread = threading.Thread(
            target=self._pump_run,
            name="clipwarden-pump",
            daemon=True,
        )
        self._pump_thread.start()
        if not self._ready.wait(timeout=timeout):
            self._abort_failed_start(
                WatcherStartError(f"Clipboard watcher did not become ready within {timeout:.1f}s")
            )
        if self._start_error is not None:
            err = self._start_error
            self._abort_failed_start(err)

    def _abort_failed_start(self, err: BaseException) -> None:
        # Tear down the half-started pump and worker so the caller's
        # retry on a fresh Watcher starts from a clean baseline.
        self._running = False
        if self._stop_handle is not None:
            win32event.SetEvent(self._stop_handle)
        if self._pump_tid:
            with contextlib.suppress(pywintypes.error):
                win32gui.PostThreadMessage(self._pump_tid, WM_WAKE, 0, 0)
        # Join the pump first so it cannot enqueue another event
        # behind the poison pill, then drain the worker.
        if self._pump_thread is not None:
            self._pump_thread.join(timeout=1.0)
        with contextlib.suppress(Exception):
            self._queue.put_nowait(None)
        if self._worker_thread is not None:
            self._worker_thread.join(timeout=1.0)
        pump_alive = self._pump_thread is not None and self._pump_thread.is_alive()
        worker_alive = self._worker_thread is not None and self._worker_thread.is_alive()
        self._pump_thread = None
        self._worker_thread = None
        self._pump_tid = 0
        self._wnd_proc_ref = None
        if not (pump_alive or worker_alive):
            self._release_stop_handle()
        if isinstance(err, WatcherStartError):
            raise err
        raise WatcherStartError(str(err) or type(err).__name__) from err

    def stop(self, timeout: float = 2.0) -> None:
        """Stop both threads with a bounded join.

        Signals the pump, posts a wake message to unblock the message
        wait, joins the pump, then drains the worker with a poison
        pill. Each join has its own timeout so a wedge in one thread
        does not stall the other's cleanup.
        """
        if not self._running:
            return
        self._running = False
        if self._stop_handle is not None:
            win32event.SetEvent(self._stop_handle)
        if self._pump_tid:
            with contextlib.suppress(pywintypes.error):
                win32gui.PostThreadMessage(self._pump_tid, WM_WAKE, 0, 0)
        if self._pump_thread is not None:
            self._pump_thread.join(timeout=timeout)
        # Poison-pill the worker only after the pump has exited; a
        # late enqueue could otherwise slip in behind the pill.
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            with contextlib.suppress(queue.Empty):
                self._queue.get_nowait()
            self._queue.put_nowait(None)
        if self._worker_thread is not None:
            self._worker_thread.join(timeout=timeout)
        pump_alive = self._pump_thread is not None and self._pump_thread.is_alive()
        worker_alive = self._worker_thread is not None and self._worker_thread.is_alive()
        if pump_alive or worker_alive:
            # Keep the thread refs so a future debugging session or
            # a graceful process exit can still reason about them.
            # Mark the watcher as permanently stopping; start() will
            # refuse, which is strictly safer than overlapping a
            # new listener registration with a stale one. The pump
            # may still own _stop_handle so leave it allocated; the
            # OS reclaims it on process exit.
            log.warning(
                "Watcher stop timed out (pump_alive=%s worker_alive=%s); "
                "refusing further start() calls on this instance",
                pump_alive,
                worker_alive,
            )
            self._stopping = True
            return
        self._pump_thread = None
        self._worker_thread = None
        self._pump_tid = 0
        self._wnd_proc_ref = None
        self._release_stop_handle()

    def _release_stop_handle(self) -> None:
        """Close the stop event handle if it is still open.

        Safe to call more than once. The handle is reallocated on the
        next ``start()`` so a watcher instance can be restarted after
        a clean stop.
        """
        if self._stop_handle is None:
            return
        handle, self._stop_handle = self._stop_handle, None
        with contextlib.suppress(pywintypes.error, OSError):
            win32api.CloseHandle(handle)

    def __del__(self) -> None:
        # Defensive: if the caller forgot to stop() this instance, at
        # least release the Win32 event handle instead of leaking it
        # until process exit.
        with contextlib.suppress(Exception):
            self._release_stop_handle()

    def _pump_run(self) -> None:
        self._pump_tid = win32api.GetCurrentThreadId()
        try:
            self._create_window()
            _add_clipboard_listener(self._hwnd)
        except BaseException as exc:  # noqa: BLE001
            log.exception("Watcher pump failed to initialise")
            self._start_error = exc
            self._teardown_window()
            # Signal after recording the error so ``start()`` picks
            # up the failure rather than blocking on ``_ready.wait``
            # until the timeout.
            self._ready.set()
            return

        # Publish readiness only after both the window exists and the
        # listener is subscribed; a caller that sees start() return
        # normally is guaranteed to receive clipboard updates.
        self._ready.set()
        try:
            while self._running:
                rc = win32event.MsgWaitForMultipleObjects(
                    [self._stop_handle],
                    False,
                    _PUMP_TIMEOUT_MS,
                    win32event.QS_ALLINPUT,
                )
                if rc == win32event.WAIT_OBJECT_0:
                    break
                win32gui.PumpWaitingMessages()
        finally:
            try:
                _remove_clipboard_listener(self._hwnd)
            finally:
                self._teardown_window()

    def _create_window(self) -> None:
        # Class name is unique per instance and process to avoid
        # collisions with a prior crashed process that did not
        # unregister its class.
        self._class_name = f"ClipWardenWatcher-{win32api.GetCurrentProcessId()}-{id(self):x}"

        wc = win32gui.WNDCLASS()
        wc.lpszClassName = self._class_name
        wc.lpfnWndProc = self._build_wnd_proc()
        self._wnd_proc_ref = wc.lpfnWndProc
        self._class_atom = win32gui.RegisterClass(wc)

        self._hwnd = win32gui.CreateWindowEx(
            0,
            self._class_name,
            "ClipWarden message window",
            0,
            0,
            0,
            0,
            0,
            HWND_MESSAGE,
            0,
            0,
            None,
        )

    def _teardown_window(self) -> None:
        if self._hwnd:
            try:
                win32gui.DestroyWindow(self._hwnd)
            except pywintypes.error:
                log.debug("DestroyWindow failed")
            self._hwnd = 0
        if self._class_atom:
            try:
                win32gui.UnregisterClass(self._class_name, 0)
            except pywintypes.error:
                log.debug("UnregisterClass failed")
            self._class_atom = 0

    def _build_wnd_proc(self) -> Callable[[int, int, int, int], int]:
        def wnd_proc(hwnd: int, msg: int, wparam: int, lparam: int) -> int:
            if msg == WM_CLIPBOARDUPDATE:
                self._on_clipboard_update()
                return 0
            if msg == WM_WAKE:
                return 0
            if msg == win32con.WM_DESTROY:
                win32gui.PostQuitMessage(0)
                return 0
            return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

        return wnd_proc

    def _on_clipboard_update(self) -> None:
        # Capture the sequence number before reading the text. The
        # clipboard can change again mid-read; the race is bounded to
        # one tick and the next update will carry the correct content.
        try:
            seq = win32clipboard.GetClipboardSequenceNumber()
        except pywintypes.error:
            log.debug("GetClipboardSequenceNumber failed; treating as seq=0")
            seq = 0

        with self._lock:
            if self._self_write_seq is not None and seq == self._self_write_seq:
                self._self_write_seq = None
                return

        text = read_clipboard_text()
        ev = ClipboardEvent(text=text, ts_ms=monotonic_ms(), seq=seq)
        self._enqueue(ev)

    def _enqueue(self, ev: ClipboardEvent) -> None:
        try:
            self._queue.put_nowait(ev)
            return
        except queue.Full:
            pass
        # Drop-oldest: preserves the freshest clipboard state for the
        # detector once the worker recovers. Dropping the newest would
        # keep stale context live.
        with contextlib.suppress(queue.Empty):
            self._queue.get_nowait()
        with contextlib.suppress(queue.Full):
            self._queue.put_nowait(ev)
        self._dropped_count += 1

    def _worker_run(self) -> None:
        while True:
            ev = self._queue.get()
            if ev is None:
                return
            try:
                self._on_event(ev)
            except Exception:  # noqa: BLE001
                log.exception("Watcher callback raised; continuing")
