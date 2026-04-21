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
