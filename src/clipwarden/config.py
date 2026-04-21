"""User configuration.

The config is intentionally small. If a value isn't covered here, the
right fix is to revisit the threat model, not to sprinkle more knobs.

Design choices worth flagging:

* Frozen dataclass. Config is read on startup and on the Settings dialog
  save; it is never mutated in place. Passing a new instance around is
  easier to reason about than a mutable object that any thread can poke.

* Corrupt-file policy: if ``config.json`` exists but won't parse or has
  the wrong shape, we rename it to ``config.json.bak-<ts>`` and fall
  back to defaults. The alternative (refuse to start) is worse for a
  security tool users leave running unattended: a silently-disabled
  monitor is a worse failure than a reverted setting.

* Validation is strict. Unknown keys are rejected. Known keys get type
  and range checks. This catches hand-edits that would otherwise turn
  into confusing runtime bugs.
"""

from __future__ import annotations

import contextlib
import json
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from .constants import DEFAULT_SUBSTITUTION_WINDOW_MS, DEFAULT_USER_INPUT_GRACE_MS

VALID_CHAINS: frozenset[str] = frozenset({"BTC", "ETH", "XMR", "SOL"})
DEFAULT_CHAINS: tuple[str, ...] = ("BTC", "ETH", "XMR", "SOL")

MIN_WINDOW_MS = 100
MAX_WINDOW_MS = 10_000
MIN_GRACE_MS = 0
MAX_GRACE_MS = 10_000

_ALERT_KEYS: frozenset[str] = frozenset({"popup", "toast", "sound", "tray_flash"})


class ConfigError(ValueError):
    """Raised when a config file is present but malformed."""


@dataclass(frozen=True)
class AlertConfig:
    """Multi-channel alert configuration.

    Each field gates one alert channel. All default True because a
    clipper attack costs real money in seconds and the right default
    for a security tool is "every channel on, power users can opt
    out." See :mod:`clipwarden.alert` for the channels themselves.

    Channel semantics:

    * ``popup`` -- custom topmost Tk window. Bypasses Focus Assist /
      Do Not Disturb because the OS treats it as a user window, not a
      shell notification. This is the primary channel.
    * ``toast`` -- Windows shell toast via ``winotify``. Respects
      Focus Assist, so it is a secondary channel that power users can
      disable without losing detection visibility.
    * ``sound`` -- ``winsound.MessageBeep(MB_ICONEXCLAMATION)`` when
      the popup fires. Skipped in headless mode only because the
      popup itself is skipped there.
    * ``tray_flash`` -- tray icon swaps to the alert variant for a
      few seconds after a detection, providing passive awareness if
      the user isn't at the screen.
    """

    popup: bool = True
    toast: bool = True
    sound: bool = True
    tray_flash: bool = True


@dataclass(frozen=True)
class Config:
    enabled_chains: tuple[str, ...] = field(default=DEFAULT_CHAINS)
    substitution_window_ms: int = DEFAULT_SUBSTITUTION_WINDOW_MS
    user_input_grace_ms: int = DEFAULT_USER_INPUT_GRACE_MS
    # Legacy v0 gate. Kept because existing config files on disk
    # still carry it; treated as a kill-switch: False disables every
    # alert channel regardless of the per-channel flags.
    notifications_enabled: bool = True
    alert: AlertConfig = field(default_factory=AlertConfig)

    def with_changes(self, **kwargs: Any) -> Config:
        return replace(self, **kwargs)


def default_config() -> Config:
    return Config()


def _validate_alert(raw: Any) -> AlertConfig:
    if raw is None:
        return AlertConfig()
    if not isinstance(raw, dict):
        raise ConfigError("alert must be an object")
    extra = set(raw) - _ALERT_KEYS
    if extra:
        raise ConfigError(f"unknown alert keys: {sorted(extra)}")
    for key in _ALERT_KEYS:
        if key in raw and not isinstance(raw[key], bool):
            raise ConfigError(f"alert.{key} must be a boolean")
    defaults = AlertConfig()
    return AlertConfig(
        popup=raw.get("popup", defaults.popup),
        toast=raw.get("toast", defaults.toast),
        sound=raw.get("sound", defaults.sound),
        tray_flash=raw.get("tray_flash", defaults.tray_flash),
    )


