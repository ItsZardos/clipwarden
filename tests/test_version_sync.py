"""Cross-file version sync guard.

ClipWarden's marketing version appears in three places:

* ``src/clipwarden/__init__.py`` -- runtime ``__version__``.
* ``pyproject.toml`` -- wheel and sdist metadata.
* ``build/version_info.txt`` -- Windows PE FileVersion / ProductVersion
  that Explorer's Properties pane and SmartScreen read.

The release workflow bumps these together; this test makes the "bumped
one but forgot the others" failure mode show up in CI instead of in a
signed installer. Stripping prereleases keeps the comparison aligned
with the four-tuple format that ``VSVersionInfo`` requires.
"""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _read_init_version() -> str:
    init = REPO_ROOT / "src" / "clipwarden" / "__init__.py"
    tree = ast.parse(init.read_text(encoding="utf-8"), filename=str(init))
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "__version__" for t in node.targets)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            return node.value.value
    raise AssertionError("__version__ not found in clipwarden/__init__.py")


def _read_pyproject_version() -> str:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["version"]


def _read_version_info_tuple() -> tuple[int, int, int, int]:
    text = (REPO_ROOT / "build" / "version_info.txt").read_text(encoding="utf-8")
    m = re.search(r"filevers=\((\d+),\s*(\d+),\s*(\d+),\s*(\d+)\)", text)
    assert m is not None, "filevers tuple missing from version_info.txt"
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)))


def _numeric_core(version: str) -> tuple[int, int, int]:
    """Extract MAJOR.MINOR.PATCH, discarding any prerelease/build suffix."""
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)", version)
    assert m is not None, f"version {version!r} does not start with X.Y.Z"
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def test_init_and_pyproject_versions_match():
    assert _read_init_version() == _read_pyproject_version()


def test_version_info_tuple_matches_package_version():
    core = _numeric_core(_read_init_version())
    filevers = _read_version_info_tuple()
    # Fourth component is the build number; we don't require it to be
    # zero but the first three must track the package.
    assert filevers[:3] == core, (
        f"build/version_info.txt filevers {filevers} does not match __version__ core {core}"
    )
