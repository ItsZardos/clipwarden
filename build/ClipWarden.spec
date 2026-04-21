# PyInstaller spec for the ClipWarden onefile exe.
#
# Invocation (from the repo root):
#
#     pyinstaller build/ClipWarden.spec --clean --noconfirm
#
# Output: dist/ClipWarden.exe. UPX is off on purpose: SmartScreen and
# several AV products flag UPX-packed binaries as suspicious and the
# size win is not worth the support burden.
#
# Icons are bundled under an ``assets/`` folder inside the onefile
# payload; :func:`clipwarden.tray._resolve_asset` probes ``_MEIPASS``
# first, so the tray picks them up automatically.

# ruff: noqa

from pathlib import Path

# PyInstaller executes this file with ``SPECPATH`` set to the spec's
# directory; build/ClipWarden.spec lives in <repo>/build/, so the
# repo root sits one directory up.
SPEC_DIR = Path(SPECPATH)
REPO_ROOT = SPEC_DIR.parent
ASSETS = REPO_ROOT / "assets"

block_cipher = None

a = Analysis(
    [str(REPO_ROOT / "src" / "clipwarden" / "__main__.py")],
    pathex=[str(REPO_ROOT / "src")],
    binaries=[],
    datas=[
        (str(ASSETS / "icon.ico"), "assets"),
        (str(ASSETS / "icon-disabled.ico"), "assets"),
    ],
    hiddenimports=[
        # pystray's backend is picked at import time via a
        # dynamic import that PyInstaller's static analysis misses.
        "pystray._win32",
        # Pillow's tkinter finder is pulled in by the Image module
        # transitively through some image format plugins; safer to
        # declare than to chase a production-only ImportError.
        "PIL._tkinter_finder",
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
    name="ClipWarden",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # SmartScreen / AV heuristics penalise UPX-packed binaries;
    # the unpacked exe is ~25 MB which is fine for a desktop tool.
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ASSETS / "icon.ico"),
    version=str(SPEC_DIR / "version_info.txt"),
)
