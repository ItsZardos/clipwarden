"""Substitution-time clipper detector.

The detector is a pure state machine. It does not touch the clipboard,
does not read files, does not write logs, and does not know whether it
is running on Windows. It receives classified clipboard events and
emits :class:`DetectionEvent` instances. Subscribers (the app, the
logger, the toast notifier) decide what to do with those events.

Why pure:
    * Testable without Windows or PyInstaller machinery.
    * Hypothesis can fuzz it at tens of thousands of cases per second.
    * Swap in a real :func:`classify` for production and a stub for tests.
    * The watcher can feed it synthetic events during CI.

Timing contract (important, easy to get wrong):
    * ``ts_ms`` is an absolute monotonic timestamp in milliseconds.
      The app uses ``time.monotonic_ns() // 1_000_000`` in production;
      tests use synthetic values. It MUST be monotonically non-decreasing.
    * ``last_input_ts_ms`` is ALSO an absolute timestamp in the same
      reference frame, representing when the user last interacted with
      the system (keyboard or mouse). In production this comes from
      ``GetLastInputInfo`` converted into the same monotonic frame.
      The name ends in ``_ts_ms`` to distinguish it from durations.

Detection rule, expressed as "we alert iff":
    1. We have a previous classified event (same chain, different address).
    2. The new event arrived within ``substitution_window_ms`` of it.
    3. The user did not interact with the machine between the two copies
       (i.e. ``last_input_ts_ms <= prev_ts_ms``). A keystroke or click
       between the two copies implies a deliberate recopy, not a clipper.

Intentional non-resets:
    * Non-address clipboard content does NOT clear the previous address.
      A clipper that launders ``A -> junk -> B`` is still a clipper; if
      we threw away ``A`` on first sight of unrelated text, we would
      lose detection on that variant.
    * A cross-chain transition does not alert but DOES update state; a
      user jumping between wallets legitimately reclassifies the latest
      copy as the new baseline.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .classifier import Chain, ClassifiedAddress, classify

WhitelistCheck = Callable[[str, str], bool]


def _never_whitelisted(_chain: str, _address: str) -> bool:
    return False


@dataclass(frozen=True)
class DetectionEvent:
    ts_ms: int
    chain: str
    before: str
    after: str
    elapsed_ms: int
    whitelisted: bool


class Detector:
    """Pure state machine. Not thread-safe by design.

    The watcher thread owns a single :class:`Detector` instance and
    serialises all :meth:`observe` calls. If you need to wire multiple
    producers, do the serialisation upstream - not inside this class.
    Adding a lock here would hide real bugs in the caller.
    """

    def __init__(
        self,
        substitution_window_ms: int,
        is_whitelisted: WhitelistCheck = _never_whitelisted,
        enabled_chains: frozenset[Chain] | None = None,
    ) -> None:
        if substitution_window_ms <= 0:
            raise ValueError("substitution_window_ms must be positive")
        self._window_ms = substitution_window_ms
        self._is_whitelisted = is_whitelisted
        # None means "all chains"; Runtime passes a concrete frozenset
        # derived from cfg.enabled_chains so a user-disabled chain is
        # never classified and therefore never alerts.
        self._enabled_chains = enabled_chains
        self._last_addr: ClassifiedAddress | None = None
        self._last_ts_ms: int | None = None

    @property
    def window_ms(self) -> int:
        return self._window_ms

    @property
    def last_address(self) -> str | None:
        return self._last_addr.address if self._last_addr else None

    def reset(self) -> None:
        """Clear remembered state. Called on disable, config change, or
        when the watcher knows the clipboard was written by ClipWarden
        itself (first-party suppression lives in the watcher)."""
        self._last_addr = None
        self._last_ts_ms = None

    def observe(
        self,
        text: str,
        ts_ms: int,
        last_input_ts_ms: int,
    ) -> DetectionEvent | None:
        """Feed one clipboard sample in. Returns an event if the sample
        completes a substitution pair, otherwise ``None``.

        Parameters are absolute millisecond timestamps in a shared
        monotonic frame. See module docstring for the contract.
        """
        classified = classify(text, self._enabled_chains)
        if classified is None:
            # Non-address text preserves the "last address" memory so a
            # laundered substitution (A -> junk -> B) still alerts.
            return None

        prev_addr = self._last_addr
        prev_ts = self._last_ts_ms

        # We always advance to the most recent classified copy. That
        # keeps the window anchored on the freshest known-good copy,
        # which is what "substitution since last known-good" means.
        self._last_addr = classified
        self._last_ts_ms = ts_ms

        if prev_addr is None or prev_ts is None:
            return None

        if prev_addr.address == classified.address:
            # User re-copied the same address. Not a substitution. The
            # state update above already rolled the window forward.
            return None

        if prev_addr.chain != classified.chain:
            # Cross-chain transition. The attacker model is "swap BTC
            # for BTC", not "swap BTC for ETH" - wallets wouldn't accept
            # cross-chain paste anyway.
            return None

        elapsed = ts_ms - prev_ts
        if elapsed < 0:
            # Backwards time. Can happen with clock changes in theory
            # (monotonic frame should not regress, but defensive).
            return None
        if elapsed > self._window_ms:
            return None

        if last_input_ts_ms > prev_ts:
            # User typed or clicked after the previous copy. Treat as
            # deliberate recopy, not a hijack. Boundary is strict >:
            # input exactly at prev_ts is ambiguous and we err on the
            # side of alerting (safer failure mode for a security tool).
            return None

        return DetectionEvent(
            ts_ms=ts_ms,
            chain=classified.chain.value,
            before=prev_addr.address,
            after=classified.address,
            elapsed_ms=elapsed,
            whitelisted=self._is_whitelisted(classified.chain.value, classified.address),
        )
