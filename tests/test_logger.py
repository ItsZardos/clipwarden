from __future__ import annotations

import json
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from clipwarden import logger as lg
from clipwarden.detector import DetectionEvent


@pytest.fixture(autouse=True)
def _clean_logger():
    # Each test owns the logger fresh. Windows will otherwise hold the
    # previous test's log file open and cause ugly PermissionErrors.
    lg.close_logger()
    yield
    lg.close_logger()


def _event(**overrides) -> DetectionEvent:
    base = dict(
        ts_ms=1_700_000_000_000,
        chain="BTC",
        before="1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa",
        after="1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2",
        elapsed_ms=400,
        whitelisted=False,
    )
    base.update(overrides)
    return DetectionEvent(**base)


def test_detection_line_schema(tmp_path: Path) -> None:
    path = tmp_path / "log.jsonl"
    logger = lg.get_logger(path)
    lg.log_detection(logger, _event())
    lg.close_logger()

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload == {
        "kind": "detection",
        "ts_ms": 1_700_000_000_000,
        "chain": "BTC",
        "before": "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa",
        "after": "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2",
        "elapsed_ms": 400,
    }


def test_whitelisted_line_uses_skip_kind(tmp_path: Path) -> None:
    path = tmp_path / "log.jsonl"
    logger = lg.get_logger(path)
    lg.log_detection(logger, _event(whitelisted=True))
    lg.close_logger()
    payload = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert payload["kind"] == "whitelisted_skip"


def test_get_logger_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "log.jsonl"
    a = lg.get_logger(path)
    b = lg.get_logger(path)
    handlers = [h for h in a.handlers if isinstance(h, RotatingFileHandler)]
    assert a is b
    assert len(handlers) == 1


def test_switching_paths_swaps_handler(tmp_path: Path) -> None:
    p1 = tmp_path / "log1.jsonl"
    p2 = tmp_path / "log2.jsonl"
    logger = lg.get_logger(p1)
    lg.log_detection(logger, _event())
    logger = lg.get_logger(p2)
    lg.log_detection(logger, _event())
    lg.close_logger()
    assert p1.exists() and p1.read_text(encoding="utf-8").strip()
    assert p2.exists() and p2.read_text(encoding="utf-8").strip()
    # Only one active handler after swap
    handlers = [h for h in logger.handlers if isinstance(h, RotatingFileHandler)]
    assert len(handlers) <= 1


def test_rotation(tmp_path: Path) -> None:
    path = tmp_path / "log.jsonl"
    # Tiny max_bytes to force rotation after a couple of writes
    logger = lg.get_logger(path, max_bytes=200, backup_count=2)
    for i in range(50):
        lg.log_detection(logger, _event(ts_ms=i))
    lg.close_logger()
    rotated = sorted(tmp_path.glob("log.jsonl*"))
    # At least one rotation produced
    assert any(p.name != "log.jsonl" for p in rotated)
    # Backup count is respected (primary + <= backup_count rotated)
    assert len(rotated) <= 1 + 2


def test_close_logger_safe_when_nothing_open() -> None:
    lg.close_logger()
    lg.close_logger()


def test_jsonl_output_is_one_line_per_event(tmp_path: Path) -> None:
    path = tmp_path / "log.jsonl"
    logger = lg.get_logger(path)
    for i in range(5):
        lg.log_detection(logger, _event(ts_ms=i))
    lg.close_logger()
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 5
    for line in lines:
        json.loads(line)


def test_get_logger_is_idempotent_with_casefolded_paths(tmp_path: Path) -> None:
    """Windows paths differing only in case must not produce duplicate handlers.

    Without normalising via ``os.path.normcase``, a call with
    ``C:\\Users\\...\\log.jsonl`` and ``c:\\users\\...\\log.jsonl``
    would attach a second handler and duplicate every write.
    """
    path_lower = tmp_path / "log.jsonl"
    logger = lg.get_logger(path_lower)
    # Same absolute path, lookup by a different case. On non-Windows
    # normcase is a no-op so the assertion still holds via the
    # straight equality path.
    same_again = lg.get_logger(Path(str(path_lower).swapcase()))
    handlers = [h for h in logger.handlers if isinstance(h, RotatingFileHandler)]
    assert logger is same_again
    assert len(handlers) == 1


def test_rotation_error_goes_to_diagnostic_logger(tmp_path, caplog) -> None:
    """A handler-level write failure surfaces through clipwarden.diagnostic.

    stderr is swallowed in --noconsole builds, so the custom handler
    routes handleError through the diagnostic logger instead. Callers
    (packagers, ops) can then see a "log write failed" line in
    diagnostic.log without running the binary under a debugger.
    """
    import logging  # noqa: PLC0415

    path = tmp_path / "log.jsonl"
    logger = lg.get_logger(path)
    handler = next(
        h for h in logger.handlers if isinstance(h, RotatingFileHandler)
    )
    with caplog.at_level(logging.ERROR, logger="clipwarden.diagnostic"):
        try:
            try:
                raise OSError("simulated disk full")
            except OSError:
                handler.handleError(logging.makeLogRecord({"msg": "bad write"}))
        finally:
            lg.close_logger()

    assert any(
        "detection log write failed" in rec.message for rec in caplog.records
    )
