"""Tk GUI wrapper around ``attacker_sim.py`` for ClipWarden demos.

This is the same clipboard-hijack simulator as the CLI, wearing a
window. The GUI exists so reviewers and conference demos can
trigger substitutions without a Python install on the target
machine; the PyInstaller spec at ``build/attacker_sim.spec`` wraps
this module into ``ClipWarden-AttackerSim-<version>.exe``.

Safety
------
The same ``_ACKNOWLEDGED`` gate that guards
``attacker_sim._set_clipboard_text`` also guards the GUI: the fire
buttons stay disabled until the user explicitly checks the
"I understand this writes to my real clipboard" box, and
``_ACKNOWLEDGED`` is only flipped true for the duration of an
actual substitution run. Importing this module and calling
``AttackerSimApp.fire_once`` from code is rejected at the exact
line where a real clipboard write would occur, just like the CLI.
"""

from __future__ import annotations

import importlib.util
import queue
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING

# The GUI ships inside a PyInstaller onefile alongside ``attacker_sim``,
# but during development it is imported from ``tools/`` where Python's
# module resolver does not automatically put ``tools/`` on ``sys.path``.
# Load ``attacker_sim`` by file location so both layouts work without
# relying on the caller having configured ``PYTHONPATH`` first. If
# anything already imported the module under that name (tests,
# ``python tools/attacker_sim.py`` from the same session), reuse it
# so there is exactly one ``_ACKNOWLEDGED`` flag guarding the
# clipboard writes; a second module object would let the GUI bypass
# the CLI's gate and vice versa.
if "attacker_sim" in sys.modules:
    attacker_sim = sys.modules["attacker_sim"]
else:
    _HERE = Path(__file__).resolve().parent
    _CLI_PATH = _HERE / "attacker_sim.py"
    _spec = importlib.util.spec_from_file_location("attacker_sim", _CLI_PATH)
    if _spec is None or _spec.loader is None:
        raise ImportError(f"could not locate attacker_sim at {_CLI_PATH}")
    attacker_sim = importlib.util.module_from_spec(_spec)
    sys.modules["attacker_sim"] = attacker_sim
    _spec.loader.exec_module(attacker_sim)

if TYPE_CHECKING:
    from collections.abc import Callable

    from tools.attacker_sim import ChainPair

CHAINS = ("BTC", "ETH", "XMR", "SOL")

WARNING_BANNER = (
    "ADVERSARIAL TOOL: this window writes to your REAL clipboard.\n"
    "Do not run it while a crypto transaction is pending in another app."
)

DISCLAIMER_BODY = (
    "ClipWarden Attacker Simulator\n\n"
    "This program simulates a clipboard-hijack clipper. When you fire "
    "a substitution it will:\n\n"
    "  1. Write a legitimate address to your Windows clipboard.\n"
    "  2. Wait the configured delay.\n"
    "  3. Overwrite the clipboard with a different address of the "
    "same chain.\n\n"
    "If ClipWarden is running it will flag this as a substitution. "
    "If you paste the clipboard into any other app after the "
    "substitution fires, you will paste the replacement address.\n\n"
    "Only run this on a machine where you are prepared to have the "
    "clipboard rewritten. Continue?"
)


class _ThreadSafeLog:
    """Enqueue log lines from worker threads, drain them on the UI thread.

    The substitution worker sleeps and prints progress; Tk widgets
    are single-threaded so we cannot call ``insert`` from there
    directly. The worker pushes strings onto a ``queue.Queue`` and
    the UI drains it every 100 ms via ``after``.
    """

    def __init__(self, text_widget: tk.Text, root: tk.Tk) -> None:
        self._text = text_widget
        self._root = root
        self._q: queue.Queue[str] = queue.Queue()
        self._root.after(100, self._drain)

    def write(self, line: str) -> None:
        self._q.put(line)

    def _drain(self) -> None:
        try:
            while True:
                line = self._q.get_nowait()
                self._text.configure(state="normal")
                self._text.insert("end", line + "\n")
                self._text.see("end")
                self._text.configure(state="disabled")
        except queue.Empty:
            pass
        self._root.after(100, self._drain)


def _shorten(address: str, keep: int = 10) -> str:
    """Middle-elide a long address for display."""
    if len(address) <= keep * 2 + 3:
        return address
    return f"{address[:keep]}...{address[-keep:]}"


