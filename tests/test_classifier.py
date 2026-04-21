"""Classifier-level tests.

These drive ``classify()`` end-to-end. Validator-specific edge cases
live in the per-validator test modules; this file exists to prove the
dispatch layer (shape prefilter, ordering, dataclass output) behaves
and to assert the zero-FP invariant against the full corpus.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from clipwarden.classifier import Chain, ClassifiedAddress, classify


def test_classify_returns_dataclass():
    result = classify("0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045")
    assert isinstance(result, ClassifiedAddress)
    assert result.chain is Chain.ETH
    assert result.address == "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"


def test_classify_frozen_dataclass():
    result = classify("0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045")
    with pytest.raises(FrozenInstanceError):
        result.chain = Chain.BTC  # type: ignore[misc]


@pytest.mark.parametrize("bad_input", [None, "", "   ", 42, 3.14, b"bytes", object()])
def test_classify_rejects_non_string_or_blank(bad_input):
    assert classify(bad_input) is None


def test_classify_strips_whitespace():
    result = classify("  0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045  ")
    assert result is not None
    assert result.chain is Chain.ETH


def test_real_fixtures_classify_correctly(real_addresses):
    for entry in real_addresses["entries"]:
        result = classify(entry["address"])
        assert result is not None, f"expected classify to succeed on {entry['address']}"
        assert result.chain.value == entry["chain"], (
            f"{entry['address']} classified as {result.chain.value}, expected {entry['chain']}"
        )
        assert result.address == entry["address"]


def test_off_curve_negatives_classify_none(real_addresses):
    for entry in real_addresses["off_curve_negatives"]:
        assert classify(entry["address"]) is None, (
            f"{entry['address']} classified non-None despite being on the off-curve negative list"
        )


def test_false_positive_corpus_returns_none(false_positive_corpus):
    offenders = [(s, classify(s)) for s in false_positive_corpus]
    offenders = [(s, r) for s, r in offenders if r is not None]
    assert not offenders, "false positives detected in FP corpus: " + ", ".join(
        f"{s!r} -> {r.chain.value}" for s, r in offenders
    )


def test_fp_corpus_has_meaningful_size(false_positive_corpus):
    # Guardrail so nobody silently trims the fixture file.
    assert len(false_positive_corpus) >= 50


def test_chain_enum_is_stable():
    assert {c.value for c in Chain} == {"BTC", "ETH", "XMR", "SOL"}


class TestEnabledChainsFilter:
    """Finding 1: disabled chains must not classify, even when valid."""

    BTC_BECH32 = "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"
    ETH_ADDR = "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"
    XMR_ADDR = "44AFFq5kSiGBoZ4NMDwYtN18obc8AemS33DBLWs3H7otXft3XjrpDtQGv7SqSsaBYBb98uNbr2VBBEt7f2wfn3RVGQBEP3A"
    SOL_ADDR = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

    def test_none_allows_every_chain(self):
        assert classify(self.BTC_BECH32, None) is not None
        assert classify(self.ETH_ADDR, None) is not None
        assert classify(self.XMR_ADDR, None) is not None
        assert classify(self.SOL_ADDR, None) is not None

    def test_empty_set_blocks_every_chain(self):
        empty: frozenset[Chain] = frozenset()
        assert classify(self.BTC_BECH32, empty) is None
        assert classify(self.ETH_ADDR, empty) is None
        assert classify(self.XMR_ADDR, empty) is None
        assert classify(self.SOL_ADDR, empty) is None

    def test_btc_only_rejects_other_chains(self):
        btc_only = frozenset({Chain.BTC})
        assert classify(self.BTC_BECH32, btc_only) is not None
        assert classify(self.ETH_ADDR, btc_only) is None
        assert classify(self.XMR_ADDR, btc_only) is None
        assert classify(self.SOL_ADDR, btc_only) is None

    def test_disabled_chain_returns_none_for_each_chain(self):
        # Turning off one chain must not affect the others. Run the
        # table through to catch a hypothetical ordering bug where an
        # earlier branch silently shadows a later one.
        for disabled, sample in (
            (Chain.BTC, self.BTC_BECH32),
            (Chain.ETH, self.ETH_ADDR),
            (Chain.XMR, self.XMR_ADDR),
            (Chain.SOL, self.SOL_ADDR),
        ):
            enabled = frozenset(Chain) - {disabled}
            assert classify(sample, enabled) is None, f"{disabled} should be filtered"
