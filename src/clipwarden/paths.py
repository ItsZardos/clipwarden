"""Location of user data on disk.

We keep this in its own module because `config`, `whitelist`, and the
logger all need it, and I want exactly one place that decides "where
does ClipWarden put its files". In production that's ``%APPDATA%\\ClipWarden``.
The test suite doesn't touch the real APPDATA; it points this at a
tmp_path via the CLIPWARDEN_APPDATA env var.
"""

from __future__ import annotations

import os
from pathlib import Path

APP_DIR_NAME = "ClipWarden"
_OVERRIDE_ENV = "CLIPWARDEN_APPDATA"


def appdata_dir() -> Path:
    """Return the per-user ClipWarden directory.

    Resolution order:
      1. ``CLIPWARDEN_APPDATA`` env var (tests, unusual setups)
      2. ``%APPDATA%\\ClipWarden`` on Windows
      3. ``~/.clipwarden`` as a last-resort fallback (non-Windows dev hosts)

    The directory is not created here; callers that need it on disk
    should invoke :func:`ensure_app_dir`.
    """
    override = os.environ.get(_OVERRIDE_ENV)
    if override:
        return Path(override)
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / APP_DIR_NAME
    return Path.home() / ".clipwarden"


def ensure_app_dir() -> Path:
    d = appdata_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_path() -> Path:
    return appdata_dir() / "config.json"


def whitelist_path() -> Path:
    return appdata_dir() / "whitelist.json"


def log_path() -> Path:
    return appdata_dir() / "log.jsonl"
