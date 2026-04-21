from __future__ import annotations

import json
from pathlib import Path

from clipwarden.whitelist import Whitelist, WhitelistEntry, WhitelistError


def test_add_and_contains_exact_match() -> None:
    wl = Whitelist()
    wl.add("BTC", "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa")
    assert wl.contains("BTC", "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa")
    assert not wl.contains("BTC", "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2")
    assert not wl.contains("ETH", "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa")


def test_eth_lookup_is_case_insensitive() -> None:
    wl = Whitelist()
    wl.add("ETH", "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045")
    # Same 20-byte destination, different checksum case
    assert wl.contains("ETH", "0xd8da6bf26964af9d7eed9e03e53415d37aa96045")
    assert wl.contains("ETH", "0XD8DA6BF26964AF9D7EED9E03E53415D37AA96045")


def test_btc_base58_case_sensitive() -> None:
    wl = Whitelist()
    wl.add("BTC", "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa")
    # Case-changed base58 is a completely different address
    assert not wl.contains("BTC", "1a1ZP1EP5qgEFI2dmptFtl5slMV7dIVFnA")


def test_btc_bech32_case_insensitive() -> None:
    wl = Whitelist()
    wl.add("BTC", "BC1QW508D6QEJXTDG4Y5R3ZARVARY0C5XW7KV8F3T4")
    assert wl.contains("BTC", "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4")


def test_remove() -> None:
    wl = Whitelist()
    wl.add("BTC", "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa")
    assert wl.remove("BTC", "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa") is True
    assert not wl.contains("BTC", "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa")
    assert wl.remove("BTC", "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa") is False


def test_entries_sorted_newest_first() -> None:
    # Construct directly so the test doesn't spend a second sleeping
    # just to get two distinct ISO-8601 second-precision timestamps.
    wl = Whitelist(
        [
            WhitelistEntry(
                chain="BTC",
                address="1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa",
                added_at="2026-01-01T00:00:00+00:00",
                note="older",
            ),
            WhitelistEntry(
                chain="BTC",
                address="1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2",
                added_at="2026-02-01T00:00:00+00:00",
                note="newer",
            ),
        ]
    )
    entries = wl.entries()
    assert entries[0].note == "newer"
    assert entries[1].note == "older"


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    p = tmp_path / "whitelist.json"
    wl = Whitelist()
    wl.add("BTC", "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa", note="cold storage")
    wl.add("ETH", "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045")
    wl.save(p)

    reloaded = Whitelist.load(p)
    assert reloaded.contains("BTC", "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa")
    assert reloaded.contains("ETH", "0xd8da6bf26964af9d7eed9e03e53415d37aa96045")
    assert len(reloaded) == 2


def test_load_missing_file(tmp_path: Path) -> None:
    wl = Whitelist.load(tmp_path / "nope.json")
    assert len(wl) == 0


def test_load_missing_file_persists_empty_whitelist(tmp_path: Path) -> None:
    """First-run load should create the file so subsequent tooling can assume it."""
    p = tmp_path / "whitelist.json"
    wl = Whitelist.load(p)
    assert len(wl) == 0
    assert p.exists()
    round_trip = Whitelist.load(p)
    assert len(round_trip) == 0


def test_load_missing_file_survives_persist_failure(tmp_path, monkeypatch) -> None:
    """A read-only profile must not prevent startup, only skip the persist."""
    import clipwarden.whitelist as wlmod  # noqa: PLC0415

    def boom(self, _path):
        raise OSError("simulated read-only profile")

    monkeypatch.setattr(wlmod.Whitelist, "save", boom)
    wl = Whitelist.load(tmp_path / "nope.json")
    assert len(wl) == 0


def test_load_corrupt_json_returns_empty(tmp_path: Path) -> None:
    p = tmp_path / "whitelist.json"
    p.write_text("{not json", encoding="utf-8")
    wl = Whitelist.load(p)
    assert len(wl) == 0


def test_load_non_object_root_returns_empty(tmp_path: Path) -> None:
    p = tmp_path / "whitelist.json"
    p.write_text("[]", encoding="utf-8")
    assert len(Whitelist.load(p)) == 0


def test_load_entries_not_list_returns_empty(tmp_path: Path) -> None:
    p = tmp_path / "whitelist.json"
    p.write_text(json.dumps({"entries": "nope"}), encoding="utf-8")
    assert len(Whitelist.load(p)) == 0


