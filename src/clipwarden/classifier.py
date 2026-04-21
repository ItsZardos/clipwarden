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


def classify(
    candidate: str | None,
    enabled_chains: frozenset[Chain] | None = None,
) -> ClassifiedAddress | None:
    """Classify ``candidate`` against the enabled chains.

    ``enabled_chains=None`` means "all chains" and is the default
    used by the test corpus. Production callers (see
    :class:`clipwarden.detector.Detector`) pass the user-configured
    set so a chain the user has disabled never produces a detection.
    A disabled chain is skipped before its validator runs, so the
    per-sample cost of disabling chains is strictly a win.
    """
    if not isinstance(candidate, str):
        return None
    s = candidate.strip()
    if not s:
        return None

    def allowed(chain: Chain) -> bool:
        return enabled_chains is None or chain in enabled_chains

    if allowed(Chain.BTC) and BTC_BECH32_PATTERN.fullmatch(s) and is_valid_btc_bech32_address(s):
        return ClassifiedAddress(Chain.BTC, s)
    if allowed(Chain.BTC) and BTC_BASE58_PATTERN.fullmatch(s) and is_valid_btc_base58_address(s):
        return ClassifiedAddress(Chain.BTC, s)
    if allowed(Chain.ETH) and ETH_PATTERN.fullmatch(s) and is_valid_eth_address(s):
        return ClassifiedAddress(Chain.ETH, s)
    if allowed(Chain.XMR) and XMR_PATTERN.fullmatch(s) and is_valid_xmr_address(s):
        return ClassifiedAddress(Chain.XMR, s)
    if allowed(Chain.SOL) and SOL_PATTERN.fullmatch(s) and is_valid_sol_address(s):
        return ClassifiedAddress(Chain.SOL, s)
    return None
