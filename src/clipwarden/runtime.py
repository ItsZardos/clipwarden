"""Runtime composition.

Glues the watcher, worker, detector, logger, notifier, and whitelist
behind a single start/stop surface. This is the only module that
combines pure-logic modules with Windows-specific modules; everything
below it remains testable in isolation and everything above it treats
:class:`Runtime` as opaque.

Shutdown ordering:

1. Signal the watcher's stop event.
2. Post the wake message so the pump loop unblocks.
3. Join the pump thread with a bounded timeout.
4. Poison-pill the worker queue.
5. Join the worker thread with a bounded timeout.
6. Flush and close logger handles.
7. Return.

Each stage has its own timeout so a wedge in one does not pin the
process at exit. Timeouts default to two seconds per stage; steps 1-5
are handled inside :meth:`Watcher.stop`.
"""

from __future__ import annotations

import contextlib
import ctypes
import logging
import os
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

from . import config as _config
from . import logger as _logger
from . import paths as _paths
from . import whitelist as _whitelist
from .alert import AlertDispatcher, AlertEvent
from .classifier import Chain
from .config import Config
from .detector import Detector
from .notifier import Notifier, NotifierProtocol
from .watcher import ClipboardEvent, Watcher, monotonic_ms

log = logging.getLogger(__name__)

# Development-only override. When set, :func:`last_input_ts_ms` always
# reports an arbitrarily old timestamp, which disables the detector's
# "user activity since previous copy suppresses this detection" gate.
# Intended for local smoke harnesses where the operator's own mouse
# and keyboard activity would otherwise be indistinguishable from a
# deliberate recopy. Refused in frozen builds so the flag cannot leak
# into shipped binaries via environment-variable inheritance.
_DEMO_MODE_ENV = "CLIPWARDEN_DEMO_MODE"


_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


_GetLastInputInfo = _user32.GetLastInputInfo
_GetLastInputInfo.argtypes = [ctypes.POINTER(_LASTINPUTINFO)]
_GetLastInputInfo.restype = wintypes.BOOL

_GetTickCount = _kernel32.GetTickCount
_GetTickCount.argtypes = []
_GetTickCount.restype = wintypes.DWORD


def _demo_mode_enabled() -> bool:
    """Return True when the development demo-mode override is in effect.

    The flag is honored in source runs (``python -m clipwarden`` from a
    checkout) so the smoke harness can suppress the user-input gate.
    Frozen builds (PyInstaller ``sys.frozen`` or ``sys._MEIPASS``) ignore
    the flag regardless of the environment variable so an accidentally
    inherited ``CLIPWARDEN_DEMO_MODE=1`` cannot reach end users.
    """
    if not os.environ.get(_DEMO_MODE_ENV):
        return False
    if getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS"):
        log.warning(
            "CLIPWARDEN_DEMO_MODE set in a frozen build; ignoring. "
            "This override is source-run only."
        )
        return False
    return True


def last_input_ts_ms() -> int:
    """Return the user's last-input timestamp in the monotonic frame.

    ``GetLastInputInfo.dwTime`` is reported in the ``GetTickCount``
    frame (milliseconds since boot, 32-bit, rolling approximately
    every 49.7 days). Both clocks are sampled within a few
    microseconds of each other on each call and combined into the
    monotonic frame the detector expects. Recomputing the offset per
    call avoids needing a persistent rollover correction.

    When :func:`_demo_mode_enabled` is true, or when
    ``GetLastInputInfo`` fails, the function returns a very negative
    value so the detector's ``last_input_ts_ms > prev_ts`` check
    evaluates to False. This matches the security posture of
    preferring to alert rather than suppress when input tracking is
    unavailable.
    """
    if _demo_mode_enabled():
        return -(2**62)
    info = _LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
    if not _GetLastInputInfo(ctypes.byref(info)):
        return -(2**62)
    tick_now = _GetTickCount()
    mono_now = monotonic_ms()
    offset = mono_now - tick_now
    return int(info.dwTime) + offset


@dataclass
class RuntimePaths:
    config: Path
    whitelist: Path
    log: Path

    @classmethod
    def resolve(cls) -> RuntimePaths:
        _paths.ensure_app_dir()
        return cls(
            config=_paths.config_path(),
            whitelist=_paths.whitelist_path(),
            log=_paths.log_path(),
        )


