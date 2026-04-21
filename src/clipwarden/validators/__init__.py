"""Address validators.

Each module in this package handles one chain's checksum rules. The
dispatcher below merges Bitcoin's two address families into a single
entry point so the classifier can stay small.
"""

from __future__ import annotations

from .base58check import is_valid_btc_base58_address, is_valid_xmr_address
from .bech32 import is_valid_btc_bech32_address
from .eip55 import is_valid_eth_address
from .solana import is_valid_sol_address


def is_valid_btc_address(s: str) -> bool:
    if s.startswith(("bc1", "BC1")):
        return is_valid_btc_bech32_address(s)
    return is_valid_btc_base58_address(s)


__all__ = [
    "is_valid_btc_address",
    "is_valid_btc_base58_address",
    "is_valid_btc_bech32_address",
    "is_valid_eth_address",
    "is_valid_sol_address",
    "is_valid_xmr_address",
]
