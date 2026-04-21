# Changelog

All notable changes to ClipWarden land here. Uses
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) conventions
loosely. Dates are ISO.

## [Unreleased]

### Added
- Project scaffolding: package layout, LICENSE, pinned + hashed
  dependencies, ruff + pytest configs, CI on windows-latest.
- Address classifier with strongest-checksum-first dispatch (BTC ->
  ETH -> XMR -> SOL).
- Validators: Base58Check for BTC P2PKH/P2SH, Bech32 and Bech32m
  (BIP-173 + BIP-350) for BTC segwit and Taproot, EIP-55 for ETH
  (accepts mixed-case with valid checksum, and pure-lower / pure-upper
  as "no checksum claimed" per spec), CryptoNote Base58 + Keccak-256
  checksum for XMR standard and subaddress formats, and Base58 +
  Ed25519 on-curve check for SOL.
- Vendored pure-Python Keccak-256 used only for EIP-55 display
  checksums and XMR checksum verification. Not intended as a
  cryptographic primitive elsewhere.
- Test corpora: `tests/fixtures/real_addresses.json` (real mainnet
  positives with provenance) and `tests/fixtures/false_positives.txt`
  (real git SHAs plus synthetic API-key-shaped tokens, UUIDs, random
  hex/base64, and adversarial checksum-mutated addresses).
- `tools/gen_fixtures.py`: seeded generator for the synthetic half of
  the FP corpus.
- User-data paths resolver (`paths.py`) for `%APPDATA%\ClipWarden\`
  with a `CLIPWARDEN_APPDATA` env override for tests.
- Config module (`config.py`): frozen dataclass, strict JSON schema
  validation, atomic writes, and back-up-then-default recovery for
  corrupt files so a silently-disabled monitor can't happen.
- Exact-address whitelist (`whitelist.py`). ETH and BTC bech32 are
  normalised to lowercase for lookup; base58 BTC, SOL, and XMR are
  case-sensitive.
- Substitution-time detector (`detector.py`): pure state machine,
  emits `DetectionEvent`, caller handles IO. Configurable window,
  `GetLastInputInfo`-style user-input suppression, cross-chain
  transitions never fire, non-address clipboard content preserves
  prior state (so laundered A -> junk -> B still alerts).
- Detection logger (`logger.py`): stdlib `RotatingFileHandler` wrapper
  (10 MB x 3 backups) emitting a stable JSONL schema.
  `kind="whitelisted_skip"` lines are recorded for debugging when a
  detection targets a whitelisted address.
- `tools/dev_feed.py`: YAML replay harness to run hand-written
  clipboard scenarios through the classifier+detector. Ships with
  three scenario fixtures covering the three main outcomes.
- Hypothesis property tests for the detector covering first-event,
  cross-chain, user-input-suppression, idempotency, monotonicity, and
  no-state-leak invariants.
- Win32 clipboard watcher (`watcher.py`): message-only window on a
  dedicated pump thread with `AddClipboardFormatListener`, worker
  thread draining a bounded drop-oldest queue, seq-based self-write
  suppression hook (`mark_self_write`) wired up ahead of the v1.1
  "Restore previous address" action.
- Windows toast notifier (`notifier.py`): thin `winotify` wrapper with
  an `enabled` toggle, head/tail redaction of addresses in the toast
  body, and best-effort failure handling so a broken toast stack never
  kills the worker.
- Autostart helper (`autostart.py`): idempotent enable/disable of the
  per-user `HKCU\...\Run` key, no-op in dev (non-frozen) mode so
  developers don't accidentally wire `python.exe` into boot.
- Runtime composition (`runtime.py`): single `start()`/`stop()` surface
  that assembles watcher + detector + logger + notifier, with
  per-stage bounded shutdown timeouts so a wedge in any one component
  can't hang exit. Translates `GetLastInputInfo` (tick-count frame)
  into the monotonic frame the detector expects, guarded against the
  ~49.7-day tick rollover by sampling both clocks per call.
- `CLIPWARDEN_DEMO_MODE` env var disables the user-input suppression
  gate for local smoke harnesses on interactive sessions. Never set
  in a real deployment.
- Headless entry point (`__main__.py`): `python -m clipwarden` starts
  the runtime and blocks on Ctrl-C; `--version` prints the banner and
  exits without touching the clipboard. `--tray` is reserved for the
  forthcoming tray entry point.
- `tools/attacker_sim.py`: CLI clipboard-hijack simulator that refuses
  to run without `--i-know-this-is-adversarial`, prints a clear
  warning explaining what the script does, and supports `--scenarios`
  to exercise all four supported chains against a running ClipWarden.
- `tools/smoke_pipeline.py`: in-process end-to-end smoke harness that
  drives the real watcher/worker/detector/logger with tight timing
  control, useful when external clipboard managers on the dev host
  make subprocess-based smoke tests flaky.
- Tests: 33 new unit / integration tests covering watcher lifecycle
  and queue behaviour (13), notifier redaction and failure paths (7),
  autostart registry operations against a fake winreg (7), and
  runtime integration feeding substitution + whitelisted-skip +
  cross-chain + user-input suppression + disabled-toast scenarios
  through the full pipeline (6). 226 tests total.
