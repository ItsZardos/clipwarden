"""Hand-written detector scenarios.

Every case here maps back to a real-world condition I want the detector
to handle correctly. Hypothesis fuzzing lives in
``test_detector_hypothesis.py`` and covers the invariants; this file
covers the named edge cases.
"""

from __future__ import annotations

import pytest

from clipwarden.detector import DetectionEvent, Detector

# Shorthand. All of these are verified-valid via the Day 2 validators
# (BIP-173 test vectors, EIP-55 canonical vectors, real mainnet USDC
# mint, etc).
BTC_BECH32_A = "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"
BTC_BECH32_B = "bc1qrp33g0q5c5txsp9arysrx4k6zdkfs4nce4xj0gdcccefvpysxf3qccfmv3"
BTC_TAPROOT = "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqzk5jj0"
BTC_LEGACY_A = "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"
BTC_LEGACY_B = "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2"
ETH_MIXED = "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"
SOL_USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


def test_first_event_never_fires() -> None:
    det = Detector(1000)
    assert det.observe(BTC_BECH32_A, 0, 0) is None


def test_classic_btc_substitution_fires() -> None:
    det = Detector(1000)
    assert det.observe(BTC_BECH32_A, 0, 0) is None
    ev = det.observe(BTC_BECH32_B, 500, 0)
    assert isinstance(ev, DetectionEvent)
    assert ev.chain == "BTC"
    assert ev.before == BTC_BECH32_A
    assert ev.after == BTC_BECH32_B
    assert ev.elapsed_ms == 500
    assert ev.whitelisted is False


def test_same_address_recopied_no_alert() -> None:
    det = Detector(1000)
    assert det.observe(BTC_BECH32_A, 0, 0) is None
    assert det.observe(BTC_BECH32_A, 200, 0) is None


def test_cross_chain_no_alert() -> None:
    det = Detector(1000)
    assert det.observe(BTC_BECH32_A, 0, 0) is None
    assert det.observe(ETH_MIXED, 500, 0) is None


def test_non_address_then_same_address_no_alert() -> None:
    # Pattern: valid A -> unrelated clipboard content -> valid A again,
    # all within the window. Must not fire; state is effectively rolled
    # forward to the new A copy.
    det = Detector(1000)
    assert det.observe(BTC_BECH32_A, 0, 0) is None
    assert det.observe("hello world", 200, 0) is None
    assert det.observe(BTC_BECH32_A, 400, 0) is None


def test_non_address_between_does_not_hide_substitution() -> None:
    # A clipper that does A -> junk -> B within the window is still a
    # clipper. Non-address text does not clear the previous address.
    det = Detector(1000)
    assert det.observe(BTC_BECH32_A, 0, 0) is None
    assert det.observe("not an address", 200, 0) is None
    ev = det.observe(BTC_BECH32_B, 600, 0)
    assert ev is not None
    assert ev.before == BTC_BECH32_A
    assert ev.after == BTC_BECH32_B


def test_outside_window_no_alert() -> None:
    det = Detector(1000)
    assert det.observe(BTC_BECH32_A, 0, 0) is None
    assert det.observe(BTC_BECH32_B, 1001, 0) is None


def test_exact_window_boundary_fires() -> None:
    # elapsed == window_ms is "within" by our contract. Documented in
    # detector.py. If you change the semantics, update this test and
    # the docstring together.
    det = Detector(1000)
    assert det.observe(BTC_BECH32_A, 0, 0) is None
    ev = det.observe(BTC_BECH32_B, 1000, 0)
    assert ev is not None
    assert ev.elapsed_ms == 1000


def test_one_ms_past_boundary_does_not_fire() -> None:
    det = Detector(1000)
    assert det.observe(BTC_BECH32_A, 0, 0) is None
    assert det.observe(BTC_BECH32_B, 1001, 0) is None


def test_user_input_between_copies_no_alert() -> None:
    det = Detector(1000)
    assert det.observe(BTC_BECH32_A, 0, 0) is None
    # last_input_ts_ms > prev_ts means the user interacted after the
    # first copy. Treated as deliberate.
    assert det.observe(BTC_BECH32_B, 500, last_input_ts_ms=300) is None


def test_input_at_prev_ts_still_fires() -> None:
    # Strict inequality: input timestamp equal to previous copy is
    # treated as "no interaction since"; we alert. This is the
    # safer-for-a-security-tool default.
    det = Detector(1000)
    assert det.observe(BTC_BECH32_A, 100, 50) is None
    ev = det.observe(BTC_BECH32_B, 300, last_input_ts_ms=100)
    assert ev is not None


def test_whitelisted_emits_event_with_flag() -> None:
    det = Detector(1000, is_whitelisted=lambda c, a: c == "BTC" and a == BTC_BECH32_B)
    det.observe(BTC_BECH32_A, 0, 0)
    ev = det.observe(BTC_BECH32_B, 500, 0)
    assert ev is not None
    assert ev.whitelisted is True
    assert ev.chain == "BTC"
    assert ev.after == BTC_BECH32_B


def test_reset_clears_state() -> None:
    det = Detector(1000)
    det.observe(BTC_BECH32_A, 0, 0)
    assert det.last_address == BTC_BECH32_A
    det.reset()
    assert det.last_address is None
    # After reset the next copy is a "first event" again and cannot fire.
    assert det.observe(BTC_BECH32_B, 100, 0) is None


