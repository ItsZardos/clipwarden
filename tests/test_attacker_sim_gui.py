"""Unit coverage for the attacker-sim GUI without instantiating Tk.

The GUI itself is trivial glue; the interesting bits are:

* ``tools.attacker_sim._fixtures_path`` must honour ``sys._MEIPASS``
  so the PyInstaller-frozen GUI finds the bundled JSON instead of
  the source-layout path that does not exist in a release exe.
* The safety gate (``attacker_sim._ACKNOWLEDGED``) must still be the
  single point of enforcement. Calling the GUI's clipboard-write
  paths without flipping the flag must raise, same as the CLI.
* ``_shorten`` must middle-elide long addresses without touching
  short ones so the BEFORE/AFTER preview fits the window.

Running a real ``Tk`` under pytest in CI is brittle (needs a display
connection on Linux, can leak in Windows when parallelised), so the
tests deliberately avoid ``tk.Tk()``.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


def _import_tools_module(name: str):
    """Load a module under ``tools/`` by file path, bypassing sys.path."""
    path = Path(__file__).resolve().parent.parent / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


attacker_sim = _import_tools_module("attacker_sim")
attacker_sim_gui = _import_tools_module("attacker_sim_gui")


class TestShorten:
    def test_short_address_is_untouched(self):
        assert attacker_sim_gui._shorten("abc") == "abc"

    def test_long_address_is_middle_elided(self):
        long = "x" * 50
        out = attacker_sim_gui._shorten(long, keep=8)
        assert out.startswith("xxxxxxxx")
        assert out.endswith("xxxxxxxx")
        assert "..." in out
        assert len(out) < len(long)

    def test_boundary_length_matches_formula(self):
        just_short = "y" * 23
        just_long = "y" * 24
        assert attacker_sim_gui._shorten(just_short, keep=10) == just_short
        assert "..." in attacker_sim_gui._shorten(just_long, keep=10)


class TestFrozenFixtureLookup:
    """``_fixtures_path`` must prefer the PyInstaller bundle layout."""

    def test_honours_meipass_when_set(self, tmp_path, monkeypatch):
        bundle = tmp_path / "attacker_sim_fixtures"
        bundle.mkdir()
        bundled = bundle / "real_addresses.json"
        bundled.write_text(json.dumps({"entries": []}), encoding="utf-8")
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

        resolved = attacker_sim._fixtures_path()
        assert resolved == bundled

    def test_falls_back_to_source_layout(self, monkeypatch):
        # Unset _MEIPASS if present (development runs do not have it).
        monkeypatch.delattr(sys, "_MEIPASS", raising=False)
        resolved = attacker_sim._fixtures_path()
        assert resolved.parts[-3:] == ("tests", "fixtures", "real_addresses.json")

    def test_loader_uses_current_fixture_path(self, tmp_path, monkeypatch):
        """_load_addresses_by_chain re-resolves the path each call.

        Freezing the path at import time would break the GUI, which
        imports the module then relies on ``sys._MEIPASS`` once Tk
        is up; the loader MUST call ``_fixtures_path()`` per-call.
        """
        bundle = tmp_path / "attacker_sim_fixtures"
        bundle.mkdir()
        (bundle / "real_addresses.json").write_text(
            json.dumps(
                {
                    "entries": [
                        {"chain": "BTC", "address": "111"},
                        {"chain": "BTC", "address": "222"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

        by_chain = attacker_sim._load_addresses_by_chain()
        assert by_chain == {"BTC": ["111", "222"]}


class TestSafetyGate:
    """The GUI must not be able to write the clipboard without the gate.

    This mirrors the CLI test: the import-and-call-a-helper bypass
    is explicitly rejected. The Fire button flips ``_ACKNOWLEDGED``
    on its worker thread; outside of that window the helper refuses
    to run.
    """

    def test_set_clipboard_refused_when_not_acknowledged(self, monkeypatch):
        monkeypatch.setattr(attacker_sim, "_ACKNOWLEDGED", False, raising=True)
        with pytest.raises(attacker_sim._SafetyError):
            attacker_sim._set_clipboard_text("hi")

    def test_module_exposes_expected_surface(self):
        # The GUI loads attacker_sim via spec_from_file_location; if
        # those symbol names ever drift, the GUI would fail at first
        # click rather than at import, so pin the contract here.
        for name in (
            "_ACKNOWLEDGED",
            "_SafetyError",
            "_set_clipboard_text",
            "_pick_pair",
            "_load_addresses_by_chain",
            "ChainPair",
        ):
            assert hasattr(attacker_sim, name), f"attacker_sim lost symbol: {name}"


class TestGuiReusesAttackerSim:
    """Confirm the GUI imports the CLI module rather than forking it."""

    def test_gui_references_shared_module(self):
        # Same module object, not a parallel copy; a fork would let
        # the GUI bypass the gate by maintaining its own
        # ``_ACKNOWLEDGED`` flag.
        assert attacker_sim_gui.attacker_sim is sys.modules["attacker_sim"]

    def test_chain_list_matches_cli(self):
        # The CLI argparser restricts --chain to these four values;
        # if the GUI combobox ever drifts, a fixture for a chain the
        # CLI rejects would surface only at runtime.
        cli_choices = ("BTC", "ETH", "XMR", "SOL")
        assert cli_choices == attacker_sim_gui.CHAINS
