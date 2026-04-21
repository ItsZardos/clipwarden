"""Version-parsing correctness for ``tools/gen_checksums.py``.

The releaser runs this script on a clean checkout, so its version
parser cannot quietly fall back to "no version" if someone adds a
comment or swaps quote styles in ``__init__.py``. These tests pin
the ``ast``-based parser against a few representative module texts.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_TOOLS = Path(__file__).resolve().parent.parent / "tools"
_SPEC = importlib.util.spec_from_file_location("gen_checksums", _TOOLS / "gen_checksums.py")
assert _SPEC is not None
assert _SPEC.loader is not None
gen_checksums = importlib.util.module_from_spec(_SPEC)
sys.modules["gen_checksums"] = gen_checksums
_SPEC.loader.exec_module(gen_checksums)


def _write_init(tmp_path: Path, text: str) -> Path:
    """Create a minimal clipwarden/__init__.py layout for the parser."""
    init = tmp_path / "src" / "clipwarden" / "__init__.py"
    init.parent.mkdir(parents=True, exist_ok=True)
    init.write_text(text, encoding="utf-8")
    # Fake ``here = tools/gen_checksums.py`` next to ``src/``.
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "gen_checksums.py").write_text("# placeholder", encoding="utf-8")
    return tools / "gen_checksums.py"


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ('__version__ = "1.0.0"\n', "1.0.0"),
        ("__version__ = '1.2.3'\n", "1.2.3"),
        ('__version__ = "1.0.0"  # trailing comment\n', "1.0.0"),
        ('"""docstring."""\n__version__ = "1.0.0-rc1"\n', "1.0.0-rc1"),
    ],
)
def test_read_version_accepts_valid_forms(tmp_path, monkeypatch, body, expected):
    fake_here = _write_init(tmp_path, body)
    monkeypatch.setattr(gen_checksums, "__file__", str(fake_here))
    assert gen_checksums._read_version() == expected


def test_read_version_rejects_non_string_literal(tmp_path, monkeypatch):
    """A dynamic __version__ value must fail loudly.

    Release CI runs this script as the authoritative version source;
    silently coercing ``__version__ = get_version()`` to an empty
    string would produce a ``ClipWarden-Setup-.exe`` artifact name.
    """
    fake_here = _write_init(
        tmp_path,
        "def get_version() -> str: return '1.0.0'\n__version__ = get_version()\n",
    )
    monkeypatch.setattr(gen_checksums, "__file__", str(fake_here))
    with pytest.raises(SystemExit, match="must be a string literal"):
        gen_checksums._read_version()


def test_read_version_rejects_malformed_version(tmp_path, monkeypatch):
    fake_here = _write_init(tmp_path, '__version__ = "not-a-version"\n')
    monkeypatch.setattr(gen_checksums, "__file__", str(fake_here))
    with pytest.raises(SystemExit, match="does not look like a release version"):
        gen_checksums._read_version()


def test_read_version_missing_assignment(tmp_path, monkeypatch):
    fake_here = _write_init(tmp_path, "# no version here\n")
    monkeypatch.setattr(gen_checksums, "__file__", str(fake_here))
    with pytest.raises(SystemExit, match="could not locate __version__"):
        gen_checksums._read_version()
