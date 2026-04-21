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

import contextlib
import logging
import os
import sys
import threading
import time
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

_PAUSE_15M_SECONDS = 15 * 60
_PAUSE_1H_SECONDS = 60 * 60

# MB_OK | MB_ICONINFORMATION. Hard-coded so the module does not have
# to import win32con just for two values; pywin32 defines these as
# 0x0 and 0x40 respectively, same as the Win32 header.
_MB_OK_INFO = 0x00000040

_ABOUT_TITLE = "About ClipWarden"

# Sentinel stored in ``_paused_until_ms`` when the user chooses
# "Until I resume". Monotonic milliseconds are always non-negative,
# so a negative value unambiguously means "paused with no deadline".
PAUSE_INDEFINITE: int = -1


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
        open_path: Callable[[str], Any] = os.startfile,
    ) -> None:
        self._runtime = runtime
        self._notifier = notifier
        self._paths = rt_paths
        self._version = version
        self._icon_factory = icon_factory
        self._message_box = message_box
        self._timer_factory = timer_factory
        self._open_path = open_path

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
        # Manual toggle always wins: if a pause is in flight we cancel
        # its timer and clear the deadline before touching runtime
        # state so the menu doesn't briefly show both "Resume now"
        # active and "Enable" checked.
        self._clear_pause(cancel_timer=True)
        if self._enabled:
            self._disable()
        else:
            self._enable()

    @property
    def _is_paused(self) -> bool:
        return self._paused_until_ms is not None

    def _clear_pause(self, *, cancel_timer: bool) -> None:
        if cancel_timer and self._pause_timer is not None:
            try:
                self._pause_timer.cancel()
            except Exception:  # noqa: BLE001
                log.debug("pause timer cancel raised", exc_info=True)
        self._pause_timer = None
        self._paused_until_ms = None

    def _pause_for(self, seconds: float) -> None:
        """Disable the runtime and schedule an auto-resume timer.

        A new pause replaces any existing one; the pending timer (if
        any) is cancelled before the new deadline is set.
        """
        self._clear_pause(cancel_timer=True)
        if self._enabled:
            self._disable()
        deadline_ms = int(time.monotonic() * 1000) + int(seconds * 1000)
        self._paused_until_ms = deadline_ms
        timer = self._timer_factory(seconds, self._on_pause_timeout)
        # Daemon so a wedged timer cannot pin process exit; pystray
        # already runs on the main thread and handles signal delivery.
        # Test fakes without a ``daemon`` attribute are fine; the real
        # ``threading.Timer`` accepts the assignment.
        with contextlib.suppress(AttributeError):
            timer.daemon = True
        self._pause_timer = timer
        timer.start()

    def _pause_indefinite(self) -> None:
        self._clear_pause(cancel_timer=True)
        if self._enabled:
            self._disable()
        self._paused_until_ms = PAUSE_INDEFINITE

    def _on_pause_timeout(self) -> None:
        """Timer callback: clear the pause and re-enable the runtime."""
        self._pause_timer = None
        self._paused_until_ms = None
        self._enable()

    def _resume(self) -> None:
        """Manual ``Resume now`` action."""
        self._clear_pause(cancel_timer=True)
        self._enable()

    def _on_pause_15m(self, _icon: Any, _item: Any) -> None:
        self._pause_for(_PAUSE_15M_SECONDS)

    def _on_pause_1h(self, _icon: Any, _item: Any) -> None:
        self._pause_for(_PAUSE_1H_SECONDS)

    def _on_pause_indefinite(self, _icon: Any, _item: Any) -> None:
        self._pause_indefinite()

    def _on_resume_now(self, _icon: Any, _item: Any) -> None:
        self._resume()

    def _open(self, target: Path) -> None:
        """Shell-open a file or directory.

        ``os.startfile`` raises on missing paths; we log and swallow
        so a stale config or a freshly-uninstalled install dir does
        not crash the tray loop. The failure is not silent to the
        user forever -- the About dialog surfaces the same paths for
        manual inspection.
        """
        try:
            self._open_path(str(target))
        except OSError:
            log.warning("tray failed to open %s", target, exc_info=True)

    def _on_open_config(self, _icon: Any, _item: Any) -> None:
        self._open(self._paths.config)

    def _on_open_log_folder(self, _icon: Any, _item: Any) -> None:
        # log.jsonl lives directly in %APPDATA%\ClipWarden, so the
        # folder is its parent.
        self._open(self._paths.log.parent)

    def _on_open_history_folder(self, _icon: Any, _item: Any) -> None:
        # "History" is a user-facing alias for the same directory
        # that holds log.jsonl. The two menu items are kept distinct
        # because users searching for "history" shouldn't have to
        # know the operations term "log folder".
        self._open(self._paths.log.parent)

    def _about_body(self) -> str:
        # Exact wording locked in the Phase B plan. The first line is
        # always "ClipWarden <version>" -- the product name is the
        # brand, the descriptive subtitle sits on line 3.
        return (
            f"ClipWarden {self._version}\n"
            "\n"
            "Windows clipboard hijacking monitor\n"
            "Defends against cryptocurrency clipper malware\n"
            "\n"
            "Ethan Tharp\n"
            "https://ethantharp.dev\n"
            "\n"
            "Copyright (c) 2026 Ethan Tharp\n"
            "Released under the MIT License"
        )

    def _on_about(self, _icon: Any, _item: Any) -> None:
        try:
            self._message_box(0, self._about_body(), _ABOUT_TITLE, _MB_OK_INFO)
        except Exception:  # noqa: BLE001
            log.warning("About MessageBox failed", exc_info=True)

    def _on_quit(self, _icon: Any, _item: Any) -> None:
        """Tear down the runtime then signal the tray loop to exit.

        The outer ``__main__`` also calls ``runtime.stop()`` after
        ``tray.run()`` returns; stopping here first means the log
        handler is closed before ``pystray`` begins its own teardown,
        which keeps the shutdown trace tidy. ``Runtime.stop`` is
        idempotent so the second call is harmless.
        """
        self._clear_pause(cancel_timer=True)
        try:
            self._runtime.stop()
        except Exception:  # noqa: BLE001
            log.exception("runtime.stop failed during tray Quit")
        self._enabled = False
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:  # noqa: BLE001
                log.warning("icon.stop raised during Quit", exc_info=True)

    def _build_menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem(
                "Enable",
                self._on_toggle_enabled,
                checked=lambda _item: self._enabled,
            ),
            pystray.MenuItem(
                "Pause",
                pystray.Menu(
                    pystray.MenuItem("15 minutes", self._on_pause_15m),
                    pystray.MenuItem("1 hour", self._on_pause_1h),
                    pystray.MenuItem("Until I resume", self._on_pause_indefinite),
                    pystray.Menu.SEPARATOR,
                    pystray.MenuItem(
                        "Resume now",
                        self._on_resume_now,
                        enabled=lambda _item: self._is_paused,
                    ),
                ),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Open Config", self._on_open_config),
            pystray.MenuItem("Open Log Folder", self._on_open_log_folder),
            pystray.MenuItem("Open History Folder", self._on_open_history_folder),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("About ClipWarden", self._on_about),
            pystray.MenuItem("Quit ClipWarden", self._on_quit),
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
