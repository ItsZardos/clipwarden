# ClipWarden

A small Windows background tool that watches the clipboard for cryptocurrency
addresses and fires an alert when something swaps the address out between copy
and paste. It targets "clipper" malware: software that silently replaces a
copied crypto address with the attacker's address before the victim pastes.


Supported right now: Bitcoin, Ethereum (and any EVM chain that shares the ETH
address format), Solana, Monero.

> Status: v0.1, pre-release. The core detection pipeline is being built in
> the open. First tagged release will be `v1.0.0` with a published SHA-256
> checksum for the binary and the threat model written up. Until then, treat
> everything as a work in progress.

## Why I built this

I got curious about clipper malware after reading a handful of incident
reports where someone lost five or six figures of crypto because a
background process on their machine edited their clipboard between copy and
paste. Most antivirus products don't catch clippers because the behaviour
looks ordinary: a program reading and writing clipboard contents is not,
by itself, malicious.

The defensive angle seemed underserved. If the attacker's move is to
substitute the clipboard in the moment between copy and paste, then the
defender's move is to notice when that substitution happens. That's the
whole premise of ClipWarden.

## What it does, and what it does not

It does:

- Run in the system tray as a small always-on process.
- Hook the Windows clipboard change event (`AddClipboardFormatListener`)
  and inspect every new clipboard value.
- Classify clipboard text that looks like a supported crypto address,
  using real checksum validation (Base58Check, Bech32, EIP-55, Ed25519
  on-curve for Solana) rather than regex alone.
- Flag and alert when the clipboard changes from one valid address to a
  different valid address of the same type within a short window, with no
  user input recorded in between.
- Log detections to a local JSONL file you own. No network, ever.

It does not:

- Remove malware. ClipWarden is a detector, not a remediator.
- Identify which process edited the clipboard. That is on the v2 roadmap.
- Work on macOS or Linux. Windows only for the time being.
- Phone home. There is no telemetry and no auto-update check. The install
  is deliberately offline so you can verify that yourself with a packet
  capture if you want.

A longer write-up lives in [`docs/threat-model.md`](docs/threat-model.md).

## Install

Two options once v1.0.0 ships:

1. Portable `ClipWarden.exe` from the GitHub Releases page. Unzip,
   double-click.
2. Inno Setup installer (`ClipWarden-Setup.exe`) that registers a Start
   Menu entry and optionally adds a "Start with Windows" shortcut.

Both builds will have SHA-256 checksums published alongside the release.
Verify them. The binaries are not code-signed for v1.0.0 (that's on the
v1.1 list), so Windows SmartScreen may warn you the first time you run it.
Click "More info" then "Run anyway" if and only if the hash matches.

## Running from source

```powershell
git clone https://github.com/ItsZardos/clipwarden.git
cd clipwarden
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --require-hashes -r requirements.txt
pip install --require-hashes -r requirements-dev.txt
pip install -e . --no-deps
python -m clipwarden
```

Tests:

```powershell
pytest
```

Lint and format check:

```powershell
ruff check .
ruff format --check .
```

## Self-testing

There is a small attacker simulator in [`tools/attacker_sim.py`](tools/attacker_sim.py)
(lands in Week 2) that mimics what a clipper does: copy a legitimate
address, wait a configurable delay, overwrite the clipboard with a second
address. Run it while ClipWarden is active and you should see a detection
fire. I use it for local smoke testing and anyone auditing this project
can use it to verify the tool actually works on their machine.

## Configuration

User config, whitelist, and detection log all live under
`%APPDATA%\ClipWarden\`:

- `config.json` - which chains to monitor, detection sensitivity, autostart.
- `whitelist.json` - addresses you've marked as safe (exact match only).
- `log.jsonl` - one detection event per line, JSON. Rotates at 10MB with
  3 backups.

Delete any of these and ClipWarden will recreate them with defaults on
next launch.

## License

MIT. See [`LICENSE`](LICENSE).

## Author

Ethan Tharp. [ethantharp.dev](https://ethantharp.dev).
