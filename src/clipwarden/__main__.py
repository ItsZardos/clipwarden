"""Headless entry point.

Prints a banner, starts the runtime, blocks on Ctrl-C, and stops
cleanly. A future tray entry point will wrap the same
:func:`Runtime.start` and :func:`Runtime.stop` pair.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading

from . import __version__
from .runtime import build_runtime


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="clipwarden", description=f"ClipWarden {__version__}")
    p.add_argument(
        "--tray",
        action="store_true",
        help="Reserved for the tray entry point; currently behaves identically to headless.",
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
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    banner = f"ClipWarden {__version__}"
    if args.version:
        print(banner, flush=True)
        return 0
    print(f"{banner} - headless. Ctrl-C to exit.", flush=True)

    runtime = build_runtime()
    runtime.start()

    stop_event = threading.Event()

    def _handle_signal(signum, _frame):  # noqa: ANN001
        logging.getLogger(__name__).info("received signal %d; stopping", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _handle_signal)

    try:
        # signal.pause is not available on Windows. A short polling
        # wait keeps the KeyboardInterrupt path responsive on consoles
        # where the signal does not set the event.
        while not stop_event.is_set():
            stop_event.wait(timeout=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        runtime.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
