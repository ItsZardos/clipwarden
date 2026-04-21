"""Bech32 / Bech32m validator tests for Bitcoin segwit addresses."""

from __future__ import annotations

import pytest

from clipwarden.validators.bech32 import is_valid_btc_bech32_address

VALID_BECH32_V0 = [
    "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4",
    "BC1QW508D6QEJXTDG4Y5R3ZARVARY0C5XW7KV8F3T4",
    "bc1qrp33g0q5c5txsp9arysrx4k6zdkfs4nce4xj0gdcccefvpysxf3qccfmv3",
]

VALID_BECH32M_TAPROOT = [
    "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqzk5jj0",
    "bc1pxwww0ct9ue7e8tdnlmug5m2tamfn7q06sahstg39ys4c9f3340qqxrdu9k",
]


@pytest.mark.parametrize("addr", VALID_BECH32_V0 + VALID_BECH32M_TAPROOT)
def test_valid_segwit_accepted(addr):
    assert is_valid_btc_bech32_address(addr) is True


def test_mixed_case_rejected():
    # BIP-173 disallows mixed case within a single address.
    addr = "bc1QW508D6QEJXTDG4Y5R3ZARVARY0C5XW7KV8F3T4"
    assert is_valid_btc_bech32_address(addr) is False


def test_checksum_mutation_v0_rejected():
    good = "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"
    bad = good[:-1] + ("a" if good[-1] != "a" else "q")
    assert is_valid_btc_bech32_address(bad) is False


def test_checksum_mutation_taproot_rejected():
    good = "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqzk5jj0"
    bad = good[:-1] + ("9" if good[-1] != "9" else "y")
    assert is_valid_btc_bech32_address(bad) is False


def test_wrong_hrp_rejected():
    # Testnet (tb1) addresses share the shape but we only accept mainnet bc1.
    assert is_valid_btc_bech32_address("tb1qw508d6qejxtdg4y5r3zarvary0c5xw7kxpjzsx") is False


def test_v0_with_bech32m_checksum_rejected():
    # Swap-in: the BIP-350 Taproot address format applied to a v0
    # program would have the wrong checksum constant.
    synthetic = "bc1q0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqzk5jj0"
    assert is_valid_btc_bech32_address(synthetic) is False


def test_empty_rejected():
    assert is_valid_btc_bech32_address("") is False


def test_too_long_rejected():
    # Over BIP-173's 90-char limit.
    assert is_valid_btc_bech32_address("bc1" + "q" * 100) is False


def test_missing_bc1_prefix_rejected():
    assert is_valid_btc_bech32_address("ab1qw508d6qejxtdg4y5r3zarvary0c5xw7k") is False


def test_too_short_after_prefix_rejected():
    # "bc1" passes the startswith gate but the reference decoder rejects
    # anything where the separator leaves fewer than 6 checksum chars.
    assert is_valid_btc_bech32_address("bc1") is False


def test_non_printable_char_rejected():
    assert is_valid_btc_bech32_address("bc1q\x01qejxtdg4y5r3zarvary0c5xw7kv8f3t4") is False


def test_missing_separator_rejected():
    # No '1' separator in a stripped form.
    assert is_valid_btc_bech32_address("bcqw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4") is False


def test_bad_data_char_rejected():
    # Contains 'b' which is not in the Bech32 CHARSET.
    addr = "bc1bb508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"
    assert is_valid_btc_bech32_address(addr) is False


def test_synthetic_witness_version_17_rejected():
    # Construct a Bech32m string with a first-byte witness version of
    # 17 (out of spec). Uses the low-level primitives directly to make
    # the checksum valid so the version gate is actually hit.
    from bech32 import CHARSET, bech32_hrp_expand, bech32_polymod

    data = [17] + [0] * 10  # version 17 + 10 data symbols
    values = bech32_hrp_expand("bc") + data + [0, 0, 0, 0, 0, 0]
    polymod = bech32_polymod(values) ^ 0x2BC830A3
    checksum = [(polymod >> 5 * (5 - i)) & 31 for i in range(6)]
    combined = data + checksum
    addr = "bc1" + "".join(CHARSET[c] for c in combined)
    assert is_valid_btc_bech32_address(addr) is False


def test_synthetic_program_length_over_40_rejected():
    # Construct a Taproot (v1, Bech32m) address carrying a 41-byte
    # program. BIP-141 caps witness programs at 40 bytes, so even with
    # a valid Bech32m checksum this must be rejected. Covers the
    # `not (2 <= len(program) <= 40)` gate.
    from bech32 import CHARSET, bech32_hrp_expand, bech32_polymod, convertbits

    program = bytes(range(41))
    data_5bit = convertbits(list(program), 8, 5, True)
    assert data_5bit is not None
    data = [1] + data_5bit
    values = bech32_hrp_expand("bc") + data + [0, 0, 0, 0, 0, 0]
    polymod = bech32_polymod(values) ^ 0x2BC830A3
    checksum = [(polymod >> 5 * (5 - i)) & 31 for i in range(6)]
    addr = "bc1" + "".join(CHARSET[c] for c in data + checksum)
    assert is_valid_btc_bech32_address(addr) is False


def test_synthetic_v0_with_bech32m_checksum_rejected():
    # v0 must use Bech32 per BIP-350. Construct a v0 string whose
    # checksum is correct under Bech32m and confirm the version/encoding
    # gate rejects it.
    from bech32 import CHARSET, bech32_hrp_expand, bech32_polymod, convertbits

    program = b"\x00" * 20
    data_5bit = convertbits(list(program), 8, 5, True)
    assert data_5bit is not None
    data = [0] + data_5bit
    values = bech32_hrp_expand("bc") + data + [0, 0, 0, 0, 0, 0]
    polymod = bech32_polymod(values) ^ 0x2BC830A3
    checksum = [(polymod >> 5 * (5 - i)) & 31 for i in range(6)]
    addr = "bc1" + "".join(CHARSET[c] for c in data + checksum)
    assert is_valid_btc_bech32_address(addr) is False


def test_synthetic_v1_with_bech32_checksum_rejected():
    # Inverse of the above: v1+ must use Bech32m. A v1 string with a
    # valid Bech32 checksum should be rejected.
    from bech32 import CHARSET, bech32_hrp_expand, bech32_polymod, convertbits

    program = b"\x00" * 32
    data_5bit = convertbits(list(program), 8, 5, True)
    assert data_5bit is not None
    data = [1] + data_5bit
    values = bech32_hrp_expand("bc") + data + [0, 0, 0, 0, 0, 0]
    polymod = bech32_polymod(values) ^ 1
    checksum = [(polymod >> 5 * (5 - i)) & 31 for i in range(6)]
    addr = "bc1" + "".join(CHARSET[c] for c in data + checksum)
    assert is_valid_btc_bech32_address(addr) is False


def test_synthetic_v0_short_program_rejected():
    # Segwit v0 program must be 20 or 32 bytes. Construct one with a
    # 16-byte program that otherwise has a valid Bech32 checksum.
    from bech32 import CHARSET, bech32_hrp_expand, bech32_polymod, convertbits

    program = b"\x00" * 16
    data = [0] + convertbits(list(program), 8, 5)
    values = bech32_hrp_expand("bc") + data + [0, 0, 0, 0, 0, 0]
    polymod = bech32_polymod(values) ^ 1
    checksum = [(polymod >> 5 * (5 - i)) & 31 for i in range(6)]
    combined = data + checksum
    addr = "bc1" + "".join(CHARSET[c] for c in combined)
    assert is_valid_btc_bech32_address(addr) is False
