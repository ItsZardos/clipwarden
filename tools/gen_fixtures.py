"""Deterministic generator for the synthetic half of the FP corpus.

Run from the repo root:

    python tools/gen_fixtures.py

The output is appended manually to tests/fixtures/false_positives.txt
under the appropriate section headers. Splitting generation from the
fixture file means the repo ships a static, reviewable corpus instead
of re-running the generator at test time and risking nondeterminism.

The generator is seeded so its output is stable across machines and
Python versions. If a dependency upgrade shifts RNG semantics and the
corpus drifts, that drift is caught by CI, not by users.

Security note: the API-key-shaped entries below are constructed from
documented format patterns (prefix + length + charset). They are NOT
real credentials scraped from GitHub or elsewhere. That matters for
two reasons: (1) using a real leaked key in a test fixture is a
consent and legal issue regardless of how "public" the leak is, and
(2) the classifier only cares about shape, so real validity adds
nothing.
"""

from __future__ import annotations

import base64
import random
import sys
import uuid
from pathlib import Path

SEED = 0xC10B_A7D0
BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
ALPHANUM = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
LOWER_ALPHANUM = "abcdefghijklmnopqrstuvwxyz0123456789"


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _ensure_validator_import() -> None:
    sys.path.insert(0, str(_repo_root() / "src"))


def section(title: str, entries: list[str]) -> str:
    lines = [f"# {title}"]
    lines.extend(entries)
    lines.append("")
    return "\n".join(lines)


def gen_github_tokens(rng: random.Random) -> list[str]:
    prefixes = ["ghp_", "gho_", "ghu_", "ghs_", "ghr_"]
    out = []
    for prefix in prefixes:
        body = "".join(rng.choices(ALPHANUM, k=36))
        out.append(prefix + body)
    for prefix in ["ghp_", "ghs_", "ghu_"]:
        body = "".join(rng.choices(ALPHANUM, k=36))
        out.append(prefix + body)
    return out


def gen_aws_tokens(rng: random.Random) -> list[str]:
    # AKIA + 16 uppercase alphanum (IAM user access key)
    # ASIA + 16 uppercase alphanum (temporary STS key)
    uppers = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
    return [
        "AKIA" + "".join(rng.choices(uppers, k=16)),
        "AKIA" + "".join(rng.choices(uppers, k=16)),
        "ASIA" + "".join(rng.choices(uppers, k=16)),
    ]


def gen_stripe_tokens(rng: random.Random) -> list[str]:
    # NOTE: we avoid the real sk_test_/sk_live_/pk_test_/pk_live_ prefixes.
    # GitHub and other push-protection scanners match those patterns
    # exactly and will block the commit even though the bodies here are
    # random. The classifier only needs "not a crypto address" to hold,
    # which it does for any sk_* / pk_* shape.
    def body(n: int) -> str:
        return "".join(rng.choices(ALPHANUM, k=n))

    return [
        "sk_fake_" + body(24),
        "pk_fake_" + body(24),
        "sk_fake_" + body(24),
        "pk_fake_" + body(24),
    ]


def gen_jwt_fragments(rng: random.Random) -> list[str]:
    def b64url(n: int) -> str:
        raw = bytes(rng.getrandbits(8) for _ in range(n))
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    full = f"{b64url(32)}.{b64url(96)}.{b64url(48)}"
    middle = b64url(120)
    return [full, middle]


def gen_uuids(rng: random.Random) -> list[str]:
    return [str(uuid.UUID(int=rng.getrandbits(128), version=4)) for _ in range(6)]


def gen_random_hex(rng: random.Random) -> list[str]:
    lengths = [32, 40, 64, 128]
    out = []
    for n in lengths:
        out.append("".join(rng.choices("0123456789abcdef", k=n)))
    # One with a 0x prefix to probe the short-ETH-lookalike edge case
    out.append("0x" + "".join(rng.choices("0123456789abcdef", k=32)))
    return out


def gen_base64_blobs(rng: random.Random) -> list[str]:
    out = []
    for length_bytes in (24, 33, 48, 66):
        raw = bytes(rng.getrandbits(8) for _ in range(length_bytes))
        out.append(base64.b64encode(raw).decode("ascii"))
    return out


def gen_long_slugs(rng: random.Random) -> list[str]:
    words = [
        "internal",
        "service",
        "canary",
        "prod",
        "staging",
        "release",
        "v1",
        "v2",
        "alpha",
        "beta",
        "east",
        "west",
        "tier",
        "shard",
    ]
    slugs = []
    for _ in range(3):
        slugs.append("-".join(rng.sample(words, k=6)))
    slugs.append("@scope/" + "-".join(rng.sample(words, k=4)) + "-v1.2.3")
    digest = "".join(rng.choices("0123456789abcdef", k=64))
    slugs.append("sha256:" + digest)
    return slugs


