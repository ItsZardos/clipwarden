"""Address classifier.

Takes a string (presumed to be clipboard contents, already trimmed to
a single line by the watcher) and returns either a ``ClassifiedAddress``
or ``None``. The classifier is a pure function. It does not talk to
the clipboard, the filesystem, or anything else; that makes it cheap
to test exhaustively against the fixture corpora.

Ordering rationale: chains with stronger checksums run first, so a
string that happens to be valid on multiple shape gates resolves to
the chain with the higher-confidence signal. Solana runs last because
its on-curve check is the weakest of the four.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .constants import (
    BTC_BASE58_PATTERN,
    BTC_BECH32_PATTERN,
    ETH_PATTERN,
    SOL_PATTERN,
    XMR_PATTERN,
)
from .validators import (
    is_valid_btc_base58_address,
    is_valid_btc_bech32_address,
    is_valid_eth_address,
    is_valid_sol_address,
    is_valid_xmr_address,
)


class Chain(StrEnum):
    BTC = "BTC"
    ETH = "ETH"
    XMR = "XMR"
    SOL = "SOL"


@dataclass(frozen=True)
class ClassifiedAddress:
    chain: Chain
    address: str


def classify(candidate: str | None) -> ClassifiedAddress | None:
    if not isinstance(candidate, str):
        return None
    s = candidate.strip()
    if not s:
        return None

    if BTC_BECH32_PATTERN.fullmatch(s) and is_valid_btc_bech32_address(s):
        return ClassifiedAddress(Chain.BTC, s)
    if BTC_BASE58_PATTERN.fullmatch(s) and is_valid_btc_base58_address(s):
        return ClassifiedAddress(Chain.BTC, s)
    if ETH_PATTERN.fullmatch(s) and is_valid_eth_address(s):
        return ClassifiedAddress(Chain.ETH, s)
    if XMR_PATTERN.fullmatch(s) and is_valid_xmr_address(s):
        return ClassifiedAddress(Chain.XMR, s)
    if SOL_PATTERN.fullmatch(s) and is_valid_sol_address(s):
        return ClassifiedAddress(Chain.SOL, s)
    return None
