"""Safety-flag enforcement for ``tools/attacker_sim.py``.

The simulator writes to the user's real clipboard. The safety flag
must be impossible to bypass by importing the module and calling
helpers directly; the guard lives inside ``_set_clipboard_text`` so
any code path that ends up writing the clipboard trips over it.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_TOOLS = Path(__file__).resolve().parent.parent / "tools"
_SPEC = importlib.util.spec_from_file_location("attacker_sim", _TOOLS / "attacker_sim.py")
assert _SPEC is not None
assert _SPEC.loader is not None
attacker_sim = importlib.util.module_from_spec(_SPEC)
sys.modules["attacker_sim"] = attacker_sim
_SPEC.loader.exec_module(attacker_sim)


def test_imported_module_starts_unacknowledged():
    """Fresh import must not permit clipboard writes."""
    assert attacker_sim._ACKNOWLEDGED is False


def test_set_clipboard_text_refuses_without_flag():
    """Direct helper call without the flag raises _SafetyError."""
    attacker_sim._ACKNOWLEDGED = False
    with pytest.raises(attacker_sim._SafetyError) as excinfo:
        attacker_sim._set_clipboard_text("anything")
    assert attacker_sim.SAFETY_FLAG in str(excinfo.value)


def test_run_substitution_refuses_without_flag():
    """High-level entry points route through the same guard."""
    attacker_sim._ACKNOWLEDGED = False
    pair = attacker_sim.ChainPair(chain="BTC", before="a", after="b")
    with pytest.raises(attacker_sim._SafetyError):
        attacker_sim.run_substitution(pair, delay_ms=0)


def test_main_without_flag_prints_warning_and_exits_nonzero(capsys):
    """The CLI contract: missing flag -> stderr warning + exit code 2."""
    rc = attacker_sim.main([])
    assert rc == 2
    captured = capsys.readouterr()
    assert attacker_sim.SAFETY_FLAG in captured.err
    assert "REAL clipboard" in captured.err
    # The flag was never flipped on.
    assert attacker_sim._ACKNOWLEDGED is False


def test_main_restores_flag_after_run(monkeypatch):
    """The acknowledgement window is scoped to main(); it never leaks.

    Even when ``run_*`` raises, the ``finally`` in ``main()`` must
    reset ``_ACKNOWLEDGED`` so a subsequent import-level call cannot
    ride on the previous invocation.
    """
    attacker_sim._ACKNOWLEDGED = False

    def boom(*_args, **_kwargs):
        raise RuntimeError("simulated runtime failure")

    monkeypatch.setattr(attacker_sim, "run_substitution", boom)
    monkeypatch.setattr(
        attacker_sim,
        "_load_addresses_by_chain",
        lambda: {"BTC": ["a", "b"]},
    )
    with pytest.raises(RuntimeError):
        attacker_sim.main([attacker_sim.SAFETY_FLAG])
    assert attacker_sim._ACKNOWLEDGED is False
