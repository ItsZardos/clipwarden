# PyInstaller spec for the attacker-sim GUI.
#
# Invocation (from the repo root):
#
#     pyinstaller build/attacker_sim.spec --clean --noconfirm
#
# Output: dist/ClipWarden-AttackerSim-<version>.exe. This is an
# optional release artifact for reviewer demos; it is not shipped
# by the main installer and has no persistent state.
#
# The fixture JSON is bundled under ``attacker_sim_fixtures/`` so
# ``tools/attacker_sim._fixtures_path()`` finds it inside
# ``sys._MEIPASS`` at runtime. UPX is off for the same
# SmartScreen/AV reasons as ``ClipWarden.spec``.

# ruff: noqa

import ast
import re
from pathlib import Path

SPEC_DIR = Path(SPECPATH)
REPO_ROOT = SPEC_DIR.parent
ASSETS = REPO_ROOT / "assets"
TOOLS = REPO_ROOT / "tools"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "real_addresses.json"


def _read_version() -> str:
    """Parse __version__ from clipwarden/__init__.py without importing."""
    init = REPO_ROOT / "src" / "clipwarden" / "__init__.py"
    tree = ast.parse(init.read_text(encoding="utf-8"), filename=str(init))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = [t for t in node.targets if isinstance(t, ast.Name)]
            if any(t.id == "__version__" for t in targets):
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    value = node.value.value.strip()
                    if re.match(r"^\d+\.\d+\.\d+", value):
                        return value
    raise SystemExit("could not parse __version__ for attacker-sim spec")


VERSION = _read_version()

block_cipher = None

a = Analysis(
    [str(TOOLS / "attacker_sim_gui.py")],
    pathex=[str(TOOLS)],
    binaries=[],
    datas=[
        (str(FIXTURES), "attacker_sim_fixtures"),
        (str(ASSETS / "icon-alert.ico"), "assets"),
    ],
    hiddenimports=[
        "tkinter",
        "tkinter.ttk",
        # win32clipboard/pywintypes/win32con are imported by tools/
        # attacker_sim.py at module load time, but the GUI does a
        # runtime file-based import of that module so PyInstaller's
        # static analysis does not see the chain. Declare them
        # explicitly so the frozen exe has the native pieces.
        "win32clipboard",
        "win32con",
        "pywintypes",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name=f"ClipWarden-AttackerSim-{VERSION}",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ASSETS / "icon-alert.ico"),
)
