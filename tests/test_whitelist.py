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
    """Unparseable JSON is renamed aside rather than silently overwritten.

    Silently returning an empty whitelist and then having the next
    save() clobber the bad file would destroy user-trusted pairs
    without evidence. The backup file is the user's escape hatch.
    """
    p = tmp_path / "whitelist.json"
    p.write_text("{not json", encoding="utf-8")
    wl = Whitelist.load(p)
    assert len(wl) == 0
    assert not p.exists()
    backups = sorted(tmp_path.glob("whitelist.json.bak-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{not json"


def test_load_non_object_root_backs_up_file(tmp_path: Path) -> None:
    p = tmp_path / "whitelist.json"
    p.write_text("[]", encoding="utf-8")
    assert len(Whitelist.load(p)) == 0
    assert not p.exists()
    assert len(list(tmp_path.glob("whitelist.json.bak-*"))) == 1


def test_load_entries_not_list_backs_up_file(tmp_path: Path) -> None:
    p = tmp_path / "whitelist.json"
    p.write_text(json.dumps({"entries": "nope"}), encoding="utf-8")
    assert len(Whitelist.load(p)) == 0
    assert not p.exists()
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
