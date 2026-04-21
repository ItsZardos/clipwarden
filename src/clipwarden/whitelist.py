"""Exact-address whitelist.

Scope is deliberately minimal: a user can mark a specific (chain,
address) pair as trusted, and detections targeting that pair are logged
as ``whitelisted_skip`` instead of surfaced as alerts. We do not offer
"trust this entire chain" or "trust by prefix"; those are broad
categories that would defeat the tool by whitelisting attacker-
controlled addresses alongside the user's intended destination.

ETH addresses are normalised to lowercase for lookup. EIP-55 mixed-case
is cosmetic; the underlying destination is the same 20-byte value. The
original form (as the user entered it) is preserved for display.

Corrupt-file policy mirrors :mod:`clipwarden.config`: if
``whitelist.json`` exists but won't parse or has the wrong shape, the
bad file is renamed to ``whitelist.json.bak-<ts>`` and we fall back to
an empty whitelist. Silently dropping trusted-address data without
evidence would be a worse failure mode for a security tool than a
backed-up reset the user can recover from.
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from .classifier import classify

log = logging.getLogger(__name__)


class WhitelistError(ValueError):
    """Raised when a whitelist file is present but malformed."""


@dataclass(frozen=True)
class WhitelistEntry:
    chain: str
    address: str
    added_at: str  # ISO 8601 UTC
    note: str = ""


def _normalize_chain(chain: str) -> str:
    """Normalize the chain token to its canonical uppercase form."""
    return chain.strip().upper()


def _normalize(chain: str, address: str) -> str:
    # Case sensitivity by chain:
    # - ETH: hex is hex; lowercased is canonical.
    # - BTC bech32: lowercase per BIP-173 (the BTC_BECH32 shape regex
    #   already tolerates BC1/bc1 prefix, but the payload charset is
    #   lowercase by spec). We lowercase to be safe.
    # - BTC base58, SOL, XMR: case matters.
    chain = _normalize_chain(chain)
    if chain == "ETH":
        return address.lower()
    if chain == "BTC" and address.lower().startswith("bc1"):
        return address.lower()
    return address


def _validate_pair(chain: str, address: str) -> str:
    """Return the canonical chain token for ``(chain, address)``.

    Raises :class:`WhitelistError` if the classifier disagrees with
    the claimed chain or if the address fails validation for any
    chain. Used by ``Whitelist.add`` so API callers cannot silently
    store a mismatched pair, and by ``Whitelist.load`` so hand-edited
    JSON files surface the mismatch loudly instead of storing a row
    that can never match a real detection.
    """
    canonical = _normalize_chain(chain)
    classified = classify(address)
    if classified is None:
        raise WhitelistError(
            f"address does not validate for any supported chain: {address!r}"
        )
    if classified.chain.value != canonical:
        raise WhitelistError(
            f"claimed chain {canonical!r} does not match classifier "
            f"result {classified.chain.value!r} for address {address!r}"
        )
    return canonical


class Whitelist:
    def __init__(self, entries: list[WhitelistEntry] | None = None) -> None:
        self._entries: dict[tuple[str, str], WhitelistEntry] = {}
        for e in entries or []:
            chain = _normalize_chain(e.chain)
            self._entries[(chain, _normalize(chain, e.address))] = e

    def __len__(self) -> int:
        return len(self._entries)

    def contains(self, chain: str, address: str) -> bool:
        chain = _normalize_chain(chain)
        return (chain, _normalize(chain, address)) in self._entries

    def add(self, chain: str, address: str, note: str = "") -> WhitelistEntry:
        """Add ``(chain, address)`` to the whitelist.

        Raises :class:`WhitelistError` if the classifier disagrees
        with the claimed chain so API callers cannot silently store a
        pair that would never match a real detection.
        """
        canonical = _validate_pair(chain, address)
        entry = WhitelistEntry(
            chain=canonical,
            address=address,
            added_at=datetime.now(UTC).isoformat(timespec="seconds"),
            note=note,
        )
        self._entries[(canonical, _normalize(canonical, address))] = entry
        return entry

    def remove(self, chain: str, address: str) -> bool:
        chain = _normalize_chain(chain)
        key = (chain, _normalize(chain, address))
        if key in self._entries:
            del self._entries[key]
            return True
        return False

    def entries(self) -> list[WhitelistEntry]:
        # Return newest-first so the settings window shows recently-added
        # pairs at the top, which is what users expect.
        return sorted(self._entries.values(), key=lambda e: e.added_at, reverse=True)

    @classmethod
    def load(cls, path: Path) -> Whitelist:
        """Load whitelist from disk, falling back to an empty whitelist.

        Missing file -> empty whitelist, and the empty state is
        immediately persisted so the user has an editable file on
        disk and later tooling can assume ``path`` exists.
        Bad JSON or shape -> file is renamed aside to
        ``<path>.bak-<ts>`` and an empty whitelist is returned. Within
        a file that parses, individual malformed entries are skipped
        rather than nuking the rest; that matches the "Settings UI
        shows the rest" UX contract.
        """
        if not path.exists():
            wl = cls()
            # Best-effort persist. A read-only profile or antivirus
            # lock must not prevent startup; in-memory state is still
            # valid and the next Whitelist.save() will create the
            # file.
            with contextlib.suppress(OSError):
                wl.save(path)
            return wl
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise WhitelistError("whitelist root must be an object")
            items = raw.get("entries", [])
            if not isinstance(items, list):
                raise WhitelistError("whitelist.entries must be a list")
        except (OSError, json.JSONDecodeError, WhitelistError):
            # Rename the bad file aside, then re-persist an empty
            # whitelist so the on-disk state is consistent. A read-only
            # profile will fail the save; in-memory state is still
            # valid and the next successful save reappears the file.
            wl = cls()
            with contextlib.suppress(OSError):
                _backup_corrupt(path)
            with contextlib.suppress(OSError):
                wl.save(path)
            return wl
        parsed: list[WhitelistEntry] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                chain_raw = str(item["chain"])
                address = str(item["address"])
                added_at = str(item["added_at"])
                note = str(item.get("note", ""))
            except KeyError:
                # Skip malformed entries rather than nuking the whole file.
                # The Settings UI will show the rest and the user can re-add.
                continue
            try:
                canonical = _validate_pair(chain_raw, address)
            except WhitelistError as exc:
                log.warning(
                    "whitelist: dropping invalid entry chain=%r address=%r: %s",
                    chain_raw,
                    address,
                    exc,
                )
                continue
            parsed.append(
                WhitelistEntry(
                    chain=canonical,
                    address=address,
                    added_at=added_at,
                    note=note,
                )
            )
        return cls(parsed)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"entries": [asdict(e) for e in self.entries()]}
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)


def _backup_corrupt(path: Path) -> Path:
    # Millisecond suffix matches config.py so rapid successive
    # corruptions do not clobber each other's backups.
    ts = int(time.time() * 1000)
    target = path.with_suffix(path.suffix + f".bak-{ts}")
    path.rename(target)
    return target
