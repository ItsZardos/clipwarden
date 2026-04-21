"""EIP-55 validator tests.

Covers the policy documented in validators/eip55.py: mixed case must
match the checksum, pure lowercase / pure uppercase are "no checksum
claimed" and accepted, anything else is rejected.
"""

from __future__ import annotations

import pytest

from clipwarden.validators._keccak import keccak256
from clipwarden.validators.eip55 import is_valid_eth_address

EIP55_CANONICAL = [
    # From https://eips.ethereum.org/EIPS/eip-55#test-cases
    "0x52908400098527886E0F7030069857D2E4169EE7",
    "0x8617E340B3D01FA5F11F306F4090FD50E238070D",
    "0xde709f2102306220921060314715629080e2fb77",
    "0x27b1fdb04752bbc536007a920d24acb045561c26",
    "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
    "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359",
    "0xdbF03B407c01E7cD3CBea99509d93f8DDDC8C6FB",
    "0xD1220A0cf47c7B9Be7A2E6BA89F429762e7b9aDb",
]


@pytest.mark.parametrize("addr", EIP55_CANONICAL)
def test_eip55_canonical_vectors_accepted(addr):
    assert is_valid_eth_address(addr) is True


def test_vitalik_accepted():
    assert is_valid_eth_address("0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045")


def test_all_lowercase_accepted_no_checksum_claimed():
    # Valid hex, all lowercase letters: EIP-55 treats this as "no
    # checksum claimed" and accepts.
    assert is_valid_eth_address("0xabcdefabcdefabcdefabcdefabcdefabcdefabcd")


def test_all_uppercase_accepted_no_checksum_claimed():
    assert is_valid_eth_address("0xABCDEFABCDEFABCDEFABCDEFABCDEFABCDEFABCD")


def test_pure_digit_body_accepted():
    # No letters means no case to verify; the hex is still a valid
    # 40-char body, so it passes the shape gate.
    assert is_valid_eth_address("0x" + "0123456789" * 4)


def test_mixed_case_wrong_checksum_rejected():
    # Valid canonical with one letter case flipped.
    addr = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1beAed"  # note lowercase 'b' that should be 'B'
    assert is_valid_eth_address(addr) is False


def test_missing_0x_rejected():
    assert is_valid_eth_address("d8dA6BF26964aF9D7eEd9e03E53415D37aA96045") is False


def test_wrong_length_rejected():
    assert is_valid_eth_address("0xd8dA6BF26964aF9D7eEd9e03E53415D37aA960") is False
    assert is_valid_eth_address("0xd8dA6BF26964aF9D7eEd9e03E53415D37aA960450") is False


def test_non_hex_rejected():
    assert is_valid_eth_address("0xZZZZ6BF26964aF9D7eEd9e03E53415D37aA96045") is False


def test_empty_and_short_inputs():
    assert is_valid_eth_address("") is False
    assert is_valid_eth_address("0x") is False
    assert is_valid_eth_address("0xd8") is False


def test_keccak_matches_eip55_derivation():
    # Sanity-check the derivation path: if this test fails, the
    # EIP-55 tests above would fail too, but this one names the
    # failure clearly.
    body = "d8da6bf26964af9d7eed9e03e53415d37aa96045"
    digest = keccak256(body.encode("ascii")).hex()
    assert digest.startswith("535bdae9bb214b3c")
