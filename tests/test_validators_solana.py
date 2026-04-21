"""Solana validator tests."""

from __future__ import annotations

import base58
import pytest
from nacl.signing import SigningKey

from clipwarden.validators.solana import is_valid_sol_address

ON_CURVE_REAL = [
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
]


@pytest.mark.parametrize("addr", ON_CURVE_REAL)
def test_real_on_curve_accepted(addr):
    assert is_valid_sol_address(addr) is True


def test_freshly_generated_keypair_accepted():
    # Any legitimately generated Ed25519 public key must pass.
    verify_key = SigningKey.generate().verify_key
    pk_bytes = bytes(verify_key)
    addr = base58.b58encode(pk_bytes).decode("ascii")
    assert is_valid_sol_address(addr) is True


def test_system_program_rejected():
    # 32 zero bytes is not on the Ed25519 curve, so we intentionally
    # reject the Solana System Program ID. See validators/solana.py
    # for the design rationale.
    assert is_valid_sol_address("11111111111111111111111111111111") is False


def test_too_short_rejected():
    assert is_valid_sol_address("abc") is False


def test_too_long_rejected():
    assert is_valid_sol_address("E" * 45) is False


def test_wrong_byte_length_rejected():
    # 20 random bytes base58-encoded: right alphabet, wrong size.
    raw = bytes(range(20))
    encoded = base58.b58encode(raw).decode("ascii")
    assert is_valid_sol_address(encoded) is False


def test_non_base58_chars_rejected():
    # '0' is not in the Base58 alphabet.
    assert is_valid_sol_address("0" * 44) is False


def test_empty_rejected():
    assert is_valid_sol_address("") is False
