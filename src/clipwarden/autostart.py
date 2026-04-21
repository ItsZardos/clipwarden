"""Windows autostart via the per-user Run key.

Scope:

* Registry only, per-user (``HKEY_CURRENT_USER``). HKLM would require
  elevation and Task Scheduler adds no capability the Run key does
  not already provide at login.
* Idempotent. :func:`enable` overwrites; :func:`disable` treats a
  missing entry as success.
* Development-mode no-op. When running from source (``sys.frozen`` is
  falsy) :func:`enable` refuses to wire autostart to ``python.exe`` +
  a script path because that command line is brittle across
  virtualenvs. The caller receives ``False`` and a debug log line.

The registry module is referenced through :data:`_reg` so tests can
substitute a dict-backed implementation without patching the stdlib.
"""

from __future__ import annotations

import logging
import sys
import winreg
from pathlib import Path

log = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "ClipWarden"
TRAY_FLAG = "--tray"

_reg = winreg


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _quoted(path: Path | str) -> str:
    s = str(path)
    if s.startswith('"'):
        return s
    return f'"{s}"'


def is_enabled() -> bool:
    try:
        with _reg.OpenKey(_reg.HKEY_CURRENT_USER, RUN_KEY, 0, _reg.KEY_READ) as key:
            _reg.QueryValueEx(key, VALUE_NAME)
            return True
    except FileNotFoundError:
        return False
    except OSError:
        log.debug("autostart is_enabled query failed", exc_info=True)
        return False


def enable(exe_path: Path | None = None) -> bool:
    """Register ClipWarden for login autostart.

    ``exe_path`` defaults to the current frozen executable. Returns
    ``True`` on success, ``False`` when skipped (development mode) or
    on write failure.
    """
    if not _is_frozen() and exe_path is None:
        log.debug("autostart.enable is a no-op in development mode")
        return False

    target = exe_path if exe_path is not None else Path(sys.executable)
    command = f"{_quoted(target)} {TRAY_FLAG}"

    try:
        with _reg.CreateKey(_reg.HKEY_CURRENT_USER, RUN_KEY) as key:
            _reg.SetValueEx(key, VALUE_NAME, 0, _reg.REG_SZ, command)
        return True
    except OSError:
        log.warning("autostart.enable failed", exc_info=True)
        return False


def disable() -> bool:
    """Remove the autostart entry.

    Returns ``True`` when an entry was removed, ``False`` when no
    entry was present or when the write failed. Callers should not
    treat :func:`disable` as a guaranteed state mutation.
    """
    try:
        with _reg.OpenKey(_reg.HKEY_CURRENT_USER, RUN_KEY, 0, _reg.KEY_SET_VALUE) as key:
            _reg.DeleteValue(key, VALUE_NAME)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        log.warning("autostart.disable failed", exc_info=True)
        return False
