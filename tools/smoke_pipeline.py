"""In-process end-to-end smoke test.

Runs the real watcher, worker, detector, logger, and notifier in this
process. Writes to the real Windows clipboard via ``win32clipboard``
to drive detections. Unlike ``attacker_sim.py`` (which runs in a
separate process and competes with other clipboard-aware software for
ownership), this harness owns the whole flow and gives the pipeline
uninterrupted windows to react.

Usage::

    .venv\\Scripts\\python.exe tools\\smoke_pipeline.py

Prints what it did, waits for the watcher to process each event, and
finishes by dumping ``log.jsonl`` and toast capture counts. Uses an
isolated ``%TEMP%\\clipwarden-smoke`` directory via
``CLIPWARDEN_APPDATA`` so it never touches real user data.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from pathlib import Path

import pywintypes
import win32clipboard
import win32con

SMOKE_DIR = Path(tempfile.gettempdir()) / "clipwarden-smoke"


def _reset_appdata() -> Path:
    if SMOKE_DIR.exists():
        shutil.rmtree(SMOKE_DIR, ignore_errors=True)
    SMOKE_DIR.mkdir(parents=True, exist_ok=True)
    os.environ["CLIPWARDEN_APPDATA"] = str(SMOKE_DIR)
    # Demo mode disables the user-input gate so the harness can drive
    # events while the operator is still touching mouse and keyboard.
    os.environ["CLIPWARDEN_DEMO_MODE"] = "1"
    return SMOKE_DIR


def _write_clipboard(text: str) -> None:
    last_err: Exception | None = None
    for _ in range(5):
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
            time.sleep(0.020)
    raise SystemExit(f"could not write clipboard: {last_err}")


class RecordingNotifier:
    def __init__(self) -> None:
        self.substitutions = []
        self.infos = []

    def notify_substitution(self, event) -> None:
        self.substitutions.append(event)

    def notify_info(self, title, body) -> None:
        self.infos.append((title, body))


PAIRS = [
    (
        "BTC",
        "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4",
        "bc1qrp33g0q5c5txsp9arysrx4k6zdkfs4nce4xj0gdcccefvpysxf3qccfmv3",
    ),
    (
        "ETH",
        "0x52908400098527886E0F7030069857D2E4169EE7",
        "0x8617E340B3D01FA5F11F306F4090FD50E238070D",
    ),
    (
        "SOL",
        "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
    ),
]


def main() -> int:
    appdata = _reset_appdata()
    print(f"[smoke] appdata = {appdata}")

    # Import after env vars are set so paths.appdata_dir() sees them.
    from clipwarden.runtime import build_runtime

    rec = RecordingNotifier()
    rt = build_runtime(notifier=rec)
    rt.start()
    try:
        # Let the pump finish creating its HWND and drain any state
        # the clipboard already held from other processes.
        time.sleep(0.3)
        for chain, before, after in PAIRS:
            print(f"\n[smoke] {chain}: writing BEFORE...")
            _write_clipboard(before)
            # Keep the gap short so Windows Clipboard History and
            # Cloud Clipboard do not inject a value between writes.
            time.sleep(0.08)
            print(f"[smoke] {chain}: writing AFTER ...")
            _write_clipboard(after)
            time.sleep(0.5)

        time.sleep(0.3)
    finally:
        rt.stop()

    log_path = SMOKE_DIR / "log.jsonl"
    print("\n--- smoke results ---")
    print(f"toasts captured: {len(rec.substitutions)}")
    for ev in rec.substitutions:
        print(f"  - {ev.chain}: {ev.before[:12]}... -> {ev.after[:12]}... ({ev.elapsed_ms} ms)")
    if log_path.exists():
        print(f"\nlog.jsonl ({log_path}):")
        for line in log_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            print(f"  {entry}")
    else:
        print("log.jsonl not present (no detections fired)")

    expected = len(PAIRS)
    ok = len(rec.substitutions) == expected
    print(f"\n[smoke] {'PASS' if ok else 'FAIL'}: {len(rec.substitutions)}/{expected} detections")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