class Runtime:
    """One watcher, one detector, one logger, one notifier.

    Prefer :func:`build_runtime` to constructing :class:`Runtime`
    directly; the factory knows how to assemble the defaults from
    disk.
    """

    def __init__(
        self,
        *,
        cfg: Config,
        rt_paths: RuntimePaths,
        detector: Detector,
        notifier: NotifierProtocol,
        logger: logging.Logger,
        alert_dispatcher: AlertDispatcher | None = None,
        watcher_factory=Watcher,
    ) -> None:
        self._cfg = cfg
        self._paths = rt_paths
        self._detector = detector
        self._notifier = notifier
        self._logger = logger
        # The dispatcher is optional so legacy callers (and the
        # ``notifications_enabled=False`` kill-switch path) still work.
        # When absent, the runtime falls back to calling the notifier
        # directly; this preserves the Phase A behaviour for any
        # embedding that hasn't migrated to the multi-channel world.
        self._alerts = alert_dispatcher
        self._watcher = watcher_factory(self._on_clipboard_event)

    def start(self) -> None:
        log.info("ClipWarden runtime starting")
        self._watcher.start()

    def reset_detector(self) -> None:
        """Clear detector memory of the last classified address.

        Called by the tray on enable/disable transitions so a user who
        paused for hours does not get a stale-baseline alert on their
        first post-resume copy. Exposed as a method rather than
        poking ``_detector`` directly so the runtime owns the
        contract; a future config-reload path should also call this.
        """
        self._detector.reset()

    def stop(self, *, per_stage_timeout_s: float = 2.0) -> None:
        log.info("ClipWarden runtime stopping")
        t0 = time.monotonic()
        try:
            self._watcher.stop(timeout=per_stage_timeout_s)
        except Exception:  # noqa: BLE001
            log.exception("Watcher stop raised; continuing teardown")
        if self._alerts is not None:
            with contextlib.suppress(Exception):
                self._alerts.close()
        with contextlib.suppress(Exception):
            _logger.close_logger()
        log.info("ClipWarden runtime stopped in %.0f ms", (time.monotonic() - t0) * 1000)

    def _on_clipboard_event(self, ev: ClipboardEvent) -> None:
        # Non-text payloads still advance the sequence. Feeding an
        # empty string through classify preserves the detector's
        # "non-address content between two addresses" laundering
        # signal, which would otherwise be lost.
        text = ev.text if ev.text is not None else ""
        detection = self._detector.observe(
            text=text,
            ts_ms=ev.ts_ms,
            last_input_ts_ms=last_input_ts_ms(),
        )
        if detection is None:
            return
        try:
            _logger.log_detection(self._logger, detection)
        except Exception:  # noqa: BLE001
            log.exception("log_detection failed")
        if detection.whitelisted:
            return
        if not self._cfg.notifications_enabled:
            # Legacy global kill-switch. If the user has turned off
            # notifications entirely, honor that: skip every alert
            # channel. The log entry above still fires so the audit
            # trail is preserved.
            return
        if self._alerts is not None:
            # Multi-channel dispatch. The dispatcher swallows per-
            # channel exceptions internally, so a broken popup does
            # not take down the toast.
            self._alerts.dispatch(AlertEvent.from_detection(detection))
            return
        # No dispatcher wired: fall back to the direct toast call so
        # the runtime still alerts in the Phase A composition.
        try:
            self._notifier.notify_substitution(detection)
        except Exception:  # noqa: BLE001
            log.exception("notify_substitution failed")


def build_runtime(
    cfg: Config | None = None,
    *,
    rt_paths: RuntimePaths | None = None,
    notifier: NotifierProtocol | None = None,
    alert_dispatcher: AlertDispatcher | None = None,
) -> Runtime:
    """Assemble a :class:`Runtime` from disk-backed defaults.

    Loads config, whitelist, and the rotating detection logger from
    their canonical locations (see :mod:`clipwarden.paths`). Tests
    that need finer control should construct :class:`Runtime`
    directly.

    Pass ``alert_dispatcher`` to hook into the multi-channel alert
    system from :mod:`clipwarden.alert`; leaving it as ``None`` falls
    back to the Phase A direct-to-notifier path so callers that
    haven't migrated still get a working runtime.
    """
    rt_paths = rt_paths or RuntimePaths.resolve()
    cfg = cfg if cfg is not None else _config.load(rt_paths.config)

    wl = _whitelist.Whitelist.load(rt_paths.whitelist)
    # Translate the config's string chain list into the Chain enum set
    # the classifier understands. Config validation already rejects
    # unknown strings, so any unknown at this point means an upstream
    # contract was violated; raise loudly instead of silently dropping
    # so a misconfigured embedding fails at startup, not at runtime.
    unknown = [c for c in cfg.enabled_chains if c not in Chain.__members__]
    if unknown:
        raise ValueError(
            f"enabled_chains contains unsupported entries: {sorted(unknown)}"
        )
    enabled_chains: frozenset[Chain] = frozenset(Chain(c) for c in cfg.enabled_chains)
    detector = Detector(
        substitution_window_ms=cfg.substitution_window_ms,
        is_whitelisted=wl.contains,
        enabled_chains=enabled_chains,
    )
    detection_logger = _logger.get_logger(rt_paths.log)
    notifier = notifier or Notifier(enabled=cfg.notifications_enabled)

    return Runtime(
        cfg=cfg,
        rt_paths=rt_paths,
        detector=detector,
        notifier=notifier,
        logger=detection_logger,
        alert_dispatcher=alert_dispatcher,
        # Resolved from module globals at call time so tests can swap
        # in a fake via monkeypatch.setattr(runtime, "Watcher", ...).
        watcher_factory=Watcher,
    )
