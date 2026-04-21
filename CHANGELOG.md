# Changelog

All notable changes to ClipWarden land here. Uses
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) conventions
loosely. Dates are ISO.

## [Unreleased]

### Added - Phase B (tray UI + packaging)
- System-tray app (`tray.py`) built on `pystray` with `Enable`,
  three-option `Pause` submenu (15 min / 1 hour / Until I resume)
  with auto-resume timer, `Open Config` / `Open Log Folder` /
  `Open History Folder`, `About ClipWarden`, and `Quit` items.
  Icon swaps between normal, disabled, and a 5-second "alert"
  variant after a detection.
- Single-instance guard (`singleton.py`) backed by a named Win32
  mutex. A second launch shows a native MessageBox pointing the
  user at the running tray icon and exits 0.
- About dialog: runs on a dedicated daemon thread so a blocking
  `MessageBox` cannot deadlock the `pystray` event loop.
- Multi-channel alert system (`alert.py`) routing every detection
  through a dispatcher that fans out to:
  - **Popup** - custom topmost Tkinter window on its own daemon
    thread, shows chain + redacted addresses + a "Got it"
    dismissal, bypasses Windows Do Not Disturb.
  - **Sound** - independent `SoundChannel` ringing
    `winsound.MessageBeep`; still fires when the popup is
    disabled or when running headless.
  - **Toast** - the existing `winotify` notifier, still subject
    to DND, kept as a secondary channel for passive awareness.
  - **Tray flash** - swaps the tray icon to an alert red variant
    for 5 seconds, then reverts.
  - **Log** - `log.jsonl` is always appended, regardless of which
    other channels are enabled.
  Each channel can be toggled independently under `config.alert`
  (`popup`, `toast`, `sound`, `tray_flash`, all default `true`).
- `__main__.py` rewritten for tray-by-default with `--headless`
  opt-out, `--version`, and hidden `--install-autostart` /
  `--uninstall-autostart` installer hooks. An outer crash handler
  writes unhandled exceptions to `%APPDATA%\ClipWarden\crash.log`
  so silent failures in a `--noconsole` build leave a trail.
- `build/launcher.py`: PyInstaller entry-point shim that also
  records import-time crashes to the same log, using a stdlib-only
  mirror of `paths.appdata_dir` because the `clipwarden` package
  may not import cleanly at that point.
- `paths.py` is the single source of truth for user-writable
  state: `config.json`, `whitelist.json`, `log.jsonl`, `crash.log`,
  and the opt-in `diagnostic.log` all live under
  `%APPDATA%\ClipWarden\` (Roaming).
- Optional `CLIPWARDEN_DIAGNOSTIC=1` rotating file log under
  `%APPDATA%\ClipWarden\diagnostic.log` (256 KiB x 3 backups).
  Off by default; intended for user-reported-bug repro.
- Icon generator (`tools/gen_icons.py`) producing reproducible
  multi-resolution `icon.ico`, `icon-disabled.ico`, and
  `icon-alert.ico` (256 / 48 / 32 / 16).
- Process-wide per-monitor DPI awareness (v2) applied before any
  window is created, so the tray icon and popup are crisp on
  HiDPI displays.
- Packaging: `build/ClipWarden.spec` (`--onefile --noconsole
  --noupx`, icons bundled, version resource, hidden imports for
  `pystray._win32`, `PIL._tkinter_finder`, `tkinter`, `winsound`,
  `_cffi_backend`), `build/version_info.txt`, `build/installer.iss`
  (Inno Setup 6, per-user install to
  `%LOCALAPPDATA%\Programs\ClipWarden\`, optional autostart task
  that shells out to `--install-autostart`, uninstaller calls
  `--uninstall-autostart` and preserves `%APPDATA%\ClipWarden\`),
  and `tools/gen_checksums.py` for SHA-256 release manifests.
- `build/README.md` documents the full portable-exe + installer
  + checksums workflow and the clean-install smoke test.
- Tests: +102 tests covering the singleton, the tray state
  machine and flash behaviour, each alert channel, the
  dispatcher composition for tray and headless paths, the
  crash-log path on Roaming `%APPDATA%\ClipWarden\`, the opt-in
  diagnostic logger, `enabled_chains` gating at classifier and
  detector layers, whitelist corruption backup, default-
  persistence on missing config / whitelist, the `autostart`
  legacy-key migration, watcher startup handshake and stop-
  timeout guard, and detector reset on tray enable/disable.
  328 tests total.

### Changed
- Version bumped to `1.0.0` across `src/clipwarden/__init__.py`,
  `pyproject.toml`, and `build/version_info.txt`. PyPI
  `Development Status` classifier moved from `3 - Alpha` to
  `4 - Beta`.
- Default run mode is now the tray; the Phase A headless
  behaviour is still reachable via `--headless` and is the mode
  used by CI and the smoke-pipeline harness.
- `Config.enabled_chains` is now honoured at runtime: disabled
  chains are short-circuited in the classifier dispatch instead
  of only in the UI, so a disabled chain produces zero detector
  state and zero alerts.
- `Config.load()` / `Whitelist.load()` now persist defaults to
  disk on missing-file fallback, so a fresh install leaves a
  user-editable `config.json` and `whitelist.json` rather than
  an empty data directory.
- `Whitelist.load()` now backs corrupt files up to
  `whitelist.json.bak-<timestamp>` before falling back to
  empty, matching the config-corruption recovery pattern.
- `Watcher.start()` now blocks until the pump thread confirms
  `AddClipboardFormatListener` succeeded, raising
  `WatcherStartError` on timeout or listener failure. A previous
  `Watcher.stop()` that hit its join timeout now marks the
  instance stopping and refuses subsequent `start()` calls, so
  a wedged previous run cannot race fresh workers.
- Tray enable/disable transitions reset detector state so a
  paused-then-resumed session starts clean.

### Removed
- `Config.autostart` is no longer part of the schema; autostart
  is a per-user Windows Run entry owned by the installer task
  and `--install-autostart` / `--uninstall-autostart` flags.
  Any legacy `"autostart"` field in an existing `config.json`
  is stripped on load and the file is rewritten without it.

### Added - Phase A (runtime foundation)
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
