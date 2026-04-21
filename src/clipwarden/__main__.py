"""ClipWarden entry point.

Modes:

* default -- tray mode. Constructs the runtime, starts it, runs the
  pystray event loop. Quit from the tray cleanly stops the runtime.
* ``--headless`` -- Phase A behavior. Starts the runtime, blocks on
  Ctrl-C, stops cleanly. Retained for smoke testing and for running
  on a headless CI host.
* ``--install-autostart`` / ``--uninstall-autostart`` -- invoked by
  the Inno Setup installer and uninstaller so the autostart codepath
  is centralised here and can be edited in one place in v1.1+. These
  flags exit immediately after toggling the HKCU\\...\\Run entry.
* ``--version`` -- print the banner and exit without touching the
  clipboard.

Startup sequence (tray / headless paths):

1. Mark the process per-monitor DPI aware so the tray icon, About
   MessageBox, and Tk alert popup render crisply on HiDPI displays.
2. Parse args and configure logging.
3. Acquire the session-scoped singleton mutex. If another instance
   already holds it, show a native MessageBox and exit 0.
4. Build the alert dispatcher for the selected mode (tray vs. headless)
   and build the runtime with it wired in.
5. Run the selected mode's blocking loop.
6. On exit, stop the runtime (idempotent -- tray's Quit handler also
   calls stop).
"""

from __future__ import annotations

import argparse
import ctypes
import logging
import signal
import sys
import threading
from pathlib import Path

import win32api

from . import __version__
from . import autostart as _autostart
from . import config as _config
from . import paths as _paths
from .alert import (
    TrayFlashChannel,
    build_dispatcher_for_headless,
    build_dispatcher_for_tray,
)
from .notifier import Notifier
from .runtime import RuntimePaths, build_runtime
from .singleton import SINGLETON_MUTEX_NAME
from .singleton import acquire as acquire_singleton
from .tray import TrayApp

log = logging.getLogger(__name__)

_MB_OK_INFO = 0x00000040
_MB_OK_ERROR = 0x00000010

_SECOND_INSTANCE_TITLE = "ClipWarden"
_SECOND_INSTANCE_BODY = "ClipWarden is already running. Check your system tray."
_STARTUP_FAILURE_TITLE = "ClipWarden failed to start"

# SetProcessDpiAwarenessContext sentinel. ``-4`` is
# DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 (Windows 10 1703+),
# which is the strongest mode available to desktop apps and the
# one Windows documentation recommends for new tray apps.
_DPI_CONTEXT_PER_MONITOR_AWARE_V2 = -4
# SetProcessDpiAwareness value for PROCESS_PER_MONITOR_DPI_AWARE
# (Windows 8.1 fallback when the V2 context API is unavailable).
_PROCESS_PER_MONITOR_DPI_AWARE = 2