def test_load_corrupt_json_backs_up_file(tmp_path: Path) -> None:
    """Unparseable JSON is renamed aside and replaced with an empty file.

    Silently returning an empty whitelist and then having the next
    save() clobber the bad file would destroy user-trusted pairs
    without evidence. The backup file is the user's escape hatch;
    the primary path is immediately re-persisted so downstream
    tooling can assume the file exists.
    """
    p = tmp_path / "whitelist.json"
    p.write_text("{not json", encoding="utf-8")
    wl = Whitelist.load(p)
    assert len(wl) == 0
    assert p.exists(), "primary path must be re-persisted after backup"
    assert json.loads(p.read_text(encoding="utf-8")) == {"entries": []}
    backups = sorted(tmp_path.glob("whitelist.json.bak-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{not json"


def test_load_non_object_root_backs_up_file(tmp_path: Path) -> None:
    p = tmp_path / "whitelist.json"
    p.write_text("[]", encoding="utf-8")
    assert len(Whitelist.load(p)) == 0
    assert p.exists()
    assert json.loads(p.read_text(encoding="utf-8")) == {"entries": []}
    assert len(list(tmp_path.glob("whitelist.json.bak-*"))) == 1


def test_load_entries_not_list_backs_up_file(tmp_path: Path) -> None:
    p = tmp_path / "whitelist.json"
    p.write_text(json.dumps({"entries": "nope"}), encoding="utf-8")
    assert len(Whitelist.load(p)) == 0
    assert p.exists()
    assert json.loads(p.read_text(encoding="utf-8")) == {"entries": []}
    assert len(list(tmp_path.glob("whitelist.json.bak-*"))) == 1


def test_whitelist_error_is_value_error() -> None:
    # A caller catching ValueError still handles this; subclassing is
    # documented API so external loaders can distinguish the
    # corrupt-file case.
    assert issubclass(WhitelistError, ValueError)


def test_load_skips_malformed_entries(tmp_path: Path) -> None:
    p = tmp_path / "whitelist.json"
    payload = {
        "entries": [
            # Missing chain key - must be skipped
            {"address": "abc", "added_at": "2026-01-01T00:00:00+00:00"},
            # Not even an object - must be skipped
            "oops",
            # Valid
            {
                "chain": "BTC",
                "address": "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa",
                "added_at": "2026-01-01T00:00:00+00:00",
            },
        ],
    }
    p.write_text(json.dumps(payload), encoding="utf-8")
    wl = Whitelist.load(p)
    assert len(wl) == 1
    assert wl.contains("BTC", "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa")


def test_add_rejects_mismatched_chain() -> None:
    """Chain/address classifier disagreement must raise.

    Callers that try to whitelist an ETH address under the BTC chain
    would never produce a matching entry; silently accepting the row
    would leave the user expecting protection they do not have.
    """
    wl = Whitelist()
    import pytest  # noqa: PLC0415

    with pytest.raises(WhitelistError):
        wl.add("BTC", "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045")
    with pytest.raises(WhitelistError):
        wl.add("ETH", "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa")


def test_add_rejects_garbage_address() -> None:
    wl = Whitelist()
    import pytest  # noqa: PLC0415

    with pytest.raises(WhitelistError):
        wl.add("BTC", "not an address")


def test_load_normalizes_chain_case(tmp_path: Path) -> None:
    """Hand-edited ``"chain": "btc"`` must be treated as canonical BTC.

    Without normalization the row keys to ``("btc", ...)`` while
    detections key to ``("BTC", ...)`` so the entry would never match.
    """
    p = tmp_path / "whitelist.json"
    payload = {
        "entries": [
            {
                "chain": "btc",
                "address": "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa",
                "added_at": "2026-01-01T00:00:00+00:00",
            },
        ],
    }
    p.write_text(json.dumps(payload), encoding="utf-8")
    wl = Whitelist.load(p)
    assert wl.contains("BTC", "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa")


def test_load_drops_classifier_mismatches(tmp_path: Path, caplog) -> None:
    """A hand-edited entry whose chain disagrees with the classifier is dropped.

    Silently storing a useless row would leave the user thinking
    their address is protected. The entry is removed, the rest of
    the file is preserved, and a warning line is written so a user
    who checks the diagnostic log sees what happened.
    """
    import logging  # noqa: PLC0415

    p = tmp_path / "whitelist.json"
    payload = {
        "entries": [
            # ETH address claimed as BTC - dropped on load
            {
                "chain": "BTC",
                "address": "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045",
                "added_at": "2026-01-01T00:00:00+00:00",
            },
            # Valid pair - kept
            {
                "chain": "ETH",
                "address": "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045",
                "added_at": "2026-01-02T00:00:00+00:00",
            },
        ],
    }
    p.write_text(json.dumps(payload), encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="clipwarden.whitelist"):
        wl = Whitelist.load(p)
    assert len(wl) == 1
    assert wl.contains("ETH", "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045")
    assert any("dropping invalid entry" in rec.message for rec in caplog.records)
