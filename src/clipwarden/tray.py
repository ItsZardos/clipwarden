"""pystray tray app wrapping the ClipWarden runtime.

This module is the interactive front-end for the v1 tray build. It
owns the tray icon, the menu, and the small state machine (enabled /
paused) that flips between them; everything else continues to live in
:mod:`clipwarden.runtime`.

Scaffold commit intentionally ships an empty menu. Follow-up commits
fill in the Enable toggle, Pause submenu, folder shortcuts, About
dialog, and Quit action. Keeping the scaffold minimal gives the rest
of the system (``__main__.py`` rewrite, PyInstaller spec, tests) a
stable type to target while the menu grows.

Design choices:

* Asset resolution tries PyInstaller's ``_MEIPASS`` bundle first, then
  falls back to the repo-relative ``assets/`` directory so the tray
  runs in-place from a dev checkout with no install step.
* ``pystray.Icon`` is injected via an ``icon_factory`` kwarg so the
  tests can substitute a recording fake. ``win32api.MessageBox`` and
  ``threading.Timer`` follow the same pattern.
* The state machine is deliberately thin: two booleans plus an
  optional timer handle. No external store, no file persistence of
  the "Enabled" or "Paused" bits -- on next launch the tray starts
  enabled, matching Phase A's default.
"""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pystray
import win32api
from PIL import Image

log = logging.getLogger(__name__)

_ICON_NORMAL = "icon.ico"
_ICON_DISABLED = "icon-disabled.ico"
_TRAY_TITLE = "ClipWarden"


def _resolve_asset(name: str) -> Path:
    """Locate a bundled asset across PyInstaller, wheel, and dev layouts."""
    meipass = getattr(sys, "_MEIPASS", None)
    if getattr(sys, "frozen", False) and meipass:
        candidate = Path(meipass) / "assets" / name
        if candidate.is_file():
            return candidate
    # Dev layout: src/clipwarden/tray.py -> <repo>/assets/<name>.
    return Path(__file__).resolve().parent.parent.parent / "assets" / name


def _load_image(name: str) -> Image.Image:
    path = _resolve_asset(name)
    with Image.open(path) as im:
        return im.copy()


class TrayApp:
    """pystray wrapper around a :class:`clipwarden.runtime.Runtime`.

    The class is deliberately agnostic about how the runtime starts;
    it calls ``runtime.start()`` / ``runtime.stop()`` and expects them
    to be idempotent (which the current Runtime implementation is, by
    way of Watcher start/stop).
    """

    def __init__(
        self,
        *,
        runtime: Any,
        notifier: Any,
        rt_paths: Any,
        version: str,
        icon_factory: Callable[..., Any] = pystray.Icon,
        message_box: Callable[..., int] = win32api.MessageBox,
        timer_factory: Callable[..., threading.Timer] = threading.Timer,
    ) -> None:
        self._runtime = runtime
        self._notifier = notifier
        self._paths = rt_paths
        self._version = version
        self._icon_factory = icon_factory
        self._message_box = message_box
        self._timer_factory = timer_factory

        self._enabled: bool = True
        self._paused_until_ms: int | None = None
        self._pause_timer: Any | None = None
        self._icon: Any | None = None

    def _current_image(self) -> Image.Image:
        return _load_image(_ICON_NORMAL if self._enabled else _ICON_DISABLED)

    def _refresh_icon(self) -> None:
        """Swap the tray image to match the current enabled state.

        Safe to call before ``run()`` has constructed the icon; the
        initial state is baked in at construction time.
        """
        if self._icon is None:
            return
        try:
            self._icon.icon = self._current_image()
        except Exception:  # noqa: BLE001
            log.warning("tray icon swap failed", exc_info=True)

    def _enable(self) -> None:
        """Start the runtime and flip to the enabled state.

        ``Runtime.start`` is idempotent, but we still gate on
        ``_enabled`` to avoid the log noise of a start-when-running
        call.
        """
        if not self._enabled:
            try:
                self._runtime.start()
            except Exception:  # noqa: BLE001
                log.exception("runtime.start failed from tray")
                return
            self._enabled = True
            self._refresh_icon()

    def _disable(self) -> None:
        """Stop the runtime and flip to the disabled state."""
        if self._enabled:
            try:
                self._runtime.stop()
            except Exception:  # noqa: BLE001
                log.exception("runtime.stop failed from tray")
            self._enabled = False
            self._refresh_icon()

    def _on_toggle_enabled(self, _icon: Any, _item: Any) -> None:
        if self._enabled:
            self._disable()
        else:
            self._enable()

    def _build_menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem(
                "Enable",
                self._on_toggle_enabled,
                checked=lambda _item: self._enabled,
            ),
        )

    def run(self) -> None:
        """Construct the tray icon and block on the pystray event loop."""
        self._icon = self._icon_factory(
            _TRAY_TITLE,
            self._current_image(),
            _TRAY_TITLE,
            self._build_menu(),
        )
        self._icon.run()
