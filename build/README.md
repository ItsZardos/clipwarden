# Build / Packaging

This folder holds everything needed to turn the source tree into the
shippable release artifacts:

- `dist\ClipWarden-<version>.exe` - per-user Inno Setup installer.
- `dist\ClipWarden-Portable.exe` - standalone portable exe.
- `dist\ClipWarden-AttackerSim-<version>.exe` - optional GUI clipper
  simulator used for demos and reviewer smoke tests.

Files:

- `ClipWarden.spec` - PyInstaller spec for the portable exe.
  Defines entry point, bundled assets (icons), hidden imports, and
  the PE metadata embed. Produces `dist/ClipWarden-Portable.exe`.
- `attacker_sim.spec` - PyInstaller spec for the attacker-sim GUI.
  Produces `dist/ClipWarden-AttackerSim-<version>.exe`.
- `launcher.py` - PyInstaller entry-point shim. Handles import-time
  crash logging and absolute-import resolution inside the frozen exe.
- `version_info.txt` - PE version resource consumed by PyInstaller
  (ProductName/ProductVersion/FileVersion/LegalCopyright).
- `installer.iss` - Inno Setup 6 script. Consumes
  `dist/ClipWarden-Portable.exe` and produces the per-user
  `dist/ClipWarden-<version>.exe` installer, which copies the
  portable into `{app}\ClipWarden.exe` (canonical installed name).

## Prerequisites

- Windows 10+ with PowerShell 5.1 or 7.x.
- Python matching `pyproject.toml` (3.11+) with a venv under `.venv`
  at the repo root. Install runtime and dev deps via
  `pip install -r requirements.txt -r requirements-dev.txt`
  (ClipWarden ships pinned requirement files, not a `[dev]` extra).
- Inno Setup 6, available on `PATH` as `iscc` or installed per-user
  at `%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe`:
  ```powershell
  winget install --id JRSoftware.InnoSetup --exact --silent --scope user
  ```

## Produce the portable exe

```powershell
# From the repo root with the venv active.
pyinstaller build\ClipWarden.spec --clean
```

Output: `dist\ClipWarden-Portable.exe` (one-file, `--noconsole`, UPX
disabled). Task Manager will show two `ClipWarden-Portable.exe`
entries per launch - that is the PyInstaller onefile bootloader plus
its Python child, not a singleton bug. See the module docstring in
`src/clipwarden/__main__.py` for detail.

## Build the installer

Build the portable exe first (above); the installer consumes it.

```powershell
$iscc = "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
& $iscc build\installer.iss
```

Output: `dist\ClipWarden-<version>.exe`.

The installer:

- Installs per-user (no admin) to
  `%LOCALAPPDATA%\Programs\ClipWarden\`.
- Copies `dist\ClipWarden-Portable.exe` to
  `{app}\ClipWarden.exe` so the installed binary keeps the canonical
  process name used by autostart and the PE metadata.
- Adds a Start Menu group with `ClipWarden` and
  `Uninstall ClipWarden` shortcuts.
- Offers an *optional* autostart checkbox (unchecked by default).
  Selecting it runs `ClipWarden.exe --install-autostart` so the Run
  key is written through the same codepath
  `src/clipwarden/autostart.py` uses at runtime - one source of
  truth to change in `v1.1+`.
- Preserves user data under `%APPDATA%\ClipWarden\` on uninstall.
  The installer touches nothing in that folder; `config.json`,
  `whitelist.json`, and `log.jsonl` survive reinstalls.

## Build the attacker-sim GUI (optional)

```powershell
pyinstaller build\attacker_sim.spec --clean
```

Output: `dist\ClipWarden-AttackerSim-<version>.exe`. This is a
self-contained GUI that bundles `tests/fixtures/real_addresses.json`
and exposes the clipboard-hijack primitives behind an explicit
"I understand" gate. Useful for demos and reviewer smoke tests on a
machine without Python. It is never auto-started and is not
referenced by the main installer.

## Emit release checksums

```powershell
.\.venv\Scripts\python tools\gen_checksums.py
```

Prints SHA-256 lines for the installer and portable (required) and,
if present, the attacker-sim GUI (optional). Exits non-zero if
either required artifact is missing from `dist\`.

## Clean-install smoke test

Baseline expectations; run against a freshly wiped
`%LOCALAPPDATA%\Programs\ClipWarden\` and `%APPDATA%\ClipWarden\`.

1. `dist\ClipWarden-<version>.exe /VERYSILENT /CURRENTUSER`.
2. Confirm `%LOCALAPPDATA%\Programs\ClipWarden\ClipWarden.exe` exists,
   `Start Menu\Programs\ClipWarden\ClipWarden.lnk` exists.
3. Launch via the Start Menu shortcut; confirm tray icon appears.
4. `.\.venv\Scripts\python tools\attacker_sim.py --i-know-this-is-adversarial`
   (or double-click `dist\ClipWarden-AttackerSim-<version>.exe` and
   fire a substitution from the GUI) and confirm the popup, sound,
   tray flash, and toast all fire, and that
   `%APPDATA%\ClipWarden\log.jsonl` gets a new detection line.
5. Right-click tray -> `Quit`.
6. `%LOCALAPPDATA%\Programs\ClipWarden\unins000.exe /VERYSILENT`.
7. Confirm `%LOCALAPPDATA%\Programs\ClipWarden\` is gone and
   `%APPDATA%\ClipWarden\log.jsonl` is still present.

## Diagnostic logging

Off by default. Set `CLIPWARDEN_DIAGNOSTIC=1` (also accepts `true` /
`yes` / `on`, case-insensitive) before launching ClipWarden to get
a rotating INFO+ trace at `%APPDATA%\ClipWarden\diagnostic.log`.
Intended for reproducing a user-reported issue without asking the
user to install a debug build.
