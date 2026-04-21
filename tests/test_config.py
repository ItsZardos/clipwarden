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
    other = cfg.with_changes(substitution_window_ms=1111)
    assert cfg.substitution_window_ms == cfgmod.DEFAULT_SUBSTITUTION_WINDOW_MS
    assert other.substitution_window_ms == 1111


class TestAutostartMigration:
    """One-shot migration: strip legacy ``autostart`` key on load."""

    def test_legacy_autostart_key_is_dropped(self, tmp_path: Path) -> None:
        p = tmp_path / "config.json"
        p.write_text(json.dumps({"autostart": True}), encoding="utf-8")

        cfg = cfgmod.load(p)

        assert cfg == cfgmod.default_config()
        assert not hasattr(cfg, "autostart")

    def test_migration_rewrites_file_without_autostart(self, tmp_path: Path) -> None:
        # The cleaned config must be persisted so the next startup
        # does not re-walk the migration path. Future unknown-key
        # checks would then trip on autostart if we kept it.
        p = tmp_path / "config.json"
        p.write_text(
            json.dumps({"autostart": False, "substitution_window_ms": 2500}),
            encoding="utf-8",
        )

        cfgmod.load(p)

        rewritten = json.loads(p.read_text(encoding="utf-8"))
        assert "autostart" not in rewritten
        assert rewritten["substitution_window_ms"] == 2500

    def test_non_legacy_config_is_not_rewritten(self, tmp_path: Path) -> None:
        # Sanity: a config file with no legacy keys must be left
        # byte-for-byte alone. We pin this to keep the migration path
        # cheap on the common case.
        p = tmp_path / "config.json"
        original = json.dumps({"substitution_window_ms": 1500}) + "\n"
        p.write_text(original, encoding="utf-8")

        before_mtime = p.stat().st_mtime_ns
        cfgmod.load(p)
        after_mtime = p.stat().st_mtime_ns

        assert before_mtime == after_mtime
        assert p.read_text(encoding="utf-8") == original

    def test_autostart_invalid_value_still_migrates(self, tmp_path: Path) -> None:
        # The validator used to refuse ``{"autostart": "yes"}``.
        # Post-migration it must be silently stripped, not
        # backed-up-as-corrupt: upgrading users with a weird legacy
        # value should not see a surprise "config reset".
        p = tmp_path / "config.json"
        p.write_text(json.dumps({"autostart": "yes"}), encoding="utf-8")

        cfg = cfgmod.load(p)

        assert cfg == cfgmod.default_config()
        assert not list(tmp_path.glob("config.json.bak-*"))


class TestAlertConfig:
    def test_default_has_all_channels_on(self) -> None:
        cfg = cfgmod.default_config()
        assert cfg.alert.popup is True
        assert cfg.alert.toast is True
        assert cfg.alert.sound is True
        assert cfg.alert.tray_flash is True

    def test_missing_alert_section_uses_defaults(self, tmp_path: Path) -> None:
        # Legacy v0 config files on disk have no alert block; they
        # must still load cleanly without triggering the corrupt-file
        # backup path.
        p = tmp_path / "config.json"
        p.write_text(
            json.dumps({"substitution_window_ms": 2000}),
            encoding="utf-8",
        )
        cfg = cfgmod.load(p)
        assert cfg.alert == cfgmod.AlertConfig()
        assert not list(tmp_path.glob("config.json.bak-*"))

    def test_partial_alert_section_fills_defaults(self, tmp_path: Path) -> None:
        p = tmp_path / "config.json"
        p.write_text(
            json.dumps({"alert": {"toast": False}}),
            encoding="utf-8",
        )
        cfg = cfgmod.load(p)
        assert cfg.alert.toast is False
        assert cfg.alert.popup is True
        assert cfg.alert.sound is True
        assert cfg.alert.tray_flash is True

    def test_all_channels_off_is_accepted(self, tmp_path: Path) -> None:
        p = tmp_path / "config.json"
        p.write_text(
            json.dumps(
                {
                    "alert": {
                        "popup": False,
                        "toast": False,
                        "sound": False,
                        "tray_flash": False,
                    }
                }
            ),
            encoding="utf-8",
        )
        cfg = cfgmod.load(p)
        assert cfg.alert.popup is False
        assert cfg.alert.toast is False
        assert cfg.alert.sound is False
        assert cfg.alert.tray_flash is False

    @pytest.mark.parametrize(
        "bad_alert",
        [
            "yes",
            123,
            ["popup"],
            {"popup": "true"},
            {"popup": 1},
            {"unknown_channel": True},
            {"popup": True, "extra": False},
        ],
    )
    def test_invalid_alert_block_is_backed_up(self, tmp_path: Path, bad_alert) -> None:
        p = tmp_path / "config.json"
        p.write_text(json.dumps({"alert": bad_alert}), encoding="utf-8")
        cfg = cfgmod.load(p)
        assert cfg == cfgmod.default_config()
        assert list(tmp_path.glob("config.json.bak-*"))

    def test_round_trip_preserves_alert_block(self, tmp_path: Path) -> None:
        p = tmp_path / "config.json"
        cfg = cfgmod.default_config().with_changes(
            alert=cfgmod.AlertConfig(popup=True, toast=False, sound=True, tray_flash=False)
        )
        cfgmod.save(cfg, p)
        raw = json.loads(p.read_text(encoding="utf-8"))
        assert raw["alert"] == {
            "popup": True,
            "toast": False,
            "sound": True,
            "tray_flash": False,
        }
        assert cfgmod.load(p) == cfg
