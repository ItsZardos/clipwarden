"""Windows toast notifier.

Thin wrapper over :mod:`winotify` so the runtime depends on a single
stable interface. All platform-specific behaviour lives here.

* Notifications are best-effort. An exception raised from
  ``winotify`` must not take down the worker thread; failures are
  logged at ``warning`` and swallowed.
* ``enabled=False`` short-circuits before constructing a
  ``Notification``, which keeps the library's COM setup out of the
  path for users who have toasts disabled.
* Toast body redacts the middle of each address so the before/after
  contrast stays readable at a glance. The full addresses are written
  to ``log.jsonl``; the toast is a pointer to that record, not the
  record itself.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

from winotify import Notification

from .detector import DetectionEvent

log = logging.getLogger(__name__)

APP_ID = "ClipWarden"
_DEFAULT_DURATION = "short"


def _redact_address(address: str, *, head: int = 6, tail: int = 4) -> str:
    """Return ``head`` leading chars, an ellipsis, and ``tail`` trailing chars.

    Strings shorter than or equal to ``head + tail + 1`` are returned
    unchanged because the redacted form would be no shorter.
    """
    if len(address) <= head + tail + 1:
        return address
    return f"{address[:head]}\u2026{address[-tail:]}"


class NotifierProtocol(Protocol):
    """Minimal surface the runtime uses; tests provide a fake."""

    def notify_substitution(self, event: DetectionEvent) -> None: ...

    def notify_info(self, title: str, body: str) -> None: ...


class Notifier:
    def __init__(
        self,
        *,
        enabled: bool = True,
        icon_path: Path | None = None,
        app_id: str = APP_ID,
    ) -> None:
        self._enabled = enabled
        self._app_id = app_id
        # Windows Toast XML resolves relative paths against the app's
        # working directory, which is unpredictable under PyInstaller.
        self._icon = str(icon_path.resolve()) if icon_path is not None else ""

    @property
    def enabled(self) -> bool:
        return self._enabled

    def notify_substitution(self, event: DetectionEvent) -> None:
        if not self._enabled:
            return
        before = _redact_address(event.before)
        after = _redact_address(event.after)
        title = "Possible clipboard hijack"
        body = (
            f"{event.chain} address changed in {event.elapsed_ms} ms.\nWas: {before}\nNow: {after}"
        )
        self._show(title, body)

    def notify_info(self, title: str, body: str) -> None:
        if not self._enabled:
            return
        self._show(title, body)

    def _show(self, title: str, body: str) -> None:
        try:
            toast = Notification(
                app_id=self._app_id,
                title=title,
                msg=body,
                icon=self._icon,
                duration=_DEFAULT_DURATION,
            )
            toast.show()
        except Exception:  # noqa: BLE001
            log.warning("Toast notification failed", exc_info=True)
