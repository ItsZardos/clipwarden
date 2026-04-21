# ClipWarden

<p align="center"><img src="assets/demo.gif" alt="ClipWarden detecting a clipboard substitution" width="720"/></p>

A small Windows background tool that watches the clipboard for
cryptocurrency addresses and fires an alert when something swaps the
address out between copy and paste. It targets "clipper" malware:
software that silently replaces a copied crypto address with the
attacker's address before the victim pastes.

Supported chains: **Bitcoin, Ethereum** (and any EVM chain that shares
the ETH address format), **Solana, Monero**.

> Status: v1.0.0. Portable exe + per-user Inno Setup installer, both
> with published SHA-256 checksums. Windows only.

## Why I built this

Clipper malware is the slow, patient way to rob someone holding crypto.
A background process reads your clipboard, sees a valid wallet address,
and replaces it with the attacker's before you paste. Most AV products
don't catch clippers because the behaviour looks ordinary: reading and
writing the clipboard is not, by itself, malicious.

The defensive angle seemed underserved. If the attacker's move is to
substitute the clipboard in the moment between copy and paste, then the
defender's move is to notice when that substitution happens. That is
the entire premise of ClipWarden.

A longer write-up lives in [`docs/threat-model.md`](docs/threat-model.md).

## What it does, and what it does not

It does:

- Run in the system tray as a small always-on process.
- Hook the Windows clipboard change event
  (`AddClipboardFormatListener`) and inspect every new clipboard value.
- Classify clipboard text that looks like a supported crypto address
  using **real checksum validation** (Base58Check, Bech32/Bech32m,
  EIP-55, Ed25519 on-curve for Solana), not regex alone.
- Flag and alert when the clipboard changes from one valid address to
  a different valid address of the same type within a short window,
  with no user keystroke or click in between.
- Alert through **four independent channels** so one Do-Not-Disturb
  setting or one broken subsystem cannot silence a detection: topmost
  popup, system sound, tray icon flash, Windows toast. Every channel
  is configurable; every detection is always written to an
  append-only audit log.
