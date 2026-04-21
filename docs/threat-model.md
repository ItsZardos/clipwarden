# Threat Model

ClipWarden defends against one narrow, very real attack: **clipboard
hijacking of cryptocurrency addresses by userland malware**. This doc
spells out what that means, what ClipWarden cannot help with, and
what a realistic adversary looks like.

## Asset

The address you are about to paste into a wallet, exchange, or DeFi
front-end when sending or receiving cryptocurrency. If the attacker
replaces that address between your copy and your paste, the next
confirmation step sends funds to the attacker. Transactions on
supported chains (BTC, ETH and EVM look-alikes, XMR, SOL) are
irreversible once confirmed.

## Adversary

**In scope** - a userland process running with the same privileges as
the user, for example:

- An installer bundle or game cheat that shipped a clipper as a
  side-effect payload.
- A browser extension with clipboard read/write permissions abusing
  that surface.
- A Python / AutoHotkey / batch script dropped by a phishing lure.
- An `AddClipboardFormatListener`-based watcher that replaces the
  clipboard in the milliseconds between your Ctrl-C and Ctrl-V.

The attacker is assumed to have:

- The ability to open, read, write, and close the clipboard via the
  normal Win32 API surface.
- No kernel driver, no injected window hook below the user-mode
  message pump, no code-signing.
- No ability to subvert ClipWarden's binary or config files beyond
  what any other userland process has (i.e. we do not model an
  attacker that rewrites `config.json` while we are running).

**Out of scope** - ClipWarden will not protect you from:

- **Kernel-mode clippers.** A rootkit that filters clipboard events
  below the user-mode listener can replay a clipboard change that
  never fires `WM_CLIPBOARDUPDATE` at userland. ClipWarden cannot
  observe what the OS does not deliver.
- **Infostealers that exfiltrate addresses.** An attacker who dumps
  your saved wallets, seed phrases, or address book and uses those
  offline never touches your clipboard. Nothing to detect.
- **Pre-copy substitution.** A malicious wallet UI that shows you
  address A while the copy button writes address B. ClipWarden sees
  only B arrive in the clipboard and has no reference for A.
- **Social engineering.** Someone types the attacker's address into
  Telegram and you copy it yourself. The address is "valid" from
  ClipWarden's perspective; there is no substitution event to detect.
- **Account takeover / session hijack.** Once an attacker is logged
  into your exchange, they do not need your clipboard.
- **Physical access.** Evil-maid, hardware keyloggers, monitor-capture.
- **Platforms other than Windows.** macOS, Linux, mobile are all
  out of scope for v1.0.

## Attack model in one line

The attacker sees a new address land on the clipboard, checks whether
it is a supported format, and overwrites it with their own of the
same format within a fraction of a second.

## Defence model in one line

ClipWarden subscribes to every clipboard change, classifies each
value against real checksum validators, and alerts when two *valid*,
*different*, *same-chain* addresses land within a configurable window
(default 1000 ms) with no user input in between.

See [`detection-model.md`](detection-model.md) for the full detection
rule, the non-resets (laundered substitution A → junk → B still
alerts), the cross-chain handling, and the user-input gate.

## What a successful attack vs. a detected one looks like

| | No ClipWarden | With ClipWarden |
| --- | --- | --- |
| You copy `bc1q…your`. | Clipboard holds `bc1q…your`. | Same. |
| Clipper replaces it with `bc1q…attack`. | Clipboard now holds `bc1q…attack`. | Detector sees same-chain substitution inside the 1 s window. |
| You paste. | You send to the attacker. | Topmost popup fires, sound plays, tray flashes, toast fires. You verify the address before hitting send. |
| Audit trail. | None. | `log.jsonl` has an entry with BEFORE, AFTER, chain, elapsed_ms, whitelisted flag. |

## Residual risk even when ClipWarden is installed

- **You ignore the popup and paste anyway.** The popup is loud for a
  reason; the tool cannot save you from dismissing it and sending
  funds. The addresses in the popup are truncated with `…` in the
  middle specifically so you glance at head + tail before clicking
  Got it.
- **The clipper is faster than your paste but slower than the
  `substitution_window_ms`.** The default 1000 ms window is a
  heuristic; a clipper waiting > 1 s before overwriting will not
  trip the detector. Increase the window in `config.json` at the
  cost of more false positives on your own deliberate recopies.
- **Focus Assist / Do Not Disturb.** The primary channel is a Tk
  window, not a shell toast, precisely so DND does not suppress it.
  If the popup channel is disabled in `config.json`, the toast is
  your only visual channel and DND can swallow it. Keep
  `alert.popup = true` on machines where DND is routinely on.
- **Sound disabled system-wide.** The ding is a secondary channel;
  the popup and tray flash are the primary and tertiary.

## Assumptions that must hold

- ClipWarden itself is not compromised. The binary is not
  code-signed in v1.0.0; SHA-256 verification is the only integrity
  check. Verify before first run, verify after each upgrade.
- `%APPDATA%\ClipWarden\` is writable and not tampered with. An
  attacker who can edit `config.json` can disable every alert
  channel; a userland attacker running as you has that power by
  definition.
- The Windows clipboard message pump is reachable. Ride-along
  software that disables `AddClipboardFormatListener` or monopolises
  `OpenClipboard` with no release will blind ClipWarden; the tray
  will still start, but clipboard updates will not arrive.

## What v1.1 and later aim at

- Process-attribution for the last clipboard write (`GetClipboardOwner`,
  hWnd → PID) so the log records *who* made the substitution, not
  only that one happened.
- Code-signing the binary so SmartScreen stops scaring first-time
  users.
- Optional "restore previous address" button on the popup, guarded by
  the watcher's self-write suppression hook so the restore itself
  does not loop-detect.
- Event forwarding to Windows Event Log for managed-endpoint users.

None of those change the threat model above; they sharpen the
response to an attack the model already describes.
