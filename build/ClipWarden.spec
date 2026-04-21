# PyInstaller spec for the ClipWarden portable onefile exe.
#
# Invocation (from the repo root):
#
#     pyinstaller build/ClipWarden.spec --clean --noconfirm
#
# Output: dist/ClipWarden-Portable.exe. The installer (installer.iss)
# consumes this artifact and copies it to the install directory as
# ``ClipWarden.exe`` so the installed binary keeps the canonical
# process name used by autostart and the PE version resource.
# UPX is off on purpose: SmartScreen and several AV products flag
# UPX-packed binaries as suspicious and the size win is not worth the
# support burden.
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
    # Use the launcher shim as the frozen entry point. PyInstaller's
    # bootloader runs the entry script with ``__package__`` unset,
    # which breaks relative imports in ``clipwarden.__main__``. The
    # shim does ``from clipwarden.__main__ import main`` so the
    # frozen exe and ``python -m clipwarden`` share one code path.
    [str(SPEC_DIR / "launcher.py")],
    pathex=[str(REPO_ROOT / "src")],
    binaries=[],
    datas=[
        (str(ASSETS / "icon.ico"), "assets"),
        (str(ASSETS / "icon-disabled.ico"), "assets"),
        (str(ASSETS / "icon-alert.ico"), "assets"),
    ],
    hiddenimports=[
        # pystray's backend is picked at import time via a
        # dynamic import that PyInstaller's static analysis misses.
        "pystray._win32",
        # Pillow's tkinter finder is pulled in by the Image module
        # transitively through some image format plugins; safer to
        # declare than to chase a production-only ImportError.
        "PIL._tkinter_finder",
        # PyNaCl (via clipwarden.validators.solana) imports cffi
        # through C-ext indirection that PyInstaller's static
        # analysis misses on this platform; without these the frozen
        # exe raises ModuleNotFoundError: _cffi_backend at import
        # time before main() can run.
        "_cffi_backend",
        "cffi",
        # The alert popup uses Tkinter. PyInstaller usually discovers
        # tkinter automatically, but being explicit guards against a
        # transitive import path that a future refactor might break
        # (lazy import inside alert.PopupChannel._show). Including
        # ``winsound`` is cheap and matches the "one channel, one
        # declared dep" convention.
        "tkinter",
        "tkinter.ttk",
        "winsound",
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
    name="ClipWarden-Portable",
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
