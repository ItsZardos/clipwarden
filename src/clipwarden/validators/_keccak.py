"""Keccak-256 (pre-standard Keccak, not NIST SHA3-256).

Ethereum's EIP-55 mixed-case checksum is defined against Keccak-256 with
the original Keccak padding (``0x01`` delimiter, then ``0x80`` on the
last byte), not against the NIST-standardised SHA3-256 which uses the
``0x06`` delimiter. ``hashlib.sha3_256`` will produce a different digest
and is therefore unsuitable here.

Reference: Keccak team specification summary at https://keccak.team/
(rotation offsets and round constants for Keccak-f[1600] match the
original submission). A short Python reference also lives in the
Ethereum wiki and in eth-hash's pure backend.

Scope: this module is used only to compute the display checksum for
Ethereum addresses. It is not intended for signing, key derivation, or
any setting where constant-time or side-channel-hardened hashing is
required. If one of those requirements shows up later, swap this out
for a native implementation. Keeping it vendored today avoids a
transitive dependency on pycryptodome just to hash ~40 bytes per copy.
"""

from __future__ import annotations

_MASK64 = (1 << 64) - 1

_RC = (
    0x0000000000000001,
    0x0000000000008082,
    0x800000000000808A,
    0x8000000080008000,
    0x000000000000808B,
    0x0000000080000001,
    0x8000000080008081,
    0x8000000000008009,
    0x000000000000008A,
    0x0000000000000088,
    0x0000000080008009,
    0x000000008000000A,
    0x000000008000808B,
    0x800000000000008B,
    0x8000000000008089,
    0x8000000000008003,
    0x8000000000008002,
    0x8000000000000080,
    0x000000000000800A,
    0x800000008000000A,
    0x8000000080008081,
    0x8000000000008080,
    0x0000000080000001,
    0x8000000080008008,
)

# Rotation offsets ROT[x][y] for Keccak-f[1600].
_ROT = (
    (0, 36, 3, 41, 18),
    (1, 44, 10, 45, 2),
    (62, 6, 43, 15, 61),
    (28, 55, 25, 21, 56),
    (27, 20, 39, 8, 14),
)


def _rotl(x: int, n: int) -> int:
    return ((x << n) | (x >> (64 - n))) & _MASK64


def _keccak_f(state: list[list[int]]) -> None:
    for rnd in range(24):
        # Theta: C[x] = XOR over y of A[x][y]
        c = [state[x][0] ^ state[x][1] ^ state[x][2] ^ state[x][3] ^ state[x][4] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rotl(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                state[x][y] ^= d[x]

        # Rho + Pi: B[y][(2x + 3y) mod 5] = rot(A[x][y], r[x][y])
        b = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                b[y][(2 * x + 3 * y) % 5] = _rotl(state[x][y], _ROT[x][y])

        # Chi
        for x in range(5):
            for y in range(5):
                state[x][y] = b[x][y] ^ ((~b[(x + 1) % 5][y]) & b[(x + 2) % 5][y]) & _MASK64

        # Iota
        state[0][0] ^= _RC[rnd]


def keccak256(data: bytes) -> bytes:
    """Return the 32-byte Keccak-256 digest of ``data``."""
    rate_bytes = 136  # 1088-bit rate, 512-bit capacity for Keccak-256

    padded = bytearray(data)
    padded.append(0x01)
    while len(padded) % rate_bytes != 0:
        padded.append(0x00)
    padded[-1] |= 0x80

    state = [[0] * 5 for _ in range(5)]

    for block_start in range(0, len(padded), rate_bytes):
        block = padded[block_start : block_start + rate_bytes]
        for i in range(rate_bytes // 8):
            lane = int.from_bytes(block[i * 8 : (i + 1) * 8], "little")
            x = i % 5
            y = i // 5
            state[x][y] ^= lane
        _keccak_f(state)

    out = bytearray()
    for i in range(4):  # 256 bits / 64-bit lanes = 4 lanes
        x = i % 5
        y = i // 5
        out.extend(state[x][y].to_bytes(8, "little"))
    return bytes(out)
