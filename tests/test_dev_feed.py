from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make tools/ importable as a package-less module path, same way a
# developer would run "python tools/dev_feed.py scenarios/foo.yml".
_TOOLS = Path(__file__).resolve().parent.parent / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

import dev_feed  # noqa: E402

SCENARIOS = Path(__file__).resolve().parent / "fixtures" / "scenarios"


def test_parse_minimal_scenario() -> None:
    s = dev_feed.parse_scenario(
        {
            "scenario": "tiny",
            "events": [
                {"ts": 0, "input_ts": 0, "text": "abc"},
            ],
        }
    )
    assert s.name == "tiny"
    assert s.window_ms == dev_feed.DEFAULT_WINDOW_MS
    assert s.expect is None
    assert len(s.events) == 1


def test_btc_substitution_scenario_fires() -> None:
    s = dev_feed.load_scenario(SCENARIOS / "btc_substitution.yml")
    results = dev_feed.run_scenario(s)
    assert results[0] is None
    assert results[1] is not None
    assert results[1].chain == "BTC"
    assert results[1].whitelisted is False


def test_deliberate_recopy_no_event() -> None:
    s = dev_feed.load_scenario(SCENARIOS / "deliberate_recopy.yml")
    results = dev_feed.run_scenario(s)
    assert all(r is None for r in results)


def test_whitelisted_pair_emits_skip() -> None:
    s = dev_feed.load_scenario(SCENARIOS / "whitelisted_pair.yml")
    results = dev_feed.run_scenario(s)
    assert results[1] is not None
    assert results[1].whitelisted is True


def test_main_success_exit_code(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    rc = dev_feed.main([str(SCENARIOS / "btc_substitution.yml")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "summary: detection" in out


def test_main_fail_when_expectation_mismatches(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yml"
    bad.write_text(
        """
scenario: wrong expect
events:
  - {ts: 0, input_ts: 0, text: "not-an-address"}
expect: detection
""",
        encoding="utf-8",
    )
    rc = dev_feed.main([str(bad)])
    assert rc == 1


def test_main_fails_on_missing_file(tmp_path: Path) -> None:
    rc = dev_feed.main([str(tmp_path / "nope.yml")])
    assert rc == 2


@pytest.mark.parametrize(
    "raw",
    [
        [],
        "not a mapping",
        {"events": "not a list"},
        {"events": [{"ts": 0, "input_ts": 0}]},  # missing text
        {"events": [{"ts": 0, "input_ts": 0, "text": 5}]},  # bad text type
        {"events": [], "window_ms": 0},  # non-positive window
        {"events": [], "window_ms": -5},
        {"events": [], "expect": "garbage"},
        {"events": [], "whitelist": "nope"},
        {"events": [], "whitelist": ["not-a-dict"]},
        {"events": [], "whitelist": [{"chain": "BTC"}]},  # missing address
    ],
)
def test_parse_rejects_malformed(raw) -> None:
    with pytest.raises(dev_feed.ScenarioError):
        dev_feed.parse_scenario(raw)


def test_parse_accepts_whitelist() -> None:
    s = dev_feed.parse_scenario(
        {
            "events": [{"ts": 0, "input_ts": 0, "text": "hi"}],
            "whitelist": [{"chain": "BTC", "address": "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"}],
        }
    )
    assert ("BTC", "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa") in s.whitelist


def test_summary_helpers() -> None:
    from clipwarden.detector import DetectionEvent

    assert dev_feed._summarize([None, None]) == "no_event"
    ev = DetectionEvent(
        ts_ms=1, chain="BTC", before="a", after="b", elapsed_ms=10, whitelisted=False
    )
    assert dev_feed._summarize([None, ev]) == "detection"
    wl = DetectionEvent(
        ts_ms=1, chain="BTC", before="a", after="b", elapsed_ms=10, whitelisted=True
    )
    assert dev_feed._summarize([None, wl]) == "whitelisted_skip"
