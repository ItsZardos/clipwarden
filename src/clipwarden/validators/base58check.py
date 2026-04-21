"""Base58Check validation for Bitcoin and CryptoNote Base58 for Monero.

Bitcoin and Monero both use Base58 but the encodings are not
interoperable. Bitcoin's Base58Check treats the whole byte string as
one big-endian integer and appends a double-SHA256 checksum. Monero
uses CryptoNote Base58, which encodes 8-byte blocks into 11-char
chunks (with a fixed lookup for short tail blocks) and uses Keccak-256
as the checksum primitive.

Both formats use the same 58-character alphabet, which is why they are
easy to confuse.
"""

from __future__ import annotations

import hashlib

import base58

from ._keccak import keccak256

# Bitcoin mainnet version bytes:
#   0x00 -> P2PKH  ("1..." addresses)
#   0x05 -> P2SH   ("3..." addresses)
_BTC_MAINNET_VERSIONS = (0x00, 0x05)

# Monero mainnet network tags:
#   18 -> standard address     (95 chars, starts with "4")
#   19 -> integrated address   (106 chars, starts with "4")
#   42 -> subaddress           (95 chars, starts with "8")
_XMR_MAINNET_TAGS = {18: 69, 19: 77, 42: 69}

_CN_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_CN_INDEX = {c: i for i, c in enumerate(_CN_ALPHABET)}

# Lookup for CryptoNote Base58 block sizes. Keys are encoded-chunk
# lengths that yield an exact number of decoded bytes without overflow.
# Any other tail length is invalid.
_CN_BLOCK_BYTES = {0: 0, 2: 1, 3: 2, 5: 3, 6: 4, 7: 5, 9: 6, 10: 7, 11: 8}


def is_valid_btc_base58_address(s: str) -> bool:
    if not s or s[0] not in "13":
        return False
    try:
        decoded = base58.b58decode(s)
    except ValueError:
        return False
    if len(decoded) != 25:
        return False
    if decoded[0] not in _BTC_MAINNET_VERSIONS:
        return False
    payload, checksum = decoded[:-4], decoded[-4:]
    digest = hashlib.sha256(hashlib.sha256(payload).digest()).digest()
    return digest[:4] == checksum


def is_valid_xmr_address(s: str) -> bool:
    # Length prefilter mirrors constants.XMR_PATTERN, repeated here so
    # the validator is safe to call without going through classify().
    if len(s) not in (95, 106) or s[0] not in "48":
        return False
    try:
        decoded = _cryptonote_b58_decode(s)
    except ValueError:
        return False
    expected_len = _XMR_MAINNET_TAGS.get(decoded[0])
    if expected_len is None or len(decoded) != expected_len:
        return False
    payload, checksum = decoded[:-4], decoded[-4:]
    return keccak256(payload)[:4] == checksum


def _cryptonote_b58_decode(s: str) -> bytes:
    full_blocks = len(s) // 11
    tail = len(s) % 11
    if tail not in _CN_BLOCK_BYTES:
        raise ValueError("invalid cryptonote base58 length")
    out = bytearray()
    for i in range(full_blocks):
        out.extend(_cn_decode_block(s[i * 11 : (i + 1) * 11], 8))
    if tail:
        out.extend(_cn_decode_block(s[full_blocks * 11 :], _CN_BLOCK_BYTES[tail]))
    return bytes(out)


def _cn_decode_block(block: str, size: int) -> bytes:
    n = 0
    for ch in block:
        idx = _CN_INDEX.get(ch)
        if idx is None:
            raise ValueError("invalid base58 character")
        n = n * 58 + idx
    if n >> (size * 8):
        raise ValueError("cryptonote base58 block overflow")
    return n.to_bytes(size, "big")
