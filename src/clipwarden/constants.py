"""Shape-only regex prefilters and tunable defaults.

The regexes here are coarse. They decide whether a string is worth
handing to a validator. The validators (Base58Check, Bech32/Bech32m,
EIP-55, Ed25519 on-curve) are the real arbiters. Keeping the regexes
permissive and the validators strict is deliberate: it centralises the
"this is definitely an address" judgement in one layer that has test
coverage, rather than scattering shape assumptions across modules.
"""

from __future__ import annotations

import re

BTC_BASE58_PATTERN = re.compile(r"^[13][1-9A-HJ-NP-Za-km-z]{25,34}$")
BTC_BECH32_PATTERN = re.compile(r"^(?:bc1|BC1)[A-Za-z0-9]{6,87}$")

ETH_PATTERN = re.compile(r"^0x[0-9a-fA-F]{40}$")

# Length 95 or 106; first char is the Base58-encoded high nibble of the
# Monero mainnet tag byte. The validator verifies the exact tag.
XMR_PATTERN = re.compile(r"^[48][1-9A-HJ-NP-Za-km-z]{94,105}$")

# Solana has no checksum. Shape alone is cheap to match and would trigger
# on anything that looks like base58 of roughly-the-right length, so the
# validator also enforces a 32-byte decoded length and Ed25519 on-curve.
SOL_PATTERN = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


# Detection-layer defaults. Consumed by the Day 3 detector; exposed here
# so user-facing settings (config.json, settings window) can override.
DEFAULT_SUBSTITUTION_WINDOW_MS = 1000
DEFAULT_USER_INPUT_GRACE_MS = 750
