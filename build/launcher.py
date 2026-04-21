"""PyInstaller entry-point shim.

PyInstaller's bootloader executes the entry script as a top-level
module with ``__package__`` unset, which breaks the relative imports
in :mod:`clipwarden.__main__` (``from . import __version__`` etc).

This shim performs an absolute import of the real main so the
packaged exe behaves identically to ``python -m clipwarden``. Keep
this file boring; it must not accrue logic. Everything real lives in
``src/clipwarden/__main__.py`` so that ``python -m clipwarden`` and
the frozen exe share one code path.

It also wraps the import + main() call in a stdlib-only try/except
that writes import-time tracebacks to
``%APPDATA%\\ClipWarden\\crash.log``. This duplicates the logic
inside :func:`clipwarden.__main__._write_crash_log` on purpose: the
wrapper inside ``__main__`` cannot catch failures that occur while
``clipwarden.__main__`` is itself being imported, so the launcher
needs its own fallback that depends only on the stdlib. Path
selection must match :func:`clipwarden.paths.appdata_dir`; both
default to the Roaming profile so every piece of user-writable state
(config, whitelist, log.jsonl, crash.log) lives in one directory.
"""

from __future__ import annotations

import datetime
import os
import sys
import traceback
from pathlib import Path


def _appdata_dir() -> Path | None:
    """Mirror ``clipwarden.paths.appdata_dir`` using stdlib only.

    The launcher cannot import ``clipwarden.paths`` here because the
    whole point of this shim is to survive failures that happen
    *while* the ``clipwarden`` package is being imported. So we
    re-derive the same path from the environment, using the same
    resolution order (``CLIPWARDEN_APPDATA`` override > ``%APPDATA%``
    > ``~/.clipwarden``) so a user with a non-standard profile still
    gets a consistent directory across the two crash-log writers.
    """
    override = os.environ.get("CLIPWARDEN_APPDATA")
    if override:
        return Path(override)
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "ClipWarden"
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME")
    if home:
        return Path(home) / ".clipwarden"
    return None


def _launcher_crash_log(exc_type, exc, tb) -> Path | None:
    try:
        crash_dir = _appdata_dir()
        if crash_dir is None:
            return None
        crash_dir.mkdir(parents=True, exist_ok=True)
        crash_file = crash_dir / "crash.log"
        with crash_file.open("a", encoding="utf-8") as f:
            f.write("\n=== " + datetime.datetime.now().isoformat() + " ===\n")
            f.write("origin: launcher.py (pre-main)\n")
            f.write(f"sys.executable: {sys.executable}\n")
            f.write(f"sys.argv: {sys.argv}\n")
            f.write(f"frozen: {getattr(sys, 'frozen', False)}\n")
            traceback.print_exception(exc_type, exc, tb, file=f)
            f.flush()
        return crash_file
    except Exception:  # noqa: BLE001
        return None


def _launcher_message_box(title: str, body: str) -> None:
    try:
        import win32api  # noqa: PLC0415

        win32api.MessageBox(0, body, title, 0x00000010)
    except Exception:  # noqa: BLE001
        # Crash happened before win32api could be imported or the
        # dialog failed to show. Either way the crash.log is already
        # on disk, so there is nothing further to do.
        pass


def _launcher_main() -> int:
    try:
        from clipwarden.__main__ import main  # noqa: PLC0415

        return main()
    except SystemExit:
        raise
    except BaseException as err:  # noqa: BLE001
        crash_path = _launcher_crash_log(type(err), err, err.__traceback__)
        body = f"ClipWarden could not start.\n\n{type(err).__name__}: {err}\n\n" + (
            f"Crash log:\n{crash_path}" if crash_path else "(no crash log written)"
        )
        _launcher_message_box("ClipWarden failed to start", body)
        return 1


if __name__ == "__main__":
    sys.exit(_launcher_main())