def _enable_dpi_awareness() -> None:
    """Mark the process as per-monitor DPI aware before any GUI code runs.

    Without this, the frozen ClipWarden.exe ships as DPI-unaware and
    Windows applies DPI virtualization on HiDPI displays (125%/150%
    scaling): the whole app is rendered at 96 DPI and then bitmap-
    upscaled, which blurs the tray icon on top of any blur introduced
    by Shell_NotifyIcon's own stretch. Becoming per-monitor aware
    lets Windows ask the app for larger native HICONs and renders
    text crisply in the About MessageBox and the Tk alert popup.

    Must be called before any window is created (tray icon, Tk root,
    win32 MessageBox). Safe to call multiple times; Windows silently
    ignores subsequent calls once a context is set.

    Tries three APIs in order of preference:

    1. ``user32.SetProcessDpiAwarenessContext(-4)`` - Windows 10 1703+.
    2. ``shcore.SetProcessDpiAwareness(2)`` - Windows 8.1+.
    3. ``user32.SetProcessDPIAware()`` - Vista+, system-DPI only.

    Any failure is swallowed: a locked-down host or a future Windows
    breakage must not prevent startup.
    """
    try:
        user32 = ctypes.windll.user32
    except (OSError, AttributeError):
        return
    try:
        set_ctx = getattr(user32, "SetProcessDpiAwarenessContext", None)
        if set_ctx is not None and set_ctx(ctypes.c_void_p(_DPI_CONTEXT_PER_MONITOR_AWARE_V2)):
            return
    except OSError:
        log.debug("SetProcessDpiAwarenessContext raised", exc_info=True)
    try:
        shcore = ctypes.windll.shcore
        if shcore.SetProcessDpiAwareness(_PROCESS_PER_MONITOR_DPI_AWARE) == 0:
            return
    except (OSError, AttributeError):
        log.debug("SetProcessDpiAwareness fallback unavailable", exc_info=True)
    try:
        user32.SetProcessDPIAware()
    except OSError:
        log.debug("SetProcessDPIAware fallback failed", exc_info=True)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="clipwarden", description=f"ClipWarden {__version__}")
    p.add_argument(
        "--headless",
        action="store_true",
        help="Run without a tray icon; log to stderr and block on Ctrl-C.",
    )
    # Default mode is tray now; --tray is accepted as a legacy alias
    # so existing HKCU\...\Run entries written by earlier builds keep
    # working after upgrade.
    p.add_argument(
        "--tray",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    p.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    p.add_argument(
        "--version",
        action="store_true",
        help="Print the banner and exit without starting the runtime.",
    )
    # Installer/uninstaller hooks. Hidden from --help because end
    # users should not invoke them directly; the Inno Setup script
    # drives both.
    p.add_argument(
        "--install-autostart",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    p.add_argument(
        "--uninstall-autostart",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return p.parse_args(argv)


def _show_message(title: str, body: str, flags: int) -> None:
    try:
        win32api.MessageBox(0, body, title, flags)
    except Exception:  # noqa: BLE001
        log.warning("MessageBox(%r) failed", title, exc_info=True)


def _show_second_instance_message() -> None:
    _show_message(_SECOND_INSTANCE_TITLE, _SECOND_INSTANCE_BODY, _MB_OK_INFO)


def _show_startup_failure(err: BaseException) -> None:
    try:
        log_path: Path | str = _paths.log_path()
    except Exception:  # noqa: BLE001
        log_path = "<unavailable>"
    body = f"ClipWarden could not start.\n\n{type(err).__name__}: {err}\n\nLog file:\n{log_path}"
    _show_message(_STARTUP_FAILURE_TITLE, body, _MB_OK_ERROR)


def _run_headless() -> int:
    # Headless mode is "no GUI." The popup channel is a Tk window
    # and the tray flash channel requires a tray, so neither
    # applies. Sound and toast remain available through the
    # headless dispatcher builder.
    rt_paths = RuntimePaths.resolve()
    cfg = _config.load(rt_paths.config)
    notifier = Notifier(enabled=cfg.notifications_enabled)
    dispatcher = build_dispatcher_for_headless(alert_cfg=cfg.alert, notifier=notifier)
    runtime = build_runtime(
        cfg=cfg,
        rt_paths=rt_paths,
        notifier=notifier,
        alert_dispatcher=dispatcher,
    )
    runtime.start()
    stop_event = threading.Event()

    def _handle_signal(signum, _frame):  # noqa: ANN001
        log.info("received signal %d; stopping", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _handle_signal)

    try:
        # signal.pause is not available on Windows; a short polling
        # wait keeps the KeyboardInterrupt path responsive on consoles
        # where the signal does not set the event.
        while not stop_event.is_set():
            stop_event.wait(timeout=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        runtime.stop()
    return 0


def _run_tray() -> int:
    rt_paths = RuntimePaths.resolve()
    cfg = _config.load(rt_paths.config)
    notifier = Notifier(enabled=cfg.notifications_enabled)
    # The flash channel is constructed unbound because the TrayApp
    # does not exist yet. We bind ``tray_app.flash`` into it after
    # construction so the dispatcher can fire the channel without
    # needing a handle to the tray directly.
    flash_channel = TrayFlashChannel()
    dispatcher = build_dispatcher_for_tray(
        alert_cfg=cfg.alert,
        notifier=notifier,
        tray_flash_channel=flash_channel if cfg.alert.tray_flash else None,
    )
    runtime = build_runtime(
        cfg=cfg,
        rt_paths=rt_paths,
        notifier=notifier,
        alert_dispatcher=dispatcher,
    )
    tray_app = TrayApp(
        runtime=runtime,
        notifier=notifier,
        rt_paths=rt_paths,
        version=__version__,
    )
    if cfg.alert.tray_flash:
        flash_channel.bind(tray_app.flash)
    runtime.start()
    try:
        tray_app.run()
    finally:
        # Tray's Quit handler stops the runtime too; this is the
        # defensive idempotent final call.
        runtime.stop()
    return 0


def _install_autostart() -> int:
    ok = _autostart.enable()
    if not ok:
        log.error(
            "autostart enable failed; ensure ClipWarden is running as the "
            "installed frozen exe, not from source"
        )
    return 0 if ok else 1


def _uninstall_autostart() -> int:
    # disable() returns False when the entry was already absent; that
    # is not a failure condition for the uninstaller path, so exit
    # 0 in either case.
    _autostart.disable()
    return 0


def main(argv: list[str] | None = None) -> int:
    # Must run before any GUI code (Tk, pystray, win32 MessageBox)
    # creates its first window; a window inherits its process's DPI
    # awareness context at creation time and cannot be upgraded
    # afterwards.
    _enable_dpi_awareness()
    args = _parse_args(argv)
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.version:
        print(f"ClipWarden {__version__}", flush=True)
        return 0

    if args.install_autostart:
        return _install_autostart()
    if args.uninstall_autostart:
        return _uninstall_autostart()

    handle = acquire_singleton(SINGLETON_MUTEX_NAME)
    if handle is None:
        _show_second_instance_message()
        return 0

    with handle:
        try:
            if args.headless:
                print(f"ClipWarden {__version__} - headless. Ctrl-C to exit.", flush=True)
                return _run_headless()
            return _run_tray()
        except Exception as err:  # noqa: BLE001
            log.exception("ClipWarden startup failed")
            _show_startup_failure(err)
            return 1


if __name__ == "__main__":
    sys.exit(main())
