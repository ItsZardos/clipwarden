"""Pytest configuration shared across the suite."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def real_addresses() -> dict:
    with (FIXTURES_DIR / "real_addresses.json").open("r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def false_positive_corpus() -> list[str]:
    lines = (FIXTURES_DIR / "false_positives.txt").read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]
