"""Emit SHA-256 checksums for ClipWarden release artifacts.

Prints one line per artifact in a stable format suitable for pasting
into a GitHub release body or storing next to the binaries::

    ClipWarden.exe              SHA-256: <64-hex>
    ClipWarden-Setup-1.0.0.exe  SHA-256: <64-hex>

Exits non-zero if either expected artifact is missing under
``dist/``; the script is designed to be called from the packaging
Makefile/CI step right after PyInstaller + Inno Setup have run, so a
missing file is a real failure and must surface loudly.

No external dependencies. Hashing is streamed in 1 MiB chunks so the
20+ MB one-file bootloader doesn't spike memory on a build agent.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

_CHUNK = 1 << 20  # 1 MiB streaming buffer


def _read_version() -> str:
    """Resolve ClipWarden's version without importing the package.

    We avoid importing ``clipwarden`` so this script runs in a bare
    environment (fresh checkout, no ``pip install -e .``) and so
    packaging CI does not accidentally depend on a working wheel.
    """
    here = Path(__file__).resolve()
    init = here.parent.parent / "src" / "clipwarden" / "__init__.py"
    for line in init.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("__version__"):
            # __version__ = "1.0.0"
            _, _, rhs = s.partition("=")
            return rhs.strip().strip('"').strip("'")
    raise SystemExit("could not locate __version__ in clipwarden/__init__.py")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    dist = root / "dist"
    version = _read_version()
    targets = [
        dist / "ClipWarden.exe",
        dist / f"ClipWarden-Setup-{version}.exe",
    ]

    missing = [t for t in targets if not t.is_file()]
    if missing:
        for m in missing:
            print(f"missing: {m}", file=sys.stderr)
        return 1

    width = max(len(t.name) for t in targets) + 2
    for t in targets:
        print(f"{t.name.ljust(width)}SHA-256: {_sha256(t)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
