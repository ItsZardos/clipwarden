"""Replay hand-written clipboard scenarios through the detector.

Scenario format (YAML). Comments are your friend; this file is meant
to be edited by a human writing test cases:

    scenario: basic btc clipper
    window_ms: 1000
    # Optional whitelisted pairs. Any (chain, address) listed here will
    # emit a whitelisted_skip event instead of a detection.
    whitelist:
      - {chain: BTC, address: bc1qrp33g0q5c5txsp9arysrx4k6zdkfs4nce4xj0gdcccefvpysxf3qccfmv3}
    events:
      - {ts: 0,   input_ts: 0, text: "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"}
      - {ts: 500, input_ts: 0, text: "bc1qrp33g0q5c5txsp9arysrx4k6zdkfs4nce4xj0gdcccefvpysxf3qccfmv3"}
    expect: whitelisted_skip   # or detection, or no_event

Only ``events`` is required. ``ts``, ``input_ts`` and ``text`` on each
event are required. ``expect`` is optional and is checked if present.

Run::

    python tools/dev_feed.py tests/fixtures/scenarios/basic_btc.yml
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Allow "python tools/dev_feed.py ..." without installing the package.
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import yaml  # noqa: E402

from clipwarden.detector import DetectionEvent, Detector  # noqa: E402

DEFAULT_WINDOW_MS = 1000


@dataclass
class ScenarioEvent:
    ts_ms: int
    input_ts_ms: int
    text: str


@dataclass
class Scenario:
    name: str
    window_ms: int
    whitelist: set[tuple[str, str]]
    events: list[ScenarioEvent]
    expect: str | None


class ScenarioError(ValueError):
    pass


def _require(d: dict[str, Any], key: str, typ: type) -> Any:
    if key not in d:
        raise ScenarioError(f"missing field: {key}")
    v = d[key]
    if not isinstance(v, typ):
        raise ScenarioError(f"field {key!r} must be {typ.__name__}, got {type(v).__name__}")
    return v


def parse_scenario(raw: dict[str, Any]) -> Scenario:
    if not isinstance(raw, dict):
        raise ScenarioError("scenario root must be a mapping")
    name = str(raw.get("scenario", "<unnamed>"))
    window_ms = int(raw.get("window_ms", DEFAULT_WINDOW_MS))
    if window_ms <= 0:
        raise ScenarioError("window_ms must be positive")

    wl_raw = raw.get("whitelist", []) or []
    if not isinstance(wl_raw, list):
        raise ScenarioError("whitelist must be a list")
    whitelist: set[tuple[str, str]] = set()
    for item in wl_raw:
        if not isinstance(item, dict):
            raise ScenarioError("each whitelist entry must be a mapping")
        chain = _require(item, "chain", str)
        address = _require(item, "address", str)
        whitelist.add((chain, address))

    events_raw = _require(raw, "events", list)
    events: list[ScenarioEvent] = []
    for item in events_raw:
        if not isinstance(item, dict):
            raise ScenarioError("each event must be a mapping")
        events.append(
            ScenarioEvent(
                ts_ms=int(_require(item, "ts", int)),
                input_ts_ms=int(_require(item, "input_ts", int)),
                text=_require(item, "text", str),
            )
        )

    expect = raw.get("expect")
    if expect is not None and expect not in {"detection", "whitelisted_skip", "no_event"}:
        raise ScenarioError(f"invalid expect: {expect!r}")

    return Scenario(
        name=name,
        window_ms=window_ms,
        whitelist=whitelist,
        events=events,
        expect=str(expect) if expect else None,
    )


def load_scenario(path: Path) -> Scenario:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return parse_scenario(raw)


def run_scenario(scenario: Scenario) -> list[DetectionEvent | None]:
    def is_wl(chain: str, address: str) -> bool:
        return (chain, address) in scenario.whitelist

    det = Detector(scenario.window_ms, is_whitelisted=is_wl)
    out: list[DetectionEvent | None] = []
    for ev in scenario.events:
        out.append(det.observe(ev.text, ev.ts_ms, ev.input_ts_ms))
    return out


def _summarize(results: list[DetectionEvent | None]) -> str:
    fired = [e for e in results if e is not None]
    if not fired:
        return "no_event"
    last = fired[-1]
    return "whitelisted_skip" if last.whitelisted else "detection"


def _format_result(idx: int, ev: DetectionEvent | None) -> str:
    if ev is None:
        return f"  [{idx}] -> None"
    kind = "whitelisted_skip" if ev.whitelisted else "detection"
    return (
        f"  [{idx}] -> {kind} chain={ev.chain} elapsed_ms={ev.elapsed_ms}\n"
        f"       before={ev.before}\n"
        f"       after ={ev.after}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Replay a clipboard scenario through the detector."
    )
    parser.add_argument("scenario", type=Path, help="path to YAML scenario file")
    args = parser.parse_args(argv)

    try:
        scenario = load_scenario(args.scenario)
    except (OSError, ScenarioError, yaml.YAMLError) as exc:
        print(f"error loading scenario: {exc}", file=sys.stderr)
        return 2

    results = run_scenario(scenario)
    print(f"scenario: {scenario.name}")
    print(f"window_ms: {scenario.window_ms}")
    for i, r in enumerate(results):
        print(_format_result(i, r))
    summary = _summarize(results)
    print(f"summary: {summary}")

    if scenario.expect and scenario.expect != summary:
        print(f"FAIL: expected {scenario.expect}, got {summary}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
