"""Tests for the public ``validators.is_valid_btc_address`` dispatcher.

The classifier bypasses this dispatcher and calls the two BTC branches
directly for ordering reasons, so the dispatcher only gets exercised
by consumers that import ``validators`` as a flat surface (and by
these tests).
"""

from __future__ import annotations

from clipwarden.validators import is_valid_btc_address


def test_dispatch_routes_bech32_mainnet():
    assert is_valid_btc_address("bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4") is True


def test_dispatch_routes_bech32_uppercase():
    assert is_valid_btc_address("BC1QW508D6QEJXTDG4Y5R3ZARVARY0C5XW7KV8F3T4") is True


def test_dispatch_routes_base58_legacy():
    assert is_valid_btc_address("1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2") is True


def test_dispatch_routes_base58_p2sh():
    assert is_valid_btc_address("3P14159f73E4gFr7JterCCQh9QjiTjiZrG") is True


def test_dispatch_rejects_garbage():
    assert is_valid_btc_address("not-a-btc-address") is False
