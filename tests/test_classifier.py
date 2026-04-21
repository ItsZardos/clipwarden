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