def _validate(data: dict[str, Any]) -> Config:
    known = {
        "enabled_chains",
        "substitution_window_ms",
        "user_input_grace_ms",
        "notifications_enabled",
        "alert",
    }
    extra = set(data) - known
    if extra:
        raise ConfigError(f"unknown config keys: {sorted(extra)}")

    chains_raw = data.get("enabled_chains", list(DEFAULT_CHAINS))
    if not isinstance(chains_raw, list) or not all(isinstance(c, str) for c in chains_raw):
        raise ConfigError("enabled_chains must be a list of strings")
    chains = tuple(chains_raw)
    bad = [c for c in chains if c not in VALID_CHAINS]
    if bad:
        raise ConfigError(f"unsupported chains: {bad}")
    if len(set(chains)) != len(chains):
        raise ConfigError("enabled_chains contains duplicates")

    window = data.get("substitution_window_ms", DEFAULT_SUBSTITUTION_WINDOW_MS)
    if not isinstance(window, int) or isinstance(window, bool):
        raise ConfigError("substitution_window_ms must be an integer")
    if not (MIN_WINDOW_MS <= window <= MAX_WINDOW_MS):
        raise ConfigError(f"substitution_window_ms out of range [{MIN_WINDOW_MS}, {MAX_WINDOW_MS}]")

    grace = data.get("user_input_grace_ms", DEFAULT_USER_INPUT_GRACE_MS)
    if not isinstance(grace, int) or isinstance(grace, bool):
        raise ConfigError("user_input_grace_ms must be an integer")
    if not (MIN_GRACE_MS <= grace <= MAX_GRACE_MS):
        raise ConfigError(f"user_input_grace_ms out of range [{MIN_GRACE_MS}, {MAX_GRACE_MS}]")

    notifs = data.get("notifications_enabled", True)
    if not isinstance(notifs, bool):
        raise ConfigError("notifications_enabled must be a boolean")

    alert = _validate_alert(data.get("alert"))

    return Config(
        enabled_chains=chains,
        substitution_window_ms=window,
        user_input_grace_ms=grace,
        notifications_enabled=notifs,
        alert=alert,
    )


def _backup_corrupt(path: Path) -> Path:
    # Millisecond suffix so rapid successive corruptions don't clobber
    # each other's backups.
    ts = int(time.time() * 1000)
    target = path.with_suffix(path.suffix + f".bak-{ts}")
    path.rename(target)
    return target


def load(path: Path) -> Config:
    """Load config from disk, falling back to defaults.

    Missing file -> defaults, and the defaults are immediately
    persisted so the user has an editable file on disk to point to
    from the Help/docs and so later tooling (migration on next
    start, config-watching UX) can assume ``path`` exists.
    Bad JSON or schema -> file is renamed aside, defaults returned.
    Legacy ``autostart`` key -> silently stripped and re-saved; see
    :func:`_migrate_legacy_keys`.
    """
    if not path.exists():
        cfg = default_config()
        # Best-effort persist. A read-only profile or antivirus lock
        # must not prevent startup; the in-memory defaults remain
        # valid, the next successful save will create the file, and
        # detection still runs in the meantime.
        with contextlib.suppress(OSError):
            save(cfg, path)
        return cfg
    try:
        raw_text = path.read_text(encoding="utf-8")
        data = json.loads(raw_text)
        if not isinstance(data, dict):
            raise ConfigError("config root must be an object")
        migrated = _migrate_legacy_keys(data)
        cfg = _validate(migrated)
        # Rewrite the file once so future startups don't re-trigger
        # the migration path. Rewrite is best-effort: if the disk is
        # read-only we still return the validated config rather than
        # refuse to start.
        if migrated is not data:
            with contextlib.suppress(OSError):
                save(cfg, path)
        return cfg
    except (OSError, json.JSONDecodeError, ConfigError):
        # If we cannot even rename the bad file, defaults are still the
        # safer outcome for a security tool; the user will notice on
        # next save when the file reappears.
        with contextlib.suppress(OSError):
            _backup_corrupt(path)
        return default_config()


# Legacy keys that existed in a prior release but no longer do. We
# silently drop them on load instead of refusing to start so upgrading
# users are not greeted with a "config corrupt" backup-and-reset.
_LEGACY_KEYS: frozenset[str] = frozenset({"autostart"})


def _migrate_legacy_keys(data: dict[str, Any]) -> dict[str, Any]:
    """Strip legacy keys from ``data`` and return the cleaned copy.

    Returns the same object unchanged when no legacy key is present
    so the caller can use ``is`` to detect whether a rewrite is
    needed. autostart moved from config.json to an installer-managed
    HKCU Run value; keeping the field here meant that two sources of
    truth could disagree silently.
    """
    if not _LEGACY_KEYS.intersection(data):
        return data
    cleaned = {k: v for k, v in data.items() if k not in _LEGACY_KEYS}
    return cleaned


def save(cfg: Config, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(cfg)
    payload["enabled_chains"] = list(payload["enabled_chains"])
    # ``asdict`` already flattens the nested AlertConfig into a dict;
    # nothing extra to do here, but keeping a comment so future
    # readers don't add a redundant ``payload["alert"] = ...`` line.
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
