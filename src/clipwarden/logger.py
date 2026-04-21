"""Detection log: one JSON object per line.

The file format is a stable schema. The history window, exporters, and
any external tooling that reads ``log.jsonl`` depend on it. Adding a
field is fine; renaming or removing one is a breaking change.

Rotation uses stdlib :class:`logging.handlers.RotatingFileHandler`. I
don't want to hand-roll rotation logic; it has subtle edge cases around
open handles on Windows that the stdlib has already solved. The parts I
want control over are (a) the record format and (b) suppressing all the
stdlib decorations like level names, timestamps-in-text, etc. That's
what :class:`_RawJsonlFormatter` does: the record's ``msg`` is already
a finished JSON string, we just hand it back verbatim.

The schema lives in :func:`_to_payload`. A detection line looks like::

    {
      "kind": "detection",
      "ts_ms": 1700000000123,
      "chain": "BTC",
      "before": "bc1q...",
      "after": "bc1q...",
      "elapsed_ms": 420
    }

A whitelisted event uses ``"kind": "whitelisted_skip"``. Keeping both
lines in the same file lets the history window show "this would have
fired but you trusted the address" for debugging. The whitelist field
on :class:`DetectionEvent` is the authoritative signal; callers should
not second-guess it.
"""

from __future__ import annotations

import contextlib
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from .detector import DetectionEvent

LOGGER_NAME = "clipwarden.detections"
MAX_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 3


class _RawJsonlFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # The message is already a JSON line; do not let stdlib add
        # timestamps, level names, or module tags to it.
        return record.getMessage()


def _find_handler(logger: logging.Logger, resolved: str) -> RotatingFileHandler | None:
    for h in logger.handlers:
        if isinstance(h, RotatingFileHandler) and h.baseFilename == resolved:
            return h
    return None


def get_logger(
    path: Path,
    *,
    max_bytes: int = MAX_BYTES,
    backup_count: int = BACKUP_COUNT,
) -> logging.Logger:
    """Return the detection logger bound to ``path``.

    Idempotent: calling twice with the same path returns the same logger
    with a single handler. Different path? We swap the handler. That
    makes the Settings-dialog "move log location" flow tidy later.
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    resolved = str(path.resolve())
    existing = _find_handler(logger, resolved)
    if existing is not None:
        return logger

    # Remove any prior handler pointing somewhere else before adding the
    # new one. Otherwise get_logger on a new path would duplicate writes.
    for h in list(logger.handlers):
        if isinstance(h, RotatingFileHandler):
            logger.removeHandler(h)
            h.close()

    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        filename=str(path),
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
        delay=True,
    )
    handler.setFormatter(_RawJsonlFormatter())
    logger.addHandler(handler)
    return logger


def close_logger() -> None:
    """Detach and close all handlers. Tests call this between cases so
    Windows file locks don't linger; production calls it on shutdown."""
    logger = logging.getLogger(LOGGER_NAME)
    for h in list(logger.handlers):
        logger.removeHandler(h)
        # Close is best-effort: a handler refusing to close is about to
        # be garbage-collected anyway and we would rather shut down
        # cleanly than propagate an obscure stdlib exception.
        with contextlib.suppress(Exception):
            h.close()


def _to_payload(event: DetectionEvent) -> dict[str, Any]:
    return {
        "kind": "whitelisted_skip" if event.whitelisted else "detection",
        "ts_ms": event.ts_ms,
        "chain": event.chain,
        "before": event.before,
        "after": event.after,
        "elapsed_ms": event.elapsed_ms,
    }


def log_detection(logger: logging.Logger, event: DetectionEvent) -> None:
    logger.info(json.dumps(_to_payload(event), ensure_ascii=False))
