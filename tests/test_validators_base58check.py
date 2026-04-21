"""Base58Check validator tests: BTC legacy/P2SH and XMR standard/subaddress."""

from __future__ import annotations

import pytest

from clipwarden.validators.base58check import (
    is_valid_btc_base58_address,
    is_valid_xmr_address,
)

BTC_VALID_P2PKH = [
    "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa",
    "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2",
    "12higDjoCCNXSA95xZMWUdPvXNmkAduhWv",
]

BTC_VALID_P2SH = [
    "3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy",
    "3P14159f73E4gFr7JterCCQh9QjiTjiZrG",
]


@pytest.mark.parametrize("addr", BTC_VALID_P2PKH + BTC_VALID_P2SH)
def test_valid_btc_base58_accepted(addr):
    assert is_valid_btc_base58_address(addr) is True


def test_btc_checksum_mutation_rejected():
    # Flip one character inside the payload of a known-good address.
    good = "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2"
    bad = good[:5] + ("X" if good[5] != "X" else "Y") + good[6:]
    assert is_valid_btc_base58_address(bad) is False


def test_btc_wrong_prefix_rejected():
    # Valid-looking Base58 with wrong version byte: start with '2'.
    assert is_valid_btc_base58_address("2BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2") is False


def test_btc_too_short_rejected():
    assert is_valid_btc_base58_address("1short") is False


def test_btc_empty_rejected():
    assert is_valid_btc_base58_address("") is False


def test_btc_non_base58_chars_rejected():
    # 'O', 'I', 'l', '0' are excluded from Base58.
    assert is_valid_btc_base58_address("1OOOOOOOOOOOOOOOOOOOOOOOOOOOOOO0I") is False


XMR_VALID = [
    "44AFFq5kSiGBoZ4NMDwYtN18obc8AemS33DBLWs3H7otXft3XjrpDtQGv7SqSsaBYBb98uNbr2VBBEt7f2wfn3RVGQBEP3A",
    "888tNkZrPN6JsEgekjMnABU4TBzc2Dt29EPAvkRxbANsAnjyPbb3iQ1YBRk1UXcdRsiKc9dhwMVgN5S9cQUiyoogDavup3H",
]


@pytest.mark.parametrize("addr", XMR_VALID)
def test_valid_xmr_accepted(addr):
    assert is_valid_xmr_address(addr) is True


def test_xmr_wrong_length_rejected():
    addr = "44AFFq5kSiGBoZ4NMDwYtN18obc8AemS33DBLWs3H7otXft3XjrpDtQGv7SqSsaBYBb98uNbr2VBBEt7f2wfn3RVGQBEP3"
    assert is_valid_xmr_address(addr) is False


def test_xmr_checksum_mutation_rejected():
    good = "44AFFq5kSiGBoZ4NMDwYtN18obc8AemS33DBLWs3H7otXft3XjrpDtQGv7SqSsaBYBb98uNbr2VBBEt7f2wfn3RVGQBEP3A"
    bad = good[:50] + ("X" if good[50] != "X" else "Y") + good[51:]
    assert is_valid_xmr_address(bad) is False


def test_xmr_wrong_first_char_rejected():
    # Starts with 5, which is not a valid XMR tag prefix.
    assert is_valid_xmr_address("5" + "x" * 94) is False


def test_xmr_non_base58_chars_rejected():
    addr = "4" + "O" * 94
    assert is_valid_xmr_address(addr) is False


def test_xmr_empty_rejected():
    assert is_valid_xmr_address("") is False


def test_cryptonote_decoder_rejects_invalid_tail():
    # Direct call into the decoder with a string whose length leaves a
    # tail the lookup table doesn't know about.
    from clipwarden.validators.base58check import _cryptonote_b58_decode

    with pytest.raises(ValueError):
        _cryptonote_b58_decode("1111")  # tail of 4 is invalid


def test_cryptonote_decoder_rejects_block_overflow():
    # 11 '1's decodes to 0 in Base58, fine. But 11 'z's exceeds 2**64-1.
    from clipwarden.validators.base58check import _cryptonote_b58_decode

    with pytest.raises(ValueError):
        _cryptonote_b58_decode("zzzzzzzzzzz")


def test_btc_wrong_version_byte_rejected():
    # Version byte 0x06 encodes a 25-byte Base58Check string that still
    # starts with '3', which slips past the leading-char prefilter. The
    # version-byte whitelist gate should then reject it.
    import hashlib

    import base58

    payload = bytes([0x06]) + b"\x00" * 20
    checksum = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    addr = base58.b58encode(payload + checksum).decode("ascii")
    assert addr[0] == "3"
    assert is_valid_btc_base58_address(addr) is False
