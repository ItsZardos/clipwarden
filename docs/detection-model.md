# Detection Model

The part of ClipWarden that decides "this is a clipper attack, alert"
versus "this is a user deliberately copy-pasting, stay quiet." Two
layers: the **classifier** answers "is this a crypto address, and on
what chain", the **detector** answers "given a classified event
history, should we fire an alert".

## Classifier

`classifier.classify(text)` returns a `ClassifiedAddress(chain,
address)` or `None`. Pure function. Regex prefilter, real validator.

### Dispatch order, strongest-checksum first

```python
# clipwarden/classifier.py
if BTC_BECH32_PATTERN and is_valid_btc_bech32_address: return BTC
if BTC_BASE58_PATTERN  and is_valid_btc_base58_address:  return BTC
if ETH_PATTERN         and is_valid_eth_address:         return ETH
if XMR_PATTERN         and is_valid_xmr_address:         return XMR
if SOL_PATTERN         and is_valid_sol_address:         return SOL
return None
```

A candidate that happens to satisfy multiple shape gates (e.g. a
32–44-char base58 string that also passes Solana's on-curve check
while superficially resembling Monero) resolves to the chain with
the higher-confidence signal. Solana runs last because its validator
is the weakest of the four - on-curve is a 1-in-2 test, not a
cryptographic checksum.

### Chain-by-chain guarantees

| Chain | Prefilter shape | Validator | Strength |
| --- | --- | --- | --- |
| **BTC** (segwit / taproot) | `^(?:bc1\|BC1)[A-Za-z0-9]{6,87}$` | Bech32 (BIP-173) for v0, Bech32m (BIP-350) for v1+ | Polynomial checksum, ~1 in 2³⁰ collisions for a single random flip, BIP-350 hardens against certain length-extension mistakes. |
| **BTC** (legacy P2PKH / P2SH) | `^[13][1-9A-HJ-NP-Za-km-z]{25,34}$` | Base58Check (SHA-256 × 2 checksum) | 32-bit checksum, versioned prefix byte verified. |
| **ETH** (and EVM look-alikes) | `^0x[0-9a-fA-F]{40}$` | EIP-55 | Mixed-case addresses: full checksum. Pure-lower / pure-upper: accepted as "no checksum claimed," per the spec. |
| **XMR** (standard + subaddress) | `^[48][1-9A-HJ-NP-Za-km-z]{94,105}$` | CryptoNote Base58 + Keccak-256 network-tag check | Full Keccak-256 tail, vendored pure-Python Keccak used only for display (EIP-55) and this check. |
| **SOL** | `^[1-9A-HJ-NP-Za-km-z]{32,44}$` | Base58 decode → exactly 32 bytes → Ed25519 on-curve | No checksum. On-curve is a necessary-but-not-sufficient filter; base58 shape plus on-curve eliminates the vast majority of false positives. |

The regex layer is intentionally permissive and the validators are
intentionally strict. Shape assumptions live exclusively in
`constants.py`; the "this is definitely an address" judgement lives
exclusively in the `validators/` package. The validators have
exhaustive fixture coverage (`tests/fixtures/real_addresses.json`
for positives, `tests/fixtures/false_positives.txt` for negatives
that must not match).

## Detector

`detector.Detector.observe(text, ts_ms, last_input_ts_ms)` is a pure
state machine. It owns at most one `ClassifiedAddress` plus the
timestamp of when it was copied. It returns a `DetectionEvent` iff a
substitution is detected, `None` otherwise.

### Detection rule

Emit an alert iff **all** of the following hold:

1. A previous classified copy exists.
2. The previous and current copies are on **the same chain**.
3. The previous and current **addresses differ**.
4. The elapsed time between the two copies is **within
   `substitution_window_ms`** (default 1000 ms, configurable
   100–10 000 ms).
5. The user did **not** interact with the system after the previous
   copy: `last_input_ts_ms <= prev_ts_ms`. Any keystroke or mouse
   event after `prev_ts_ms` counts as user input and is treated as
   a deliberate recopy, not a hijack.

Each of those rules has a specific failure mode it protects against;
the next section lists them individually.

### What does NOT reset detector state

The detector deliberately keeps its memory of the last classified
address in several situations that a naive implementation would
clear. Every non-reset is there to defeat a specific evasion tactic.

- **Non-address content does not reset.** A clipper that launders
  via `A → some junk → B` would evade a detector that cleared on
  first sight of unrelated clipboard text. ClipWarden preserves the
  previous address through any number of non-classified copies; if
  `B` arrives inside the window, it still alerts.
- **Re-copying the same address does not reset.** If the user copies
  `A`, then copies `A` again, the detector rolls the window anchor
  forward (so the next opportunity for attack is measured from the
  most recent copy) but does not consider it a substitution.
- **Cross-chain transitions do not alert, but do update state.**
  Copying a BTC address and then an ETH address is a legitimate
  wallet-switching flow; wallets would reject a cross-chain paste
  anyway. State advances so the new address becomes the baseline
  for the next window.
