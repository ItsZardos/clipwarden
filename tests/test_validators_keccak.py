"""Keccak-256 known-answer tests.

If any of these fail, EIP-55 validation is silently broken. Keep them
green before shipping anything that depends on Ethereum address
correctness.
"""

from __future__ import annotations

import pytest

from clipwarden.validators._keccak import keccak256

KAT = [
    (b"", "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"),
    (b"abc", "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45"),
    (
        b"The quick brown fox jumps over the lazy dog",
        "4d741b6f1eb29cb2a9b9911c82f56fa8d73b04959d3d9d222895df6c0b28aa15",
    ),
]


@pytest.mark.parametrize("data, expected_hex", KAT)
def test_known_vectors(data, expected_hex):
    assert keccak256(data).hex() == expected_hex


def test_output_length_is_32_bytes():
    assert len(keccak256(b"")) == 32
    assert len(keccak256(b"x" * 1024)) == 32


def test_large_input_consistency():
    # Two blocks-worth of input, to exercise multiple absorb calls.
    data = b"a" * 300
    digest_a = keccak256(data)
    digest_b = keccak256(data)
    assert digest_a == digest_b