class AttackerSimApp:
    """Tk window that drives ``attacker_sim.run_substitution``."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("ClipWarden Attacker Simulator")
        self.root.geometry("640x520")
        self.root.minsize(560, 420)

        self._pool: dict[str, list[str]] = {}
        self._pair: ChainPair | None = None
        self._worker: threading.Thread | None = None

        self.chain_var = tk.StringVar(value="BTC")
        self.delay_var = tk.IntVar(value=500)
        self.acknowledged_var = tk.BooleanVar(value=False)
        self.before_var = tk.StringVar(value="(load a pair)")
        self.after_var = tk.StringVar(value="(load a pair)")
        self.status_var = tk.StringVar(value="Idle.")

        self._build_ui()
        self._pool = self._safe_load_pool()
        if self._pool:
            self._refresh_pair()

    def _build_ui(self) -> None:
        pad = {"padx": 10, "pady": 6}

        banner = tk.Label(
            self.root,
            text=WARNING_BANNER,
            fg="#7a0012",
            bg="#ffe3e6",
            font=("Segoe UI", 10, "bold"),
            justify="center",
            wraplength=600,
        )
        banner.pack(fill="x", padx=8, pady=(8, 4))

        pair_frame = ttk.LabelFrame(self.root, text="Substitution pair")
        pair_frame.pack(fill="x", **pad)

        row = ttk.Frame(pair_frame)
        row.pack(fill="x", padx=6, pady=4)
        ttk.Label(row, text="Chain:").pack(side="left")
        chain_combo = ttk.Combobox(
            row,
            textvariable=self.chain_var,
            values=CHAINS,
            state="readonly",
            width=6,
        )
        chain_combo.pack(side="left", padx=(6, 12))
        chain_combo.bind("<<ComboboxSelected>>", lambda _e: self._refresh_pair())

        ttk.Button(row, text="Reshuffle", command=self._refresh_pair).pack(side="left")

        before_row = ttk.Frame(pair_frame)
        before_row.pack(fill="x", padx=6, pady=2)
        ttk.Label(before_row, text="BEFORE:", width=8).pack(side="left")
        ttk.Label(before_row, textvariable=self.before_var, font=("Consolas", 10)).pack(
            side="left", fill="x", expand=True
        )

        after_row = ttk.Frame(pair_frame)
        after_row.pack(fill="x", padx=6, pady=(2, 6))
        ttk.Label(after_row, text="AFTER:", width=8).pack(side="left")
        ttk.Label(after_row, textvariable=self.after_var, font=("Consolas", 10)).pack(
            side="left", fill="x", expand=True
        )

        timing_frame = ttk.LabelFrame(self.root, text="Timing")
        timing_frame.pack(fill="x", **pad)
        delay_row = ttk.Frame(timing_frame)
        delay_row.pack(fill="x", padx=6, pady=6)
        ttk.Label(delay_row, text="Delay between writes (ms):").pack(side="left")
        delay_value = ttk.Label(delay_row, textvariable=self.delay_var, width=5)
        delay_value.pack(side="right")
        ttk.Scale(
            timing_frame,
            from_=100,
            to=5000,
            orient="horizontal",
            variable=self.delay_var,
            command=lambda v: self.delay_var.set(int(float(v))),
        ).pack(fill="x", padx=6, pady=(0, 6))

        safety_frame = ttk.LabelFrame(self.root, text="Safety")
        safety_frame.pack(fill="x", **pad)
        self.safety_check = ttk.Checkbutton(
            safety_frame,
            text="I understand this writes to my real clipboard.",
            variable=self.acknowledged_var,
            command=self._update_fire_state,
        )
        self.safety_check.pack(anchor="w", padx=6, pady=4)

        buttons = ttk.Frame(self.root)
        buttons.pack(fill="x", **pad)
        self.fire_btn = ttk.Button(
            buttons, text="Fire substitution", command=self._on_fire_once, state="disabled"
        )
        self.fire_btn.pack(side="left")
        self.fire_all_btn = ttk.Button(
            buttons,
            text="Fire all chains",
            command=self._on_fire_all,
            state="disabled",
        )
        self.fire_all_btn.pack(side="left", padx=6)
        ttk.Button(buttons, text="Quit", command=self.root.destroy).pack(side="right")

        log_frame = ttk.LabelFrame(self.root, text="Activity")
        log_frame.pack(fill="both", expand=True, **pad)
        self.log_text = tk.Text(
            log_frame,
            height=10,
            wrap="word",
            state="disabled",
            font=("Consolas", 9),
        )
        self.log_text.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=6)
        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        scroll.pack(side="right", fill="y", pady=6)
        self.log_text.configure(yscrollcommand=scroll.set)
        self._log = _ThreadSafeLog(self.log_text, self.root)

        status = ttk.Label(self.root, textvariable=self.status_var, anchor="w")
        status.pack(fill="x", padx=10, pady=(0, 8))

    def _safe_load_pool(self) -> dict[str, list[str]]:
        try:
            return attacker_sim._load_addresses_by_chain()
        except (OSError, ValueError) as exc:
            messagebox.showerror(
                "ClipWarden Attacker Simulator",
                f"Could not load fixture addresses:\n\n{exc}",
                parent=self.root,
            )
            self._log.write(f"[error] fixture load failed: {exc}")
            self.status_var.set("Fixture load failed.")
            return {}

    def _refresh_pair(self) -> None:
        chain = self.chain_var.get()
        if not self._pool or chain not in self._pool or len(self._pool[chain]) < 2:
            self._pair = None
            self.before_var.set("(no addresses for chain)")
            self.after_var.set("(no addresses for chain)")
            self.status_var.set(f"No fixture pair available for {chain}.")
            self._update_fire_state()
            return

        try:
            pair = attacker_sim._pick_pair(chain, self._pool)
        except SystemExit as exc:
            self._pair = None
            self._log.write(f"[error] could not pick pair for {chain}: {exc}")
            self.status_var.set(f"No fixture pair available for {chain}.")
            self._update_fire_state()
            return

        self._pair = pair
        self.before_var.set(_shorten(pair.before))
        self.after_var.set(_shorten(pair.after))
        self.status_var.set(f"Pair loaded for {chain}.")
        self._update_fire_state()

    def _update_fire_state(self) -> None:
        ok_once = bool(self.acknowledged_var.get() and self._pair and self._worker is None)
        ok_all = bool(self.acknowledged_var.get() and self._pool and self._worker is None)
        self.fire_btn.configure(state="normal" if ok_once else "disabled")
        self.fire_all_btn.configure(state="normal" if ok_all else "disabled")

    def _run_worker(self, work: Callable[[], None]) -> None:
        def runner() -> None:
            attacker_sim._ACKNOWLEDGED = True
            try:
                work()
            except SystemExit as exc:
                self._log.write(f"[error] {exc}")
            except Exception as exc:  # noqa: BLE001 - surface everything to UI log
                self._log.write(f"[error] unexpected: {exc!r}")
            finally:
                attacker_sim._ACKNOWLEDGED = False
                self._worker = None
                self.root.after(0, self._on_worker_done)

        self._worker = threading.Thread(target=runner, daemon=True, name="attacker-sim-worker")
        self._update_fire_state()
        self.status_var.set("Running substitution...")
        self._worker.start()

    def _on_worker_done(self) -> None:
        self.status_var.set("Done. Check ClipWarden for the detection.")
        self._update_fire_state()

    def _on_fire_once(self) -> None:
        if not self._pair or self._worker is not None:
            return
        pair = self._pair
        delay = int(self.delay_var.get())
        self._log.write(
            f"[fire] {pair.chain}: BEFORE={_shorten(pair.before)} -> AFTER={_shorten(pair.after)} (delay {delay} ms)"
        )

        def do_run() -> None:
            self._log.write(f"[sim] writing BEFORE ({pair.chain})")
            attacker_sim._set_clipboard_text(pair.before)
            self._log.write(f"[sim] sleeping {delay} ms")
            time.sleep(delay / 1000.0)
            self._log.write(f"[sim] writing AFTER  ({pair.chain})")
            attacker_sim._set_clipboard_text(pair.after)
            self._log.write("[sim] expected: ClipWarden popup + log.jsonl entry")

        self._run_worker(do_run)

    def _on_fire_all(self) -> None:
        if self._worker is not None or not self._pool:
            return
        delay = int(self.delay_var.get())
        pool = self._pool

        def do_run() -> None:
            for chain in CHAINS:
                if chain not in pool or len(pool[chain]) < 2:
                    self._log.write(f"[skip] {chain}: not enough fixture addresses")
                    continue
                pair = attacker_sim._pick_pair(chain, pool)
                self._log.write(
                    f"[fire] {chain}: substituting {_shorten(pair.before)} -> {_shorten(pair.after)}"
                )
                attacker_sim._set_clipboard_text(pair.before)
                time.sleep(delay / 1000.0)
                attacker_sim._set_clipboard_text(pair.after)
                # Brief pause between chains so ClipWarden's dispatcher
                # has time to surface one popup before the next AFTER
                # write lands; otherwise the PopupChannel cap would
                # drop later events.
                time.sleep(0.25)
            self._log.write("[sim] all chains fired")

        self._run_worker(do_run)


def _show_startup_disclaimer(root: tk.Tk) -> bool:
    """Modal OK/Cancel before the main window becomes usable.

    Returning ``False`` means the user declined; the caller should
    destroy the root and exit. The safety checkbox inside the main
    window is a second gate; this dialog exists so a user who
    double-clicks the exe by accident cannot fire anything without
    first reading the disclaimer.
    """
    return bool(
        messagebox.askokcancel(
            "ClipWarden Attacker Simulator",
            DISCLAIMER_BODY,
            icon="warning",
            default="cancel",
            parent=root,
        )
    )


def main(argv: list[str] | None = None) -> int:
    del argv  # GUI takes no arguments
    root = tk.Tk()
    root.withdraw()
    try:
        accepted = _show_startup_disclaimer(root)
    except tk.TclError:
        return 1
    if not accepted:
        root.destroy()
        return 0

    root.deiconify()
    AttackerSimApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
