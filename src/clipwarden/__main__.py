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

1. Parse args and configure logging.
2. Acquire the session-scoped singleton mutex. If another instance
   already holds it, show a native MessageBox and exit 0.
3. Build the runtime. On failure, show a MessageBox with the
   exception and the log-file path, then exit 1.
4. Run the selected mode's blocking loop.
5. On exit, stop the runtime (idempotent -- tray's Quit handler also
   calls stop).
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
from pathlib import Path

import win32api

from . import __version__
from . import autostart as _autostart
from . import paths as _paths
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
    runtime = build_runtime()
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
    runtime = build_runtime(rt_paths=rt_paths)
    tray_app = TrayApp(
        runtime=runtime,
        notifier=None,
        rt_paths=rt_paths,
        version=__version__,
    )
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
