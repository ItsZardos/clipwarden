"""Bitcoin segwit address validation (Bech32 and Bech32m).

The pinned ``bech32`` package (Pieter Wuille's reference) only
implements BIP-173 (Bech32). Taproot addresses under BIP-350 use
Bech32m, which differs only in the polymod constant. Rather than add
another dependency, this module reuses the low-level primitives from
the reference package and adds the Bech32m branch inline.

Version-to-encoding rule, per BIP-350:
    witness version 0 MUST use Bech32
    witness version 1..16 MUST use Bech32m

Mainnet HRP is ``bc``. Mixing upper- and lowercase within a single
address is invalid by BIP-173 and rejected here via the reference
decoder's own case check.
"""

from __future__ import annotations

from bech32 import CHARSET, bech32_hrp_expand, bech32_polymod, convertbits

_BECH32_CONST = 1
_BECH32M_CONST = 0x2BC830A3
_MAX_LENGTH = 90


def is_valid_btc_bech32_address(s: str) -> bool:
    if not s or len(s) > _MAX_LENGTH:
        return False
    lower = s.lower()
    if not (s == lower or s == s.upper()):
        return False
    if not lower.startswith("bc1"):
        return False

    hrp, data, encoding = _decode(lower)
    if hrp != "bc" or data is None or not data:
        return False

    witness_version = data[0]
    if witness_version > 16:
        return False
    if witness_version == 0 and encoding is not _BECH32_CONST:
        return False
    if witness_version >= 1 and encoding is not _BECH32M_CONST:
        return False

    program = convertbits(data[1:], 5, 8, False)
    if program is None or not (2 <= len(program) <= 40):
        return False
    if witness_version == 0:
        return len(program) in (20, 32)
    return True


def _decode(bech: str):
    """Return ``(hrp, data_no_checksum, const)`` or ``(None, None, None)``."""
    if any(ord(ch) < 33 or ord(ch) > 126 for ch in bech):
        return None, None, None
    pos = bech.rfind("1")
    if pos < 1 or pos + 7 > len(bech):
        return None, None, None
    payload = bech[pos + 1 :]
    if not all(ch in CHARSET for ch in payload):
        return None, None, None
    hrp = bech[:pos]
    data = [CHARSET.find(ch) for ch in payload]
    const = bech32_polymod(bech32_hrp_expand(hrp) + data)
    if const == _BECH32_CONST:
        return hrp, data[:-6], _BECH32_CONST
    if const == _BECH32M_CONST:
        return hrp, data[:-6], _BECH32M_CONST
    return None, None, None
