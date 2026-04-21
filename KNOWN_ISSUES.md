# Known Issues

Tracked issues carried forward to v1.0.1. Each entry has a severity
(none of these are blockers for v1.0.0 or they would have been
fixed), a concrete reproduction, and a sketch of the intended fix.
Everything here was surfaced by an external review during the v1.0.0
hardening pass and deliberately deferred because it either a) is a
papercut rather than a defect, or b) needs a larger refactor than
v1.0.0 can absorb without re-opening the test matrix.

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

**Fix sketch for v1.0.1**: funnel every `TrayApp` state change
through a single `queue.Queue` drained by the tray thread. The
flash timer and the About dialog thread enqueue requests instead
of calling mutating methods directly. Preserves the "pystray owns
the main thread" contract and makes the threading invariants
checkable in tests.

## 2. Toast delivery is synchronous

**Severity**: low; affects perceived alert latency on flaky shells.

`ToastChannel.fire` calls `winotify.Notification(...).show()` on
the dispatcher's calling thread. When the Windows notification
subsystem is slow (Explorer restart pending, corrupt action-centre
cache) the call can block for hundreds of milliseconds. The
dispatcher dispatches to channels sequentially by design so a slow
toast pushes back on the popup / sound / tray-flash channels.

**Repro**: restart Explorer, copy two valid same-chain addresses
within 1 s. Observe that the popup, sound, and tray flash can
appear up to ~500 ms after the log line is written.

**Fix sketch for v1.0.1**: run `ToastChannel` on a single-slot
`ThreadPoolExecutor(max_workers=1)` so the dispatcher never blocks
on it. Drop toast requests if the executor is saturated rather
than queueing; toast is the passive channel, so dropping is the
correct failure mode.

## 3. Whitelist chain validation

**Severity**: low; user can corrupt their own whitelist but the
corruption does not silently disable detection.

`Whitelist.load()` validates that `whitelist.json` has the right
JSON shape (object root, `entries` is a list of objects with
`chain`/`address` keys) but it does not verify that each `address`
is actually a valid address on the claimed `chain`. A hand-edited
whitelist entry with a mismatched pair (ETH address labelled
as BTC, for example) will be stored and looked up literally, and
will not match any real detection event.

**Repro**: edit `whitelist.json` to add `{"chain": "BTC",
"address": "0x0000000000000000000000000000000000000000"}`. Save.
The tool accepts the entry. A subsequent ETH detection against
that address is not whitelisted, because the pair is keyed by
chain.

**Fix sketch for v1.0.1**: run each entry through
`classifier.classify(address)` at load time. Mismatches fall
through to the existing `_backup_corrupt` path (rename to
`whitelist.json.bak-<ts>`, reinstate empty). That re-uses the
Commit 13 backup pattern and surfaces the problem loudly instead
of silently storing a useless row.

## 4. `_stop_handle` leak on stop-timeout

**Severity**: low; one-time leak per hung shutdown, not per
operation.

`Watcher.stop()` posts `WM_QUIT` via `PostMessage` and joins the
pump + worker threads with a timeout. Commit 11 added the right
behaviour for stranded threads (mark `_stopping` and refuse
subsequent `start()` calls on the same instance). But the
`_stop_handle` object and the underlying message-only window's
HWND are not freed in the stranded case: the pump thread still
owns them and we have no safe way to reclaim them without
entering the thread's message pump. The memory is reclaimed when
the process exits.

**Repro**: subclass the test `_FakeWin32` from `test_watcher.py`
to never PumpMessages after `PostMessage`, call `start()` then
`stop()`. Inspect `watcher._stop_handle` post-stop; it is still
set.

**Fix sketch for v1.0.1**: give the pump thread a
`threading.Event` it checks inside its message loop every 500 ms.
On `stop()`, set the event as well as posting WM_QUIT; on event
receipt the pump thread tears down its own window and exits. A
non-responsive pump thread (the whole reason we have the
timeout) still cannot be force-collected, but any hang that
resolves within a handful of seconds now returns the resources.

## 5. Tray asset resolution fragility

**Severity**: low; affects a specific dev layout, not shipped
installs.

`tray._resolve_asset` probes `sys._MEIPASS`, then the
repo-adjacent `assets/` folder (via `Path(__file__)`). In a fresh
`pip install -e .` checkout with the sdist layout (no `assets/`
alongside `src/clipwarden/`), the fallback path resolves to a
directory that does not exist and the tray falls back to a
1x1 placeholder icon. The frozen exe is unaffected because
`_MEIPASS` is always set; only the "run from source" path hits
this.

**Repro**: `pip install .` into a fresh venv (not editable),
`python -m clipwarden`. The tray appears with the placeholder.

**Fix sketch for v1.0.1**: package the icons under
`src/clipwarden/assets/` so `Path(__file__).parent / "assets"`
resolves inside the installed wheel, and update `pyproject.toml`
package-data to include `*.ico`. Keep the top-level `assets/`
folder for the PyInstaller spec and the icon generator; `tray.py`
would pick whichever is present first.

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
