# Known Issues

Issues tracked for future releases. Each entry has a severity (none
of these are release blockers or they would have been fixed), a
concrete reproduction, and a sketch of the intended fix.

File nothing here without a repro. If you hit one of these in the
wild, please attach your `%APPDATA%\ClipWarden\diagnostic.log`
(enable with `CLIPWARDEN_DIAGNOSTIC=1`) and a description of the
sequence of actions to the issue tracker.

## 1. Tray thread ownership

**Severity**: low (architectural clarity, not a user-visible bug).

`pystray.Icon.run()` blocks the main thread for the lifetime of the
app. Menu callbacks therefore execute on the main thread, but the
tray-flash `threading.Timer` callback that reverts the icon runs on
a short-lived timer thread, and the About dialog runs on its own
daemon thread. Three different threads can touch `TrayApp` state,
and the locking strategy is "each operation is short and pystray
is mostly thread-safe for icon / title swaps." That is empirically
true on Windows 10 / 11 but it is not documented, and it is not
enforced.

**Repro**: hard to trigger in practice; stress-testing with rapid
pause-toggle + detection-fire cycles occasionally shows a redundant
icon swap that settles within the next event.

**Fix sketch**: funnel every `TrayApp` state change through a single
`queue.Queue` drained by the tray thread. The flash timer and the
About dialog thread enqueue requests instead of calling mutating
methods directly. Preserves the "pystray owns the main thread"
contract and makes the threading invariants checkable in tests.

## 2. `_stop_handle` leak on stranded pump thread

**Severity**: low; one-time leak per hung shutdown, not per
operation.

`Watcher.stop()` posts `WM_QUIT`, signals `_stop_handle`, and joins
the pump + worker threads with a timeout. A clean stop releases the
event handle. If the pump thread is stranded (external message pump
wedge, runaway Win32 callback) the instance is marked `_stopping`
and refuses subsequent `start()` calls, but `_stop_handle` and the
message-only window's HWND stay alive: the pump thread still owns
them and we have no safe way to reclaim them without entering the
thread's message pump. The memory is reclaimed when the process
exits.

**Repro**: subclass the test `_FakeWin32` from `test_watcher.py` to
never PumpMessages after `PostThreadMessage`, call `start()` then
`stop()`. Inspect `watcher._stop_handle` post-stop; it is still
set while the pump thread is alive.

**Fix sketch**: give the pump thread a
`threading.Event` it checks inside its message loop every 500 ms.
On `stop()`, set the event as well as posting WM_QUIT; on event
receipt the pump thread tears down its own window and exits. A
non-responsive pump thread still cannot be force-collected, but
any hang that resolves within a handful of seconds then returns
the resources instead of leaking them until process exit.

## 3. Tray assets not packaged in the wheel

**Severity**: low; affects a specific dev layout, not shipped
installs.

`tray._resolve_asset` probes `sys._MEIPASS`, then the repo-adjacent
`assets/` folder (via `Path(__file__)`). In a non-editable
`pip install .` checkout with the sdist layout (no `assets/`
alongside `src/clipwarden/`), the fallback path resolves to a
directory that does not exist. The tray now substitutes a 16x16
neutral-grey placeholder icon and logs a warning instead of
crashing, so the app still launches, but the icon is a visual
breadcrumb rather than a polished mark. The frozen exe is
unaffected because `_MEIPASS` is always set; only the
"pip-install, run from the installed wheel" path hits this.

**Repro**: `pip install .` into a fresh venv (not editable),
`python -m clipwarden`. The tray appears with the grey placeholder.

**Fix sketch**: package the icons under
`src/clipwarden/assets/` so `Path(__file__).parent / "assets"`
resolves inside the installed wheel, and update `pyproject.toml`
package-data to include `*.ico`. Keep the top-level `assets/`
folder for the PyInstaller spec and the icon generator; `tray.py`
would pick whichever is present first.

## 4. Self-demo mode for installed builds

**Severity**: enhancement (v1.1 roadmap), not a bug.

Reviewer demos currently require running the repository-local CLI
simulator:
`python tools/attacker_sim.py --i-know-this-is-adversarial`.
That is fine for source users, but installed users cannot run a
guided substitution demo without cloning the repo and setting up
Python.

**Repro**: install ClipWarden from release binaries only; there is no
`ClipWarden.exe` flag to trigger an in-process demo substitution flow.

**Fix sketch**: add a `--demo` flag to `ClipWarden.exe` that performs
an explicit, gated, in-process substitution simulation against itself.
Keep this inside the main binary (no separate packaged attacker
artifact) so release distribution remains a clean single-artifact
defensive story.

## Reporting a new issue

If you hit something that is not on this list:

1. Enable diagnostic logging: set `CLIPWARDEN_DIAGNOSTIC=1` and
   re-run the scenario.
2. Open [`%APPDATA%\ClipWarden\`](./) and grab
   `diagnostic.log`, `log.jsonl` (scrubbed of addresses you care
   about), and `crash.log` if present.
3. File an issue with the three files attached, the exact
   sequence of actions, your Windows build (`winver`), and the
   SHA-256 of the `ClipWarden.exe` you are running (should match
   the value from the GitHub Releases page).