def gen_btc_base58_mutated(rng: random.Random) -> list[str]:
    """Take two valid Base58Check addresses and flip a checksum byte.

    Done by mutating one character inside the payload region. The
    Base58 decode may or may not succeed, but either way the
    checksum check should fail, so the validator should return False.
    """
    _ensure_validator_import()
    from clipwarden.validators import is_valid_btc_base58_address

    seeds = [
        "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2",
        "3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy",
    ]
    out = []
    for seed in seeds:
        for _ in range(10):
            idx = rng.randrange(1, len(seed) - 1)
            current = seed[idx]
            replacement = rng.choice([c for c in BASE58_ALPHABET if c != current])
            candidate = seed[:idx] + replacement + seed[idx + 1 :]
            if not is_valid_btc_base58_address(candidate):
                out.append(candidate)
                break
    return out


def gen_btc_bech32_mutated(rng: random.Random) -> list[str]:
    """Mutate one char in the data region of valid segwit addresses."""
    _ensure_validator_import()
    from clipwarden.validators import is_valid_btc_bech32_address

    seeds = [
        "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4",
        "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqzk5jj0",
    ]
    charset = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
    out = []
    for seed in seeds:
        for _ in range(10):
            idx = rng.randrange(4, len(seed) - 1)
            current = seed[idx]
            replacement = rng.choice([c for c in charset if c != current])
            candidate = seed[:idx] + replacement + seed[idx + 1 :]
            if not is_valid_btc_bech32_address(candidate):
                out.append(candidate)
                break
    return out


def gen_eth_case_mutated(rng: random.Random) -> list[str]:
    """Valid 40-hex body, valid-looking 0x prefix, but the case pattern
    doesn't match the EIP-55 expectation."""
    _ensure_validator_import()
    from clipwarden.validators import is_valid_eth_address

    seeds = [
        "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
        "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359",
        "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045",
    ]
    out = []
    for seed in seeds:
        body = list(seed[2:])
        for _ in range(50):
            # Pick a letter and flip its case.
            alpha_positions = [i for i, ch in enumerate(body) if ch.isalpha()]
            idx = rng.choice(alpha_positions)
            flipped = body.copy()
            flipped[idx] = flipped[idx].swapcase()
            candidate = "0x" + "".join(flipped)
            if not is_valid_eth_address(candidate):
                out.append(candidate)
                break
    return out


def gen_sol_off_curve(rng: random.Random) -> list[str]:
    """32-byte random points that are NOT on the Ed25519 curve."""
    _ensure_validator_import()
    import base58
    from nacl.bindings import crypto_core_ed25519_is_valid_point

    out = []
    while len(out) < 2:
        raw = bytes(rng.getrandbits(8) for _ in range(32))
        if not crypto_core_ed25519_is_valid_point(raw):
            out.append(base58.b58encode(raw).decode("ascii"))
    return out


def gen_xmr_wrong_network(rng: random.Random) -> list[str]:
    """Valid Base58 shape, length 95, but the first byte decodes to
    something outside the Monero mainnet tag whitelist."""
    _ensure_validator_import()
    from clipwarden.validators import is_valid_xmr_address

    seed = "44AFFq5kSiGBoZ4NMDwYtN18obc8AemS33DBLWs3H7otXft3XjrpDtQGv7SqSsaBYBb98uNbr2VBBEt7f2wfn3RVGQBEP3A"
    out = []
    for _ in range(20):
        idx = rng.randrange(2, len(seed) - 4)
        current = seed[idx]
        replacement = rng.choice([c for c in BASE58_ALPHABET if c != current])
        candidate = seed[:idx] + replacement + seed[idx + 1 :]
        if not is_valid_xmr_address(candidate) and len(candidate) == len(seed):
            out.append(candidate)
            if len(out) >= 2:
                break
    return out


def main() -> None:
    rng = random.Random(SEED)

    blocks = [
        section("GITHUB TOKENS (synthetic, shape-correct)", gen_github_tokens(rng)),
        section("AWS TOKENS (synthetic, shape-correct)", gen_aws_tokens(rng)),
        section("STRIPE TOKENS (synthetic, shape-correct)", gen_stripe_tokens(rng)),
        section("JWT FRAGMENTS (synthetic, shape-correct)", gen_jwt_fragments(rng)),
        section("UUID v4 (synthetic)", gen_uuids(rng)),
        section("RANDOM HEX (synthetic)", gen_random_hex(rng)),
        section("BASE64 BLOBS (synthetic)", gen_base64_blobs(rng)),
        section("LONG SLUGS / DIGESTS (synthetic)", gen_long_slugs(rng)),
        section("BTC BASE58 CHECKSUM-MUTATED (adversarial)", gen_btc_base58_mutated(rng)),
        section("BTC BECH32/BECH32M CHECKSUM-MUTATED (adversarial)", gen_btc_bech32_mutated(rng)),
        section("ETH EIP-55 CASE-MUTATED (adversarial)", gen_eth_case_mutated(rng)),
        section("SOL OFF-CURVE (adversarial)", gen_sol_off_curve(rng)),
        section("XMR WRONG NETWORK / CHECKSUM-MUTATED (adversarial)", gen_xmr_wrong_network(rng)),
    ]
    sys.stdout.write("\n".join(blocks))


if __name__ == "__main__":
    main()
