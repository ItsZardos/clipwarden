"""Property-based fuzzing of the detector state machine.

Hypothesis generates event streams from a fixed pool of real addresses
(so the classifier actually classifies) plus monotonic timestamps and
occasional "user input between" flags. We assert six invariants:

1. First classified event never fires.
2. Cross-chain pairs never fire.
3. User input between copies suppresses alerts.
4. Idempotency: same input stream, two fresh detectors, same outputs.
5. Monotonicity: after a detection fires, feeding the same "after"
   address again immediately cannot re-fire.
6. No-state-leak: after a reset, internal state matches fresh-init.

The pool is small by design. Hypothesis wastes a lot of work generating
random strings that classify() rejects; pinning the pool to real
addresses keeps the shrinker working on *timing* and *ordering*, which
is where the state-machine bugs live.
"""

from __future__ import annotations

from dataclasses import dataclass

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from clipwarden.classifier import classify
from clipwarden.detector import Detector

BTC_POOL = [
    "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4",
    "bc1qrp33g0q5c5txsp9arysrx4k6zdkfs4nce4xj0gdcccefvpysxf3qccfmv3",
    "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqzk5jj0",
    "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa",
    "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2",
    "3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy",
]
ETH_POOL = [
    "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045",
    "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
    "0xde709f2102306220921060314715629080e2fb77",
    "0x27b1fdb04752bbc536007a920d24acb045561c26",
]
SOL_POOL = [
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
]
XMR_POOL = [
    "44AFFq5kSiGBoZ4NMDwYtN18obc8AemS33DBLWs3H7otXft3XjrpDtQGv7SqSsaBYBb98uNbr2VBBEt7f2wfn3RVGQBEP3A",
    "888tNkZrPN6JsEgekjMnABU4TBzc2Dt29EPAvkRxbANsAnjyPbb3iQ1YBRk1UXcdRsiKc9dhwMVgN5S9cQUiyoogDavup3H",
]
NOISE_POOL = [
    "",
    "hello world",
    "not-an-address",
    "04c3614c5dbe345edb7bd22df1ac46b93e08154b",  # git SHA
    "550e8400-e29b-41d4-a716-446655440000",  # uuid
]
ALL_ADDRS = BTC_POOL + ETH_POOL + SOL_POOL + XMR_POOL
ADDR_OR_NOISE = ALL_ADDRS + NOISE_POOL

WINDOW_MS = 1000


@dataclass(frozen=True)
class Step:
    text: str
    dt_ms: int
    input_dt_ms: int  # added to current timeline position


def _addr_strategy():
    return st.sampled_from(ADDR_OR_NOISE)


def _step_strategy():
    # dt_ms >= 0 keeps timestamps monotonic. input_dt_ms >= 0 keeps
    # the input timestamp moving forward too. Ranges are wider than
    # WINDOW_MS on purpose so we sometimes cross the boundary and
    # sometimes stay inside it.
    return st.builds(
        Step,
        text=_addr_strategy(),
        dt_ms=st.integers(min_value=0, max_value=2500),
        input_dt_ms=st.integers(min_value=0, max_value=2500),
    )


def _replay(steps: list[Step], detector: Detector) -> list:
    t = 0
    last_input = 0
    events = []
    for s in steps:
        t += s.dt_ms
        # Input timestamp cannot exceed current wall-clock in reality;
        # clamp so the fuzzer doesn't construct impossible futures.
        last_input = min(t, last_input + s.input_dt_ms)
        events.append(detector.observe(s.text, t, last_input))
    return events


@settings(
    deadline=None,
    max_examples=200,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)
@given(steps=st.lists(_step_strategy(), min_size=1, max_size=25))
def test_first_classified_event_never_fires(steps: list[Step]) -> None:
    det = Detector(WINDOW_MS)
    events = _replay(steps, det)
    # Find the index of the first classifying event. Nothing at or
    # before that index may be a detection.
    t = 0
    first_class_idx: int | None = None
    for i, s in enumerate(steps):
        t += s.dt_ms
        if classify(s.text) is not None:
            first_class_idx = i
            break
    if first_class_idx is None:
        assert all(e is None for e in events)
    else:
        assert all(e is None for e in events[: first_class_idx + 1])


@settings(deadline=None, max_examples=300)
@given(
    a=st.sampled_from(BTC_POOL),
    b=st.sampled_from(ETH_POOL + SOL_POOL + XMR_POOL),
    dt=st.integers(min_value=0, max_value=WINDOW_MS),
)
def test_cross_chain_never_fires(a: str, b: str, dt: int) -> None:
    det = Detector(WINDOW_MS)
    assert det.observe(a, 0, 0) is None
    assert det.observe(b, dt, 0) is None


@settings(deadline=None, max_examples=200)
@given(
    a=st.sampled_from(BTC_POOL),
    b=st.sampled_from(BTC_POOL),
    elapsed=st.integers(min_value=1, max_value=WINDOW_MS),
    input_offset=st.integers(min_value=1, max_value=WINDOW_MS - 1),
)
def test_user_input_between_suppresses(a: str, b: str, elapsed: int, input_offset: int) -> None:
    # input_offset in [1, elapsed-1] places user input strictly between
    # the two copies. If a == b the test is trivially true (no alert
    # regardless); either way the invariant must hold.
    prev_ts = 1000
    next_ts = prev_ts + elapsed
    input_ts = prev_ts + min(input_offset, elapsed - 1)
    if input_ts <= prev_ts:
        input_ts = prev_ts + 1
    det = Detector(WINDOW_MS)
    det.observe(a, prev_ts, 0)
    assert det.observe(b, next_ts, input_ts) is None


@settings(deadline=None, max_examples=150)
@given(steps=st.lists(_step_strategy(), min_size=1, max_size=25))
def test_idempotent_replay(steps: list[Step]) -> None:
    a = _replay(steps, Detector(WINDOW_MS))
    b = _replay(steps, Detector(WINDOW_MS))
    assert a == b


@settings(deadline=None, max_examples=150)
@given(steps=st.lists(_step_strategy(), min_size=1, max_size=25))
def test_monotonicity_no_immediate_refire(steps: list[Step]) -> None:
    det = Detector(WINDOW_MS)
    t = 0
    last_input = 0
    for s in steps:
        t += s.dt_ms
        last_input = min(t, last_input + s.input_dt_ms)
        ev = det.observe(s.text, t, last_input)
        if ev is not None:
            # Immediately feeding the "after" again must not re-fire.
            t += 1
            assert det.observe(ev.after, t, last_input) is None


@settings(deadline=None, max_examples=100)
@given(steps=st.lists(_step_strategy(), min_size=1, max_size=25))
def test_reset_matches_fresh_init(steps: list[Step]) -> None:
    d1 = Detector(WINDOW_MS)
    d2 = Detector(WINDOW_MS)
    _replay(steps, d1)
    d1.reset()
    # Internal state must match a freshly constructed detector. Reaching
    # into private attrs is deliberate: this is the only way to catch a
    # "reset partially cleared state" regression without false negatives.
    assert d1._last_addr == d2._last_addr
    assert d1._last_ts_ms == d2._last_ts_ms
    # Behaviour must also match for any following stream.
    follow = [
        Step(text=ALL_ADDRS[0], dt_ms=10, input_dt_ms=0),
        Step(text=ALL_ADDRS[1], dt_ms=10, input_dt_ms=0),
    ]
    assert _replay(follow, d1) == _replay(follow, d2)
