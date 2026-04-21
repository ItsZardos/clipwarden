"""Exact-address whitelist.

Scope is deliberately minimal: a user can mark a specific (chain,
address) pair as trusted, and detections targeting that pair are logged
as ``whitelisted_skip`` instead of surfaced as alerts. We do not offer
"trust this entire chain" or "trust by prefix"; those are footguns that
defeat the tool.

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
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path


class WhitelistError(ValueError):
    """Raised when a whitelist file is present but malformed."""


@dataclass(frozen=True)
class WhitelistEntry:
    chain: str
    address: str
    added_at: str  # ISO 8601 UTC
    note: str = ""


def _normalize(chain: str, address: str) -> str:
    # Case sensitivity by chain:
    # - ETH: hex is hex; lowercased is canonical.
    # - BTC bech32: lowercase per BIP-173 (the BTC_BECH32 shape regex
    #   already tolerates BC1/bc1 prefix, but the payload charset is
    #   lowercase by spec). We lowercase to be safe.
    # - BTC base58, SOL, XMR: case matters.
    if chain == "ETH":
        return address.lower()
    if chain == "BTC" and address.lower().startswith("bc1"):
        return address.lower()
    return address


class Whitelist:
    def __init__(self, entries: list[WhitelistEntry] | None = None) -> None:
        self._entries: dict[tuple[str, str], WhitelistEntry] = {}
        for e in entries or []:
            self._entries[(e.chain, _normalize(e.chain, e.address))] = e

    def __len__(self) -> int:
        return len(self._entries)

    def contains(self, chain: str, address: str) -> bool:
        return (chain, _normalize(chain, address)) in self._entries

    def add(self, chain: str, address: str, note: str = "") -> WhitelistEntry:
        entry = WhitelistEntry(
            chain=chain,
            address=address,
            added_at=datetime.now(UTC).isoformat(timespec="seconds"),
            note=note,
        )
        self._entries[(chain, _normalize(chain, address))] = entry
        return entry

    def remove(self, chain: str, address: str) -> bool:
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

        Missing file -> empty whitelist (silent; first-run behaviour).
        Bad JSON or shape -> file is renamed aside to
        ``<path>.bak-<ts>`` and an empty whitelist is returned. Within
        a file that parses, individual malformed entries are skipped
        rather than nuking the rest; that matches the "Settings UI
        shows the rest" UX contract.
        """
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise WhitelistError("whitelist root must be an object")
            items = raw.get("entries", [])
            if not isinstance(items, list):
                raise WhitelistError("whitelist.entries must be a list")
        except (OSError, json.JSONDecodeError, WhitelistError):
            # If the rename itself fails (e.g. the file is locked), an
            # empty whitelist is still the safer outcome: on next save
            # the good file reappears and the user can recover manually.
            with contextlib.suppress(OSError):
                _backup_corrupt(path)
            return cls()
        parsed: list[WhitelistEntry] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                parsed.append(
                    WhitelistEntry(
                        chain=str(item["chain"]),
                        address=str(item["address"]),
                        added_at=str(item["added_at"]),
                        note=str(item.get("note", "")),
                    )
                )
            except KeyError:
                # Skip malformed entries rather than nuking the whole file.
                # The Settings UI will show the rest and the user can re-add.
                continue
        return cls(parsed)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"entries": [asdict(e) for e in self.entries()]}
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)


def _backup_corrupt(path: Path) -> Path:
    # Millisecond suffix matches config.py so rapid successive
    # corruptions don't clobber each other's backups and the two
    # modules stay diff-shaped.
    ts = int(time.time() * 1000)
    target = path.with_suffix(path.suffix + f".bak-{ts}")
    path.rename(target)
    return target