def test_monotonicity_no_refire_on_same_pair() -> None:
    det = Detector(1000)
    det.observe(BTC_BECH32_A, 0, 0)
    ev = det.observe(BTC_BECH32_B, 500, 0)
    assert ev is not None
    # Seeing B again is "same address as before" and must not re-fire.
    assert det.observe(BTC_BECH32_B, 600, 0) is None
    # Seeing A a third time - A vs B (B is now the anchor) within window
    # is a *new* substitution pair and may fire. Document both outcomes.
    ev2 = det.observe(BTC_BECH32_A, 700, 0)
    assert ev2 is not None
    assert ev2.before == BTC_BECH32_B
    assert ev2.after == BTC_BECH32_A


def test_backwards_time_rejected() -> None:
    det = Detector(1000)
    det.observe(BTC_BECH32_A, 5000, 0)
    assert det.observe(BTC_BECH32_B, 4000, 0) is None


def test_cross_chain_then_back_within_window() -> None:
    # A -> (different chain) -> B-of-A-chain, all inside window.
    # The middle event updates the "last" pointer to the other chain,
    # so the final event compares against that one. No substitution.
    det = Detector(1000)
    det.observe(BTC_BECH32_A, 0, 0)
    det.observe(ETH_MIXED, 300, 0)
    assert det.observe(BTC_BECH32_B, 600, 0) is None


def test_invalid_window_rejected() -> None:
    with pytest.raises(ValueError):
        Detector(0)
    with pytest.raises(ValueError):
        Detector(-1)


def test_window_ms_property() -> None:
    det = Detector(2500)
    assert det.window_ms == 2500


def test_ambiguous_shape_resolves_via_classifier_strongest_first() -> None:
    # A Taproot address matches BTC bech32m shape and only there; the
    # classifier resolves it. This test locks in that the detector
    # never "second-guesses" the classifier.
    det = Detector(1000)
    det.observe(BTC_BECH32_A, 0, 0)
    ev = det.observe(BTC_TAPROOT, 500, 0)
    assert ev is not None
    assert ev.chain == "BTC"
    assert ev.before == BTC_BECH32_A
    assert ev.after == BTC_TAPROOT


def test_legacy_to_bech32_both_btc_fires() -> None:
    det = Detector(1000)
    det.observe(BTC_LEGACY_A, 0, 0)
    ev = det.observe(BTC_BECH32_A, 500, 0)
    assert ev is not None
    assert ev.chain == "BTC"


def test_legacy_to_legacy_fires() -> None:
    det = Detector(1000)
    det.observe(BTC_LEGACY_A, 0, 0)
    ev = det.observe(BTC_LEGACY_B, 500, 0)
    assert ev is not None
    assert ev.before == BTC_LEGACY_A


def test_eth_case_only_change_treated_as_substitution() -> None:
    # Same underlying ETH destination, different checksum case. String
    # comparison is strict, so we treat this as a substitution. That's
    # the safer failure mode for a security tool: a false positive on
    # a harmless case-flip costs a toast; missing a real swap costs
    # real money. Locking in the behaviour here.
    det = Detector(1000)
    det.observe(ETH_MIXED, 0, 0)
    ev = det.observe(ETH_MIXED.lower(), 500, 0)
    assert ev is not None


def test_sol_first_event_does_not_fire() -> None:
    # Same baseline invariant as every other chain; here mainly because
    # the SOL code path is Ed25519-gated and I want a smoke test that
    # a real mainnet SOL address flows through cleanly.
    det = Detector(1000)
    assert det.observe(SOL_USDC, 0, 0) is None


def test_empty_inputs_ignored() -> None:
    det = Detector(1000)
    assert det.observe("", 0, 0) is None
    assert det.observe("   ", 50, 0) is None
    # The first valid classified copy still establishes baseline only;
    # nothing to compare against yet, so no alert.
    assert det.observe(BTC_BECH32_A, 100, 0) is None


class TestEnabledChainsGate:
    """Finding 1: a user-disabled chain must not alert at the detector layer."""

    def test_disabled_chain_cannot_baseline(self) -> None:
        from clipwarden.classifier import Chain  # noqa: PLC0415

        # BTC off means the first BTC copy never becomes a baseline,
        # so a subsequent BTC copy cannot form a substitution pair.
        det = Detector(1000, enabled_chains=frozenset({Chain.ETH}))
        assert det.observe(BTC_BECH32_A, 0, 0) is None
        assert det.observe(BTC_BECH32_B, 500, 0) is None

    def test_enabled_chain_still_fires_when_others_disabled(self) -> None:
        from clipwarden.classifier import Chain  # noqa: PLC0415

        det = Detector(1000, enabled_chains=frozenset({Chain.BTC}))
        assert det.observe(BTC_BECH32_A, 0, 0) is None
        ev = det.observe(BTC_BECH32_B, 500, 0)
        assert isinstance(ev, DetectionEvent)
        assert ev.chain == "BTC"

    def test_none_enables_all_chains(self) -> None:
        det = Detector(1000, enabled_chains=None)
        assert det.observe(BTC_BECH32_A, 0, 0) is None
        ev = det.observe(BTC_BECH32_B, 500, 0)
        assert isinstance(ev, DetectionEvent)
