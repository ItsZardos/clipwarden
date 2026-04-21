"""Multi-channel alert system.

When the detector flags a clipboard substitution, *the user needs to
know now* -- the attacker's address is already in the clipboard and
the next paste is the one that sends money. Windows toast
notifications alone are the wrong primary channel for this: Focus
Assist, Do Not Disturb, fullscreen app exclusive mode, and the
notification queue all conspire to hide toasts exactly when a real
victim most needs to see one.

Architecture:

* :class:`AlertDispatcher` fans a :class:`AlertEvent` out to an
  ordered list of :class:`AlertChannel` instances. Each channel is
  called in sequence; an exception raised by one channel is logged
  and swallowed so the remaining channels still fire. Channel order
  is stable and matches the registration order.

* :class:`PopupChannel` is the primary channel. It opens a small
  always-on-top Tk window for each detection. The Tk mainloop runs
  on a dedicated per-alert daemon thread so it does not deadlock the
  pystray WM_COMMAND handler and so multiple stacked alerts cannot
  fight over a single Tk root. Bypasses Focus Assist because the OS
  treats it as a regular user window, not a shell notification.

* :class:`SoundChannel` plays a short system sound on a dedicated
  daemon thread. Independent of the popup channel so a headless
  build, or a user who has turned the popup off, still gets an
  audible cue. Independent of the tray flash for the same reason.

* :class:`ToastChannel` wraps the existing :class:`clipwarden.notifier.Notifier`
  so the v0 shell toast remains available as a secondary channel.
  Respects Focus Assist (by virtue of being a real shell toast),
  which is the correct behaviour for a secondary channel.

* :class:`TrayFlashChannel` swaps the tray icon to an alert variant
  for a few seconds. Wired in :mod:`clipwarden.__main__` when tray
  mode runs; in headless mode this channel is absent.

Each channel is constructed with a simple bool gate so the same
config that controls UI presentation also controls dispatcher
membership. Disabled channels are simply not added to the
dispatcher's list rather than being added and short-circuited: fewer
moving parts at detection time, and the tests can assert on
``len(dispatcher.channels)`` to pin intent.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from .detector import DetectionEvent

log = logging.getLogger(__name__)


# Hard-coded tunables. Kept module-private because they are UX
# decisions, not per-user preferences; flipping a 5-second flash to
# something wildly different is a design change, not a config change.
_POPUP_TITLE = "ClipWarden - Clipboard Substitution Detected"
_POPUP_BUTTON_LABEL = "Got it"
_POPUP_WARNING_TEXT = (
    "A clipboard hijacking attempt was blocked.\nVerify the correct address before pasting."
)
_POPUP_WIDTH_PX = 460
_POPUP_HEIGHT_PX = 260
_POPUP_THREAD_NAME = "clipwarden-alert-popup"
_SOUND_THREAD_NAME = "clipwarden-alert-sound"
_TRAY_FLASH_SECONDS = 5.0


@dataclass(frozen=True)
class AlertEvent:
    """What a detection looks like to the alert system.

    A thin mirror of :class:`DetectionEvent` with the whitelist bit
    stripped; the dispatcher is only invoked for non-whitelisted
    detections, so carrying the flag any further would be misleading.
    Kept as a separate type so future non-detection alerts (startup
    failure, autostart change, etc) can share the same plumbing
    without reusing a type named ``DetectionEvent``.
    """

    ts_ms: int
    chain: str
    before: str
    after: str
    elapsed_ms: int

    @classmethod
    def from_detection(cls, d: DetectionEvent) -> AlertEvent:
        return cls(
            ts_ms=d.ts_ms,
            chain=d.chain,
            before=d.before,
            after=d.after,
            elapsed_ms=d.elapsed_ms,
        )


class AlertChannel(Protocol):
    """Minimal surface the dispatcher talks to."""

    def fire(self, event: AlertEvent) -> None: ...


def redact(address: str, *, head: int = 6, tail: int = 6) -> str:
    """Return a ``head...tail`` rendering of ``address``.

    Defaults give ``bc1qw5...kv8f3t4``-style output. Strings shorter
    than the redacted form are returned unchanged. Public because the
    popup and the log formatter both want the same rendering.
    """
    if len(address) <= head + tail + 3:
        return address
    return f"{address[:head]}\u2026{address[-tail:]}"


class AlertDispatcher:
    """Ordered list of channels plus a fire-once fan-out method.

    The dispatcher owns nothing; all heavy state lives in the
    individual channels. That keeps the :class:`Runtime` wiring
    trivial: build the channels, build the dispatcher, pass it in.
    """

    def __init__(self, channels: list[AlertChannel] | None = None) -> None:
        self._channels: list[AlertChannel] = list(channels) if channels else []

    def add(self, channel: AlertChannel) -> None:
        self._channels.append(channel)

    @property
    def channels(self) -> tuple[AlertChannel, ...]:
        return tuple(self._channels)

    def dispatch(self, event: AlertEvent) -> None:
        """Fire every channel in registration order.

        A failing channel logs and is skipped; remaining channels
        still fire. The dispatcher never raises: a crash here would
        take down the worker thread, and a security tool that
        silently stops detecting because one channel bugged is a
        worse failure than a missed popup.

        Each call emits an INFO-level breadcrumb per channel so the
        diagnostic log shows the exact dispatch shape when a shipped
        build misbehaves. Cost is one log line per detection, which
        is fine given detections are rare events.
        """
        channel_names = [type(ch).__name__ for ch in self._channels]
        log.info(
            "dispatch chain=%s elapsed_ms=%d channels=%s",
            event.chain,
            event.elapsed_ms,
            channel_names,
        )
        for ch in self._channels:
            name = type(ch).__name__
            try:
                ch.fire(event)
                log.info("channel %s fired ok", name)
            except Exception:  # noqa: BLE001
                log.exception("alert channel %s raised", name)


class ToastChannel:
    """Fire a Windows shell toast via the existing :class:`Notifier`."""

    def __init__(self, notifier: Any) -> None:
        self._notifier = notifier

    def fire(self, event: AlertEvent) -> None:
        # Reconstitute the DetectionEvent-shaped object the notifier
        # expects. We use a dataclass-compatible shim rather than
        # importing DetectionEvent here to keep the alert module
        # dependency-light; the notifier only reads a small set of
        # attributes.
        self._notifier.notify_substitution(_ToastShim(event))


@dataclass(frozen=True)
class _ToastShim:
    """Duck-typed DetectionEvent view for the notifier."""

    _event: AlertEvent

    @property
    def chain(self) -> str:
        return self._event.chain

    @property
    def before(self) -> str:
        return self._event.before

    @property
    def after(self) -> str:
        return self._event.after

    @property
    def elapsed_ms(self) -> int:
        return self._event.elapsed_ms

    @property
    def ts_ms(self) -> int:
        return self._event.ts_ms


class SoundChannel:
    """Short audible beep, independent of any other channel.

    Ships as its own dispatcher channel so headless users, users who
    disable the popup, or users whose Tk is unavailable still get an
    audio cue. Plays on a dedicated daemon thread so a sound-card
    driver hang never blocks the worker thread that called
    :meth:`fire`. ``play_sound`` is injectable so the unit tests can
    assert gating without making the test machine actually beep.
    """

    def __init__(
        self,
        *,
        thread_factory: Callable[..., threading.Thread] = threading.Thread,
        play_sound: Callable[[], None] | None = None,
    ) -> None:
        self._thread_factory = thread_factory
        self._play_sound = play_sound

    def fire(self, _event: AlertEvent) -> None:
        log.info("SoundChannel.fire spawning thread")
        thread = self._thread_factory(
            target=self._run,
            args=(),
            name=_SOUND_THREAD_NAME,
            daemon=True,
        )
        thread.start()

    def _run(self) -> None:
        try:
            self._play()
            log.info("alert sound played")
        except Exception:  # noqa: BLE001
            log.warning("alert sound failed", exc_info=True)

    def _play(self) -> None:
        if self._play_sound is not None:
            self._play_sound()
            return
        # Lazy import so the module loads on non-Windows CI hosts
        # without tripping on winsound availability. winsound is a
        # Python stdlib module on Windows only.
        import winsound  # noqa: PLC0415

        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)


class PopupChannel:
    """Topmost custom Tk window. Primary user-facing alert channel.

    Each call to :meth:`fire` spawns a fresh daemon thread that owns
    its own Tk root, shows the popup, and tears Tk down when the
    user dismisses the window. Threading per alert sidesteps two
    problems at once:

    1. pystray's Win32 backend dispatches menu callbacks from inside
       a WM_COMMAND handler, which already bit us with a
       MessageBox-based About dialog. A dedicated thread gives the
       Tk mainloop its own message pump and sidesteps the issue.

    2. Tcl/Tk is not safe to share across threads. A long-lived Tk
       mainloop that receives events from the detector thread would
       require cross-thread marshalling that is easy to get wrong.
       One root per alert is simple, isolated, and self-cleaning.

    The window is small, modal-to-itself, and focus-grabbing; this
    is deliberate. A security alert that silently scrolls off the
    screen is not useful. Sound is handled by :class:`SoundChannel`
    and is deliberately not coupled to this channel.
    """

    def __init__(
        self,
        *,
        thread_factory: Callable[..., threading.Thread] = threading.Thread,
        tk_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._thread_factory = thread_factory
        self._tk_factory = tk_factory

    def fire(self, event: AlertEvent) -> None:
        log.info("PopupChannel.fire spawning thread")
        thread = self._thread_factory(
            target=self._run,
            args=(event,),
            name=_POPUP_THREAD_NAME,
            daemon=True,
        )
        thread.start()

    def _run(self, event: AlertEvent) -> None:
        try:
            log.info("popup building Tk window")
            self._show(event)
            log.info("popup window dismissed")
        except Exception:  # noqa: BLE001
            log.warning("alert popup failed", exc_info=True)

    def _show(self, event: AlertEvent) -> None:
        tk_ctor = self._tk_factory or _default_tk_factory
        tk = tk_ctor()
        tk.build(
            title=_POPUP_TITLE,
            chain=event.chain,
            before=event.before,
            after=event.after,
            elapsed_ms=event.elapsed_ms,
            warning=_POPUP_WARNING_TEXT,
            button_label=_POPUP_BUTTON_LABEL,
            width=_POPUP_WIDTH_PX,
            height=_POPUP_HEIGHT_PX,
        )
        tk.run()


def _default_tk_factory() -> _TkPopup:
    return _TkPopup()


class _TkPopup:
    """Real Tk popup. Kept in a class to simplify injection in tests.

    Constructs a ``tk.Tk`` root, styles the window to be topmost and
    undecorated enough to read as a security alert rather than a
    regular dialog, and blocks the caller thread on ``mainloop``
    until the user dismisses the window.
    """

    def __init__(self) -> None:
        self._root: Any = None

    def build(
        self,
        *,
        title: str,
        chain: str,
        before: str,
        after: str,
        elapsed_ms: int,
        warning: str,
        button_label: str,
        width: int,
        height: int,
    ) -> None:
        import tkinter as tk  # noqa: PLC0415
        from tkinter import ttk  # noqa: PLC0415

        root = tk.Tk()
        self._root = root
        root.title(title)
        root.resizable(False, False)
        # ``-topmost`` makes the window float above fullscreen apps
        # (games, video players) so a real victim cannot miss the
        # alert. ``focusmodel`` + ``lift`` + ``focus_force`` pulls
        # focus immediately so typing "return" dismisses it without
        # the user having to click into the window first.
        try:
            root.attributes("-topmost", True)
        except tk.TclError:
            log.debug("Tk -topmost attribute unsupported", exc_info=True)

        _center_window(root, width, height)

        frame = ttk.Frame(root, padding=16)
        frame.pack(fill="both", expand=True)

        ttk.Label(
            frame,
            text=title,
            font=("Segoe UI", 11, "bold"),
            wraplength=width - 40,
        ).pack(anchor="w", pady=(0, 8))

        info = (
            f"Chain: {chain}\n"
            f"Original:    {redact(before)}\n"
            f"Substituted: {redact(after)}\n"
            f"Elapsed: {elapsed_ms} ms"
        )
        ttk.Label(
            frame,
            text=info,
            font=("Consolas", 10),
            justify="left",
            wraplength=width - 40,
        ).pack(anchor="w", pady=(0, 10))

        ttk.Label(
            frame,
            text=warning,
            font=("Segoe UI", 10),
            justify="left",
            wraplength=width - 40,
        ).pack(anchor="w", pady=(0, 14))

        btn = ttk.Button(frame, text=button_label, command=self._dismiss)
        btn.pack(anchor="e")
        btn.focus_set()
        root.bind("<Return>", lambda _e: self._dismiss())
        root.bind("<Escape>", lambda _e: self._dismiss())
        root.protocol("WM_DELETE_WINDOW", self._dismiss)

        root.after(50, self._pull_focus)

    def _pull_focus(self) -> None:
        if self._root is None:
            return
        try:
            self._root.lift()
            self._root.focus_force()
        except Exception:  # noqa: BLE001
            log.debug("popup focus pull failed", exc_info=True)

    def _dismiss(self) -> None:
        if self._root is None:
            return
        try:
            self._root.destroy()
        except Exception:  # noqa: BLE001
            log.debug("popup destroy raised", exc_info=True)
        self._root = None

    def run(self) -> None:
        if self._root is None:
            return
        try:
            self._root.mainloop()
        finally:
            self._root = None


def _center_window(root: Any, width: int, height: int) -> None:
    try:
        root.update_idletasks()
        screen_w = root.winfo_screenwidth()
        screen_h = root.winfo_screenheight()
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 3)
        root.geometry(f"{width}x{height}+{x}+{y}")
    except Exception:  # noqa: BLE001
        log.debug("popup centering failed", exc_info=True)


class TrayFlashChannel:
    """Adapter between the dispatcher and :class:`TrayApp.flash`.

    The tray lives in ``__main__`` and is built after the
    :class:`AlertDispatcher`, so we accept a ``flash`` callable the
    tray can bind later. Until binding, :meth:`fire` is a no-op --
    the detection still logs and the other channels still fire.
    """

    def __init__(self, flash: Callable[[float], None] | None = None) -> None:
        self._flash = flash

    def bind(self, flash: Callable[[float], None]) -> None:
        self._flash = flash

    def fire(self, _event: AlertEvent) -> None:
        if self._flash is None:
            return
        self._flash(_TRAY_FLASH_SECONDS)


def build_dispatcher_for_tray(
    *,
    alert_cfg: Any,
    notifier: Any,
    tray_flash_channel: TrayFlashChannel | None = None,
) -> AlertDispatcher:
    """Compose the dispatcher for tray mode.

    Channels are added in "most urgent first" order: popup, sound,
    toast, tray flash. A disabled channel is simply not added rather
    than added and silently skipped; fewer moving parts at detection
    time and the tests can assert on the channel list. Sound is its
    own channel and is independently gated by ``alert_cfg.sound``.
    """
    dispatcher = AlertDispatcher()
    if alert_cfg.popup:
        dispatcher.add(PopupChannel())
    if alert_cfg.sound:
        dispatcher.add(SoundChannel())
    if alert_cfg.toast:
        dispatcher.add(ToastChannel(notifier))
    if alert_cfg.tray_flash and tray_flash_channel is not None:
        dispatcher.add(tray_flash_channel)
    return dispatcher


def build_dispatcher_for_headless(
    *,
    alert_cfg: Any,
    notifier: Any,
) -> AlertDispatcher:
    """Compose the dispatcher for ``--headless`` mode.

    Popup is skipped: ``--headless`` explicitly says "no GUI", and a
    Tk window is still a GUI. Tray flash is skipped because there is
    no tray. Sound is an independent channel, so headless users who
    leave ``alert.sound`` on still get an audible cue alongside the
    toast and the log entry.
    """
    dispatcher = AlertDispatcher()
    if alert_cfg.sound:
        dispatcher.add(SoundChannel())
    if alert_cfg.toast:
        dispatcher.add(ToastChannel(notifier))
    return dispatcher
