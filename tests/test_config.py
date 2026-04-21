from __future__ import annotations

import json
from pathlib import Path

import pytest

from clipwarden import config as cfgmod


def test_default_round_trip(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    cfg = cfgmod.default_config()
    cfgmod.save(cfg, p)
    loaded = cfgmod.load(p)
    assert loaded == cfg


def test_missing_file_returns_default(tmp_path: Path) -> None:
    cfg = cfgmod.load(tmp_path / "nope.json")
    assert cfg == cfgmod.default_config()


def test_partial_file_fills_defaults(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"substitution_window_ms": 2000}), encoding="utf-8")
    cfg = cfgmod.load(p)
    assert cfg.substitution_window_ms == 2000
    assert cfg.enabled_chains == cfgmod.DEFAULT_CHAINS


def test_corrupt_file_is_backed_up(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    p.write_text("{not valid json", encoding="utf-8")
    cfg = cfgmod.load(p)
    assert cfg == cfgmod.default_config()
    # The corrupt file should have been moved aside.
    backups = list(tmp_path.glob("config.json.bak-*"))
    assert len(backups) == 1
    assert not p.exists()


def test_corrupt_file_backup_survives_rename_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = tmp_path / "config.json"
    p.write_text("{bad", encoding="utf-8")

    def boom(self, target):
        raise OSError("simulated")

    monkeypatch.setattr(Path, "rename", boom)
    cfg = cfgmod.load(p)
    assert cfg == cfgmod.default_config()


@pytest.mark.parametrize(
    "payload",
    [
        {"enabled_chains": "BTC"},
        {"enabled_chains": ["DOGE"]},
        {"enabled_chains": ["BTC", "BTC"]},
        {"substitution_window_ms": -1},
        {"substitution_window_ms": 99_999},
        {"substitution_window_ms": True},
        {"user_input_grace_ms": -10},
        {"user_input_grace_ms": 100_000},
        {"user_input_grace_ms": "fast"},
        {"user_input_grace_ms": True},
        {"autostart": "yes"},
        {"notifications_enabled": 1},
        {"totally_unknown_key": 42},
    ],
)
def test_invalid_payloads_are_backed_up(tmp_path: Path, payload: dict) -> None:
    p = tmp_path / "config.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    cfg = cfgmod.load(p)
    assert cfg == cfgmod.default_config()
    assert list(tmp_path.glob("config.json.bak-*"))


def test_non_object_root_is_backed_up(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    p.write_text("[1, 2, 3]", encoding="utf-8")
    cfg = cfgmod.load(p)
    assert cfg == cfgmod.default_config()


def test_save_uses_atomic_rename(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    cfg = cfgmod.default_config().with_changes(substitution_window_ms=1234)
    cfgmod.save(cfg, p)
    assert p.exists()
    assert not (tmp_path / "config.json.tmp").exists()
    loaded_raw = json.loads(p.read_text(encoding="utf-8"))
    assert loaded_raw["substitution_window_ms"] == 1234
    assert loaded_raw["enabled_chains"] == list(cfgmod.DEFAULT_CHAINS)


def test_with_changes_returns_new_instance() -> None:
    cfg = cfgmod.default_config()
    other = cfg.with_changes(autostart=True)
    assert cfg.autostart is False
    assert other.autostart is True
