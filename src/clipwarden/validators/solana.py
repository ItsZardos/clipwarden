"""Solana address validation.

Solana addresses are the Base58 encoding of a 32-byte Ed25519 public
key. There is no checksum, so shape alone is weak evidence. We gate
on two things:

1. Base58 decodes to exactly 32 bytes.
2. Those 32 bytes are a canonically-encoded point on the Ed25519
   main subgroup. ``libsodium``'s ``crypto_core_ed25519_is_valid_point``
   covers both conditions in one call.

Consequence of the on-curve gate: Program Derived Addresses (PDAs)
are, by design, off-curve, so we classify them as ``None``. This is
acceptable for the clipper threat model because PDAs are not
addresses a user would normally paste as a send destination; they are
signers owned by programs. The same applies to deterministic system
addresses such as ``11111111111111111111111111111111`` (the System
Program), which is also off-curve. If a future feature needs to
recognise program accounts, that should be a separate code path that
opts in, not a relaxation of this rule.
"""

from __future__ import annotations

import base58
from nacl.bindings import crypto_core_ed25519_is_valid_point


def is_valid_sol_address(s: str) -> bool:
    if not (32 <= len(s) <= 44):
        return False
    try:
        decoded = base58.b58decode(s)
    except ValueError:
        return False
    if len(decoded) != 32:
        return False
    try:
        return bool(crypto_core_ed25519_is_valid_point(decoded))
    except Exception:
        return False
