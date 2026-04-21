"""Attacker simulator for ClipWarden.

Mimics a clipboard-hijack malware family: copies a legitimate address,
waits briefly, then overwrites the clipboard with a different address
of the same chain. When ClipWarden is running, the second write
produces a toast and a ``log.jsonl`` entry.

This is a development tool. It is not packaged in the wheel or the
installer; the README advertises it as a local smoke-test harness for
anyone auditing ClipWarden.

Safety
------
The script writes to the user's real clipboard.
``--i-know-this-is-adversarial`` is required; without it the script
prints a warning and exits without touching the clipboard.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pywintypes
import win32clipboard
import win32con

REPO_ROOT = Path(__file__).resolve().parent.parent


def _fixtures_path() -> Path:
    """Locate ``real_addresses.json`` in both source and frozen builds.

    PyInstaller extracts bundled datas to ``sys._MEIPASS`` at runtime
    and does not ship ``tests/`` in the release tree, so the source
    layout path does not resolve. The attacker-sim spec bundles the
    fixture under an ``attacker_sim_fixtures/`` prefix; look there
    first, then fall back to the source checkout layout so the CLI
    keeps working from ``tools/``.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bundled = Path(meipass) / "attacker_sim_fixtures" / "real_addresses.json"
        if bundled.is_file():
            return bundled
    return REPO_ROOT / "tests" / "fixtures" / "real_addresses.json"


FIXTURES = _fixtures_path()

SAFETY_FLAG = "--i-know-this-is-adversarial"

# Module-level acknowledgement. ``main()`` flips this when the flag is
# present on argv; :func:`_set_clipboard_text` refuses to run while it
# is False so any attempt to embed or import this module (e.g. via
# ``python -c "from attacker_sim import run_substitution; ..."``) is
# rejected at the exact line where a real clipboard write would occur.
# Treat as an internal implementation detail -- the public contract is
# "pass SAFETY_FLAG on argv to main()".
_ACKNOWLEDGED: bool = False


class _SafetyError(RuntimeError):
    """Raised when the adversarial flag has not been acknowledged."""


WARNING_TEXT = (
    """\
ClipWarden attacker_sim: this script writes to your REAL clipboard.

It simulates a clipper: copies one cryptocurrency address, waits a
configurable delay, then overwrites the clipboard with a second
address of the same chain. If you have a pending crypto transaction
in any other app right now, pasting AFTER this script runs will paste
the substituted address.

Pass """
    + SAFETY_FLAG
    + """ to confirm you understand this and want
to proceed.
"""
)

log = logging.getLogger("attacker_sim")


@dataclass(frozen=True)
class ChainPair:
    chain: str
    before: str
    after: str


def _load_addresses_by_chain() -> dict[str, list[str]]:
    # Re-resolve the fixture path on each call so a frozen build that
    # sets ``sys._MEIPASS`` after module import (the packaged GUI
    # does its own ``sys.path`` shuffle at startup) and test doubles
    # that monkeypatch the lookup both see the updated value.
    data = json.loads(_fixtures_path().read_text(encoding="utf-8"))
    by_chain: dict[str, list[str]] = {}
    for e in data.get("entries", []):
        by_chain.setdefault(e["chain"], []).append(e["address"])
    return by_chain


def _pick_pair(chain: str, pool: dict[str, list[str]]) -> ChainPair:
    if chain not in pool or len(pool[chain]) < 2:
        raise SystemExit(f"not enough fixture addresses for chain={chain}")
    return ChainPair(chain=chain, before=pool[chain][0], after=pool[chain][1])


def _set_clipboard_text(text: str) -> None:
    """Write ``text`` as CF_UNICODETEXT, retrying briefly on contention.

    Refuses to touch the clipboard unless the adversarial safety flag
    has been acknowledged via :func:`main`. Importing the module and
    calling this helper directly is rejected rather than merely
    discouraged.
    """
    if not _ACKNOWLEDGED:
        raise _SafetyError(
            "attacker_sim refused to write the clipboard: "
            f"pass {SAFETY_FLAG} on the command line to acknowledge "
            "adversarial behaviour."
        )
    last_err: Exception | None = None
    for _ in range(3):
        try:
            win32clipboard.OpenClipboard(0)
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
                return
            finally:
                win32clipboard.CloseClipboard()
        except pywintypes.error as e:
            last_err = e
            time.sleep(0.010)
    raise SystemExit(f"could not open clipboard to write: {last_err}")


def _read_clipboard_text() -> str | None:
    """Return the clipboard's current Unicode text, or ``None``."""
    try:
        win32clipboard.OpenClipboard(0)
    except pywintypes.error:
        return None
    try:
        try:
            data = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
        except (pywintypes.error, TypeError):
            return None
        return data if isinstance(data, str) else None
    finally:
        with contextlib.suppress(pywintypes.error):
            win32clipboard.CloseClipboard()


def run_substitution(pair: ChainPair, delay_ms: int) -> None:
    print(f"[sim] writing BEFORE ({pair.chain}): {pair.before}", flush=True)
    _set_clipboard_text(pair.before)
    back = _read_clipboard_text()
    print(f"[sim] clipboard now holds: {back}", flush=True)

    print(f"[sim] sleeping {delay_ms} ms before substitution...", flush=True)
    time.sleep(delay_ms / 1000.0)

    print(f"[sim] writing AFTER  ({pair.chain}): {pair.after}", flush=True)
    _set_clipboard_text(pair.after)
    back = _read_clipboard_text()
    print(f"[sim] clipboard now holds: {back}", flush=True)
    print("[sim] expected outcome: ClipWarden toast + log.jsonl entry", flush=True)


def run_scenarios(delay_ms: int, pool: dict[str, list[str]]) -> None:
    for chain in ("BTC", "ETH", "XMR", "SOL"):
        if chain not in pool or len(pool[chain]) < 2:
            print(f"[sim] skipping {chain}: not enough fixture addresses", flush=True)
            continue
        print(f"\n--- {chain} substitution ---", flush=True)
        run_substitution(_pick_pair(chain, pool), delay_ms)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="attacker_sim",
        description=(
            "Simulate a clipboard hijack against a running ClipWarden. "
            f"Requires {SAFETY_FLAG} because it writes to the real clipboard."
        ),
    )
    p.add_argument("--chain", choices=("BTC", "ETH", "XMR", "SOL"), default="BTC")
    p.add_argument(
        "--delay-ms",
        type=int,
        default=500,
        help="Pause between BEFORE and AFTER writes (ms).",
    )
    p.add_argument(
        "--scenarios",
        action="store_true",
        help="Run one substitution per supported chain instead of a single pair.",
    )
    p.add_argument(
        SAFETY_FLAG,
        dest="acknowledged",
        action="store_true",
        help="Required. Confirms you understand this touches your real clipboard.",
    )
    args = p.parse_args(argv)

    if not args.acknowledged:
        print(WARNING_TEXT, file=sys.stderr)
        return 2

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    pool = _load_addresses_by_chain()

    global _ACKNOWLEDGED
    _ACKNOWLEDGED = True
    try:
        if args.scenarios:
            run_scenarios(args.delay_ms, pool)
        else:
            run_substitution(_pick_pair(args.chain, pool), args.delay_ms)
    finally:
        _ACKNOWLEDGED = False

    return 0


if __name__ == "__main__":
    sys.exit(main())
