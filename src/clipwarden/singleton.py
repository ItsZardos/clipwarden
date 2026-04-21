"""Single-instance gate backed by a named Win32 mutex.

ClipWarden's v1 tray process assumes exactly one listener is wired to
the clipboard per interactive user. Two listeners would doubly-log
every copy and race each other on ``AddClipboardFormatListener``
unregistration at exit. A named mutex in the ``Local\\`` namespace
gives us a cheap session-scoped gate: the kernel tracks ownership,
the OS reclaims it automatically on abnormal process exit, and the
name is visible only within the current logon session so another
user on the same machine cannot collide with us.

Public surface::

    from clipwarden.singleton import acquire, SINGLETON_MUTEX_NAME

    handle = acquire(SINGLETON_MUTEX_NAME)
    if handle is None:
        # Another instance is already running; show a MessageBox.
        return 0
    with handle:
        run_app()

``pywin32`` is referenced through the module-level :data:`_event` and
:data:`_api` aliases so tests can substitute a fake without patching
the library globally. Same indirection pattern :mod:`clipwarden.autostart`
uses for :mod:`winreg`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import win32api
import win32event
import winerror

log = logging.getLogger(__name__)

SINGLETON_MUTEX_NAME = r"Local\ClipWarden-Singleton-Mutex"

_event = win32event
_api = win32api
_ERROR_ALREADY_EXISTS = winerror.ERROR_ALREADY_EXISTS


@dataclass
class SingletonHandle:
    """Owning wrapper around the handle returned by ``CreateMutex``.

    The kernel releases the mutex when the handle is closed. Leaking
    the handle is not immediately catastrophic because process exit
    will close all handles, but a long-running process that hot-swaps
    instances (tests do this) would accumulate kernel objects.

    :meth:`release` is idempotent; ``with handle: ...`` is the
    recommended pattern.
    """

    handle: Any
    _released: bool = field(default=False, repr=False)

    def release(self) -> None:
        if self._released:
            return
        try:
            _api.CloseHandle(self.handle)
        except Exception:  # noqa: BLE001
            log.debug("CloseHandle on singleton mutex raised", exc_info=True)
        self._released = True

    def __enter__(self) -> SingletonHandle:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        self.release()


def acquire(name: str) -> SingletonHandle | None:
    """Claim the named mutex for this process.

    Returns a live :class:`SingletonHandle` when this process created
    the mutex fresh, or ``None`` when another process already owns
    it. ``CreateMutex`` returns a valid handle even on
    ``ERROR_ALREADY_EXISTS``; we close that handle immediately so the
    kernel object is not leaked on the collision path.
    """
    handle = _event.CreateMutex(None, False, name)
    last_error = _api.GetLastError()
    if last_error == _ERROR_ALREADY_EXISTS:
        try:
            _api.CloseHandle(handle)
        except Exception:  # noqa: BLE001
            log.debug("CloseHandle on duplicate singleton raised", exc_info=True)
        return None
    return SingletonHandle(handle=handle)
