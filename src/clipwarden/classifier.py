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


# Invisible or formatting-only Unicode code points that would defeat the
# ASCII shape regexes above. A hostile clipboard writer could otherwise
# embed zero-width joiners or bidi marks in an otherwise valid address
# so the regex sees a non-match while the wallet UI renders the string
# as though the characters were not present. Stripping them here means
# the detector sees the same logical address the user's wallet does.
_INVISIBLE_CHARS = frozenset(
    [
        "\u00ad",  # soft hyphen
        "\u200b",  # zero width space
        "\u200c",  # zero width non-joiner
        "\u200d",  # zero width joiner
        "\u2060",  # word joiner
        "\u2061",  # function application
        "\u2062",  # invisible times
        "\u2063",  # invisible separator
        "\u2064",  # invisible plus
        "\u2066",  # left-to-right isolate
        "\u2067",  # right-to-left isolate
        "\u2068",  # first strong isolate
        "\u2069",  # pop directional isolate
        "\u202a",  # left-to-right embedding
        "\u202b",  # right-to-left embedding
        "\u202c",  # pop directional formatting
        "\u202d",  # left-to-right override
        "\u202e",  # right-to-left override
        "\ufeff",  # zero width no-break space / byte-order mark
    ]
)


def _strip_invisibles(text: str) -> str:
    if not any(ch in _INVISIBLE_CHARS for ch in text):
        return text
    return "".join(ch for ch in text if ch not in _INVISIBLE_CHARS)


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
    s = _strip_invisibles(candidate).strip()
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
