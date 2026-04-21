"""Autostart tests.

``winreg`` is replaced with a dict-backed fake for hermetic tests.
Hitting the real registry during CI would mutate the user's session
and leak across test runs.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from clipwarden import autostart


class _FakeRegistry:
    """Dict-backed stand-in for the few winreg APIs autostart.py uses."""

    HKEY_CURRENT_USER = "HKCU"
    KEY_READ = 0x1
    KEY_SET_VALUE = 0x2
    REG_SZ = 1

    def __init__(self) -> None:
        # keys: {(root, subkey): {value_name: (value, type)}}
        self._keys: dict[tuple[str, str], dict[str, tuple[str, int]]] = {}

    class _Key:
        def __init__(self, reg: _FakeRegistry, root: str, subkey: str) -> None:
            self._reg = reg
            self._root = root
            self._subkey = subkey

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        @property
        def _values(self) -> dict[str, tuple[str, int]]:
            return self._reg._keys[(self._root, self._subkey)]

    def OpenKey(self, root, subkey, _reserved, _access):  # noqa: N802 - winreg shape
        if (root, subkey) not in self._keys:
            raise FileNotFoundError(subkey)
        return self._Key(self, root, subkey)

    @contextmanager
    def CreateKey(self, root, subkey):  # noqa: N802
        self._keys.setdefault((root, subkey), {})
        yield self._Key(self, root, subkey)

    def QueryValueEx(self, key, name):  # noqa: N802
        values = key._values
        if name not in values:
            raise FileNotFoundError(name)
        return values[name]

    def SetValueEx(self, key, name, _reserved, type_, value):  # noqa: N802
        key._values[name] = (value, type_)

    def DeleteValue(self, key, name):  # noqa: N802
        values = key._values
        if name not in values:
            raise FileNotFoundError(name)
        del values[name]


@pytest.fixture
def fake_reg(monkeypatch):
    reg = _FakeRegistry()
    monkeypatch.setattr(autostart, "_reg", reg, raising=True)
    return reg


@pytest.fixture
def frozen(monkeypatch):
    """Pretend we're running as a frozen exe so enable() isn't a dev no-op."""
    monkeypatch.setattr(autostart, "_is_frozen", lambda: True, raising=True)


def test_is_enabled_false_when_absent(fake_reg):
    assert autostart.is_enabled() is False


def test_enable_then_is_enabled(fake_reg, frozen):
    exe = Path(r"C:\Program Files\ClipWarden\ClipWarden.exe")
    assert autostart.enable(exe) is True
    assert autostart.is_enabled() is True
    values = fake_reg._keys[("HKCU", autostart.RUN_KEY)]
    assert values[autostart.VALUE_NAME][0] == f'"{exe}" {autostart.TRAY_FLAG}'


def test_enable_is_idempotent(fake_reg, frozen):
    exe = Path(r"C:\ClipWarden.exe")
    autostart.enable(exe)
    autostart.enable(exe)
    values = fake_reg._keys[("HKCU", autostart.RUN_KEY)]
    # Exactly one value registered even after two calls.
    assert list(values.keys()) == [autostart.VALUE_NAME]


def test_disable_removes_entry(fake_reg, frozen):
    exe = Path(r"C:\ClipWarden.exe")
    autostart.enable(exe)
    assert autostart.disable() is True
    assert autostart.is_enabled() is False


def test_disable_when_absent_is_false(fake_reg):
    # The Run key itself doesn't exist yet; disable should not raise.
    assert autostart.disable() is False


def test_disable_when_key_exists_but_value_absent(fake_reg, frozen):
    # Create the Run key with a different value, then call disable.
    with fake_reg.CreateKey("HKCU", autostart.RUN_KEY) as k:
        fake_reg.SetValueEx(k, "SomeoneElse", 0, 1, '"C:\\other.exe"')
    assert autostart.disable() is False


def test_enable_no_op_in_dev_mode(fake_reg, monkeypatch):
    # Default frozen is False, and we pass no exe_path.
    monkeypatch.setattr(autostart, "_is_frozen", lambda: False, raising=True)
    assert autostart.enable() is False
    assert autostart.is_enabled() is False
