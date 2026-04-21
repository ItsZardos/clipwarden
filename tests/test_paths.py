from __future__ import annotations

from pathlib import Path

from clipwarden import paths


def test_override_env_takes_precedence(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CLIPWARDEN_APPDATA", str(tmp_path))
    monkeypatch.setenv("APPDATA", r"C:\Users\someone\AppData\Roaming")
    assert paths.appdata_dir() == tmp_path


def test_appdata_env_used_when_no_override(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("CLIPWARDEN_APPDATA", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert paths.appdata_dir() == tmp_path / "ClipWarden"


def test_home_fallback_when_no_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("CLIPWARDEN_APPDATA", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert paths.appdata_dir() == tmp_path / ".clipwarden"


def test_ensure_app_dir_creates_directory(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "cw"
    monkeypatch.setenv("CLIPWARDEN_APPDATA", str(target))
    result = paths.ensure_app_dir()
    assert result == target
    assert target.is_dir()


def test_derived_paths(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CLIPWARDEN_APPDATA", str(tmp_path))
    assert paths.config_path() == tmp_path / "config.json"
    assert paths.whitelist_path() == tmp_path / "whitelist.json"
    assert paths.log_path() == tmp_path / "log.jsonl"