- Store everything locally under `%APPDATA%\ClipWarden\`. No network,
  ever.

It does not:

- Remove malware. ClipWarden is a detector, not a remediator.
- Identify which process edited the clipboard. That is on the v2
  roadmap.
- Defend against kernel-mode clippers, infostealers that exfiltrate
  your seed phrase or pre-copied addresses, or social engineering in
  which an attacker convinces you to type their address. See
  [`docs/threat-model.md`](docs/threat-model.md) for the full
  out-of-scope list.
- Run on macOS or Linux. Windows only for the time being.
- Phone home. No telemetry, no auto-update check. You can verify that
  with a packet capture.

## Install

Two shipped options. Pick one.

1. **Installer** (recommended). Download `ClipWarden-1.0.0.exe` from
   the GitHub Releases page and run it. Per-user, no admin required.
   It installs the binary as `ClipWarden.exe` under
   `%LOCALAPPDATA%\Programs\ClipWarden\`, creates a Start Menu entry,
   and offers an optional "Start with Windows" checkbox (unchecked by
   default).
2. **Portable**. Download `ClipWarden-Portable.exe` from the same
   Releases page. No install - double-click to launch.

Both artifacts have SHA-256 checksums published alongside the
release. Verify them:

```powershell
Get-FileHash .\ClipWarden-1.0.0.exe -Algorithm SHA256
Get-FileHash .\ClipWarden-Portable.exe -Algorithm SHA256
```

The binaries are not code-signed in v1.0.0, so Windows SmartScreen may
warn you on first launch. Click "More info" → "Run anyway" **only if
the hash matches** the published value.

## Using the tray

Right-click the tray icon for the full menu:

- **Enable** - toggle monitoring without quitting.
- **Pause** - one-shot pauses: 15 minutes, 1 hour, or "Until I resume."
  Auto-resumes on the timed options; **Resume now** ends a pause
  early.
- **Open Config** / **Open Log Folder** / **Open History Folder** -
  jump straight to `%APPDATA%\ClipWarden\` for config tweaks or log
  review.
- **About ClipWarden** - version banner.
- **Quit ClipWarden** - clean shutdown.

When a substitution is detected:

1. The **topmost popup** pops over whatever you were doing with the
   chain, a redacted before/after pair, and a **Got it** button. Tk
   window, not a shell toast, so Focus Assist / Do Not Disturb does
   not suppress it.
2. The **system sound** plays (optional).
3. The **tray icon** flashes red for ~5 seconds.
4. A **Windows toast** also fires (optional, respects Focus Assist).
5. An entry is appended to `log.jsonl` regardless of which channels
   are enabled. The log is the durable audit trail.

## Configuration

Everything user-writable lives under `%APPDATA%\ClipWarden\`:

- `config.json` - runtime settings (see below).
- `whitelist.json` - addresses you have marked as safe. Exact-match;
  ETH and BTC bech32 are lowercased for comparison, BTC base58, SOL,
  and XMR are case-sensitive.
- `log.jsonl` - append-only detection audit trail, one JSON object
  per line. Rotates at 10 MB with 3 backups.
- `crash.log` - unhandled-exception traces, if any.

`config.json` default shape:

```json
{
  "enabled_chains": ["BTC", "ETH", "XMR", "SOL"],
  "substitution_window_ms": 1000,
  "user_input_grace_ms": 750,
  "notifications_enabled": true,
  "alert": {
    "popup": true,
    "toast": true,
    "sound": true,
    "tray_flash": true
  }
}
```

- `substitution_window_ms`: maximum gap between BEFORE and AFTER that
  still counts as a substitution (100–10000 ms).
- `user_input_grace_ms`: reserved; the detector currently uses
  `GetLastInputInfo` directly for the between-copies gate.
- `notifications_enabled`: legacy kill-switch. When `false`, every
  alert channel is suppressed; `log.jsonl` still gets the entry.
- `alert.popup` / `alert.toast` / `alert.sound` / `alert.tray_flash`:
  per-channel toggles. Log always fires.

Unknown keys are rejected at load time. If `config.json` is
malformed, it is renamed to `config.json.bak-<timestamp>` and
defaults are reinstated on the fly - a silently-disabled monitor is
a worse failure mode than a reverted setting. Delete any of the
files above and ClipWarden will recreate them with defaults on next
launch.

Autostart is not a config key. It is a per-user Windows Run entry
owned by the installer task and the `--install-autostart` /
`--uninstall-autostart` flags on `ClipWarden.exe`. Any legacy
`"autostart"` field left in an old `config.json` is stripped on
load and the file is rewritten without it, so upgrading from an
earlier build does not produce a validation error.

## Running from source

```powershell
git clone https://github.com/ItsZardos/clipwarden.git
cd clipwarden
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --require-hashes -r requirements.txt
pip install --require-hashes -r requirements-dev.txt
pip install -e . --no-deps
python -m clipwarden              # tray
python -m clipwarden --headless   # no tray; blocks on Ctrl-C
```

Build a portable exe and installer:

```powershell
pyinstaller build\ClipWarden.spec --clean
& "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" build\installer.iss
python tools\gen_checksums.py
```

See [`build/README.md`](build/README.md) for the full packaging
workflow.

Tests, lint, format:

```powershell
pytest
ruff check .
ruff format --check .
```

## Self-testing

`tools\attacker_sim.py` ships a clipper simulator that refuses to run
without `--i-know-this-is-adversarial`:

```powershell
.\.venv\Scripts\python tools\attacker_sim.py --i-know-this-is-adversarial
```

With ClipWarden running, you should see the popup + sound + tray
flash + toast, and a new line in `log.jsonl`. Anyone auditing this
project can use the same script to verify the tool works on their
machine.

## Troubleshooting

**"Popup did not fire when I re-ran `attacker_sim` a few seconds
later."** Two common causes:

- You forgot `--i-know-this-is-adversarial`; the simulator prints the
  warning and exits without touching the clipboard.
- Your keystrokes between the two copies tripped the user-input gate.
  The detector suppresses alerts when keyboard or mouse activity
  happened between BEFORE and AFTER, because that pattern looks like
  a deliberate recopy. Run the simulator, then keep your hands off
  the keyboard and mouse until the popup fires.

**"I want to capture exactly what happened on a silent failure."** Set
the `CLIPWARDEN_DIAGNOSTIC` environment variable to `1`
(also accepts `true` / `yes` / `on`, case-insensitive) before
launching ClipWarden:

```powershell
$env:CLIPWARDEN_DIAGNOSTIC = "1"
Start-Process "$env:LOCALAPPDATA\Programs\ClipWarden\ClipWarden.exe"
```

A rotating INFO+ trace appears at
`%APPDATA%\ClipWarden\diagnostic.log` (256 KiB × 3 backups). Share
that file with a bug report.

**"Task Manager shows two `ClipWarden.exe` processes for one
launch."** That is the PyInstaller one-file bootloader plus the
Python child. Normal, not a singleton bug. Quitting from the tray
exits both.

## License

MIT. See [`LICENSE`](LICENSE). Copyright (c) 2026 Ethan Tharp.

## Author

Ethan Tharp. [ethantharp.dev](https://ethantharp.dev).
