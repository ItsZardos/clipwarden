# Build / Packaging

This folder holds everything needed to turn the source tree into a
shippable `ClipWarden.exe` and `ClipWarden-Setup-1.0.0.exe`.

Files:

- `ClipWarden.spec` - PyInstaller spec. Defines entry point, bundled
  assets (icons), hidden imports, and the PE metadata embed.
- `launcher.py` - PyInstaller entry-point shim. Handles import-time
  crash logging and absolute-import resolution inside the frozen exe.
- `version_info.txt` - PE version resource consumed by PyInstaller
  (ProductName/ProductVersion/FileVersion/LegalCopyright).
- `installer.iss` - Inno Setup 6 script. Produces the per-user
  `dist/ClipWarden-Setup-<version>.exe`.

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

Output: `dist\ClipWarden.exe` (one-file, `--noconsole`, UPX disabled).
Task Manager will show two `ClipWarden.exe` entries per launch - that
is the PyInstaller onefile bootloader plus its Python child, not a
singleton bug. See the module docstring in
`src/clipwarden/__main__.py` for detail.

## Build the installer

```powershell
$iscc = "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
& $iscc build\installer.iss
```

Output: `dist\ClipWarden-Setup-<version>.exe`.

The installer:

- Installs per-user (no admin) to
  `%LOCALAPPDATA%\Programs\ClipWarden\`.
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

## Emit release checksums

```powershell
.\.venv\Scripts\python tools\gen_checksums.py
```

Prints `ClipWarden.exe` and the matching `ClipWarden-Setup-<version>.exe`
SHA-256 lines. Exits non-zero if either artifact is missing from `dist\`.

## Clean-install smoke test

Baseline expectations; run against a freshly wiped
`%LOCALAPPDATA%\Programs\ClipWarden\` and `%APPDATA%\ClipWarden\`.

1. `dist\ClipWarden-Setup-<version>.exe /VERYSILENT /CURRENTUSER`.
2. Confirm `%LOCALAPPDATA%\Programs\ClipWarden\ClipWarden.exe` exists,
   `Start Menu\Programs\ClipWarden\ClipWarden.lnk` exists.
3. Launch via the Start Menu shortcut; confirm tray icon appears.
4. `.\.venv\Scripts\python tools\attacker_sim.py --i-know-this-is-adversarial`
   and confirm the popup, sound, tray flash, and toast all fire, and
   that `%APPDATA%\ClipWarden\log.jsonl` gets a new detection line.
5. Right-click tray → `Quit`.
6. `%LOCALAPPDATA%\Programs\ClipWarden\unins000.exe /VERYSILENT`.
7. Confirm `%LOCALAPPDATA%\Programs\ClipWarden\` is gone and
   `%APPDATA%\ClipWarden\log.jsonl` is still present.

## Diagnostic logging

Off by default. Set `CLIPWARDEN_DIAGNOSTIC=1` (also accepts `true` /
`yes` / `on`, case-insensitive) before launching ClipWarden to get
a rotating INFO+ trace at `%APPDATA%\ClipWarden\diagnostic.log`.
Intended for reproducing a user-reported issue without asking the
user to install a debug build.
