"""Ethereum address validation per EIP-55.

EIP-55 defines a mixed-case checksum on top of the 40-hex-character
body: the keccak-256 hash of the lowercase address, interpreted as
hex, tells you which letter positions to uppercase. See
https://eips.ethereum.org/EIPS/eip-55 for the spec.

Policy for this project (documented so future me isn't confused):

* If the address is all lowercase (``0x<40 lowercase hex>``) or all
  uppercase (``0x<40 uppercase hex>``), it has NOT claimed a checksum
  and we accept it. EIP-55 explicitly calls these the "no checksum
  claimed" cases.
* If the address is mixed case, it has claimed a checksum and we
  require that checksum to match. A mismatch is a hard reject.

Why not require mixed case? Because our threat model is clipper
substitution. A user may copy an address straight out of a wallet,
block explorer, or config file that emits lowercase (early wallets,
some JSON configs, etc.). Rejecting those would leave the detector
un-armed on that user's copy and let the attack through. False
positives are handled a layer up, at detection time, by requiring the
substitution to also look like a valid address of the same chain.
"""

from __future__ import annotations

from ._keccak import keccak256

_HEX_CHARS = frozenset("0123456789abcdefABCDEF")


def is_valid_eth_address(s: str) -> bool:
    if len(s) != 42 or not s.startswith("0x"):
        return False
    body = s[2:]
    if any(ch not in _HEX_CHARS for ch in body):
        return False

    letters = [ch for ch in body if ch.isalpha()]
    if not letters:
        # Pure digits (rare but possible): no case to verify, accept.
        return True
    has_lower = any(ch.islower() for ch in letters)
    has_upper = any(ch.isupper() for ch in letters)
    if not (has_lower and has_upper):
        return True

    lower = body.lower()
    digest_hex = keccak256(lower.encode("ascii")).hex()
    for i, ch in enumerate(body):
        if ch.isdigit():
            continue
        should_upper = int(digest_hex[i], 16) >= 8
        if should_upper and ch.islower():
            return False
        if not should_upper and ch.isupper():
            return False
    return True