- **Backward time is rejected silently.** A monotonic frame should
  not regress; if it does (clock nudge, frame mismatch), we treat
  the current sample as the new baseline and return None, erring
  on "don't fire a weird false positive."

### User-input gate

`last_input_ts_ms` is sampled via `GetLastInputInfo()` on every
clipboard event and lifted into the same monotonic frame the
detector uses. The gate is strict: `last_input_ts_ms > prev_ts_ms`
suppresses the alert, because that means the user definitely
touched the machine after the previous copy and before the current
one. Equality is ambiguous and we err on the side of alerting -
safer failure mode for a security tool.

Consequences:

- Running `attacker_sim` twice in a row while typing between
  invocations will not fire the detector on the second round. This
  is documented in the README troubleshooting section.
- An attacker's clipper that fires while the user is actively typing
  (e.g. pasting into a chat box and then deciding to send crypto)
  is not caught by this rule alone; it is only caught when the user
  copies address A, does nothing, and address B arrives.

### Whitelist behaviour

The detector asks a `WhitelistCheck(chain, address) -> bool` callable
after building the event. Whitelist membership does **not** suppress
the detection - the event is still emitted, logged, and sent to the
dispatcher with `whitelisted=True` set. The runtime uses that flag
to decide whether to route to alert channels (it does not) but
continues to write the `whitelisted_skip` entry to `log.jsonl` for
the audit trail.

Per-chain normalisation (inside `whitelist.py`):

- **ETH**: lowercased. EIP-55 is a display convention; the underlying
  address bytes are identical.
- **BTC bech32 / bech32m**: lowercased.
- **BTC base58, SOL, XMR**: case-sensitive. Different case == different
  address.

## Tunables (`config.json`)

- `substitution_window_ms` (100–10 000, default 1000). Upper bound
  on the BEFORE → AFTER gap. Smaller values miss slow clippers,
  larger values trip on ordinary user re-copies.
- `user_input_grace_ms` (0–10 000, default 750). Reserved: exposed
  for a future "recent user activity also counts as input" extension.
  The current detector uses the strict `GetLastInputInfo` sample
  directly.
- `enabled_chains`. Gate applied at classify-time: the classifier
  short-circuits and returns `None` for any chain not in the set,
  and the detector rejects the upstream event before updating any
  state. A disabled chain therefore produces zero detections and
  zero detector state - consistent with the config toggle being a
  real runtime gate, not just a UI filter. The Win32 clipboard
  watcher still queues the underlying clipboard events because it
  runs below the classifier; the work it does is bounded and
  cheap.

## Known false negatives (tradeoffs the current model accepts)

- **Clippers slower than the window.** A clipper that waits > 1 s
  before substituting, or that substitutes only when the clipboard
  has been stable for ≥ window ms, evades the default configuration.
  You can raise `substitution_window_ms` at the cost of suppressing
  more deliberate recopies.
- **Attacks while the user is typing.** The user-input gate suppresses
  alerts if any keystroke or mouse event happened between the two
  copies. A clipper that sits on `A` until the user starts typing
  elsewhere and then races in with `B` could fall in this window.
- **Pre-copy substitution.** A malicious wallet UI that writes the
  attacker's address directly on copy (so the user never sees `A`)
  is out of scope by construction: there is no "before" to compare
  against.
- **Novel chains.** Anything outside the four supported chains is
  invisible to the classifier. Social-engineering attacks that use
  an unsupported chain look indistinguishable from random text.

The first two are classic security-versus-usability tradeoffs; the
defaults are tuned for "low false-positive rate on a normal user's
workflow" at the cost of missing the slowest clippers. Users who
want stricter catch rates can widen the window and accept the
friction.

## Known false positives (and why they are acceptable)

- **Copy-pasting two different-but-valid addresses of the same chain
  within 1 s with no user input in between.** Rare in normal use;
  common in developer workflows. The popup is dismissible and the
  detection is logged; no money moves.
- **Scripted or automated address generation** (e.g. running
  `attacker_sim` for a demo, generating wallets with a script). Also
  rare outside deliberate testing; tools that automate address work
  should run ClipWarden paused or disabled.

## Testing

- Fixture corpora:
  [`tests/fixtures/real_addresses.json`](../tests/fixtures/real_addresses.json)
  (real mainnet addresses across all four chains, with provenance),
  [`tests/fixtures/false_positives.txt`](../tests/fixtures/false_positives.txt)
  (git SHAs, UUIDs, API keys, and checksum-mutated near-addresses).
- Property tests: `tests/test_detector_hypothesis.py` uses Hypothesis
  to fuzz the state machine for first-event behaviour, cross-chain
  transitions, user-input suppression, idempotency, monotonicity,
  and no-state-leak invariants.
- Scenario replay: `tools/dev_feed.py` reads hand-written YAML
  scenarios and drives the classifier + detector end-to-end without
  touching the real clipboard.
- Adversarial smoke: `tools/attacker_sim.py` writes a real
  substitution pair to the OS clipboard against a running
  ClipWarden. Refuses to run without `--i-know-this-is-adversarial`.
