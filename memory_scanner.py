"""
memory_scanner.py — Find the fishing-state variable in Crimson Desert's memory.

Run once before using the bot:
    python memory_scanner.py [ProcessName.exe]

Stay in-game.  Press F6 at each prompted stage.  The scanner takes a snapshot
of all readable/writable game memory at each fishing phase, then finds addresses
whose value changes at every transition and whose value is a small integer
(consistent with a state-machine enum).

Results (address + value→state map) are written to config.json.
The bot then reads memory directly instead of using CV.

How it works (same algorithm as Cheat Engine):
  1. Snapshot all R/W memory (~hundreds of MB of heap/data pages).
  2. At each state transition, re-read every region and keep only
     addresses whose 4-byte int value changed.
  3. After all stages, the surviving candidates changed at EVERY transition.
  4. Filter to small non-negative integers (0–64) — the likely enum range.
  5. Rank by number of distinct values across stages; save the best hit.
"""

import ctypes
import ctypes.wintypes
import json
import os
import struct
import sys
import threading
import time
from typing import Optional

import numpy as np
from pynput import keyboard as kb

import config

# ── Windows API constants ──────────────────────────────────────────────────
PAGE_READWRITE  = 0x04
PAGE_WRITECOPY  = 0x08
PAGE_GUARD      = 0x100
MEM_COMMIT      = 0x1000

# Skip regions larger than this — they are almost certainly texture/audio buffers.
MAX_REGION_BYTES = 64 * 1024 * 1024   # 64 MB

CAPTURE_KEY     = "f6"
CAPTURE_TIMEOUT = 180   # seconds per stage

STAGES = [
    ("idle",    "IDLE — Rod out, line NOT in water.  Capture the resting baseline."),
    ("waiting", "WAITING FOR BITE — Line in water, float sitting still."),
    ("bite",    "BITE — Camera is shifting down RIGHT NOW.  Press F6 immediately."),
    ("fight",   "FIGHTING — Camera actively panning while fish pulls."),
    ("caught",  "CAUGHT — Golden fish-info panel is visible."),
]

DEFAULT_PROCESS = "CrimsonDesert.exe"


# ── Windows struct ─────────────────────────────────────────────────────────

class _MBI(ctypes.Structure):
    """MEMORY_BASIC_INFORMATION (64-bit layout)."""
    _fields_ = [
        ("BaseAddress",       ctypes.c_ulonglong),
        ("AllocationBase",    ctypes.c_ulonglong),
        ("AllocationProtect", ctypes.wintypes.DWORD),
        ("_pad1",             ctypes.wintypes.DWORD),
        ("RegionSize",        ctypes.c_ulonglong),
        ("State",             ctypes.wintypes.DWORD),
        ("Protect",           ctypes.wintypes.DWORD),
        ("Type",              ctypes.wintypes.DWORD),
        ("_pad2",             ctypes.wintypes.DWORD),
    ]


_VQEx = ctypes.windll.kernel32.VirtualQueryEx
_RPM  = ctypes.windll.kernel32.ReadProcessMemory


# ── Low-level helpers ──────────────────────────────────────────────────────

def _enum_rw_regions(handle: int):
    """Yield (base, size) for all committed, read-write, reasonably-sized regions."""
    mbi  = _MBI()
    addr = 0
    while _VQEx(handle, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
        rw = PAGE_READWRITE | PAGE_WRITECOPY
        if (mbi.State  == MEM_COMMIT and
                (mbi.Protect & rw) and
                not (mbi.Protect & PAGE_GUARD) and
                0 < mbi.RegionSize <= MAX_REGION_BYTES):
            yield int(mbi.BaseAddress), int(mbi.RegionSize)
        nxt = mbi.BaseAddress + mbi.RegionSize
        if nxt <= addr:
            break
        addr = nxt


def _read_region(handle: int, base: int, size: int) -> Optional[np.ndarray]:
    """Read a memory region into a numpy int32 array (4-byte aligned)."""
    aligned = (size // 4) * 4
    if aligned == 0:
        return None
    buf = ctypes.create_string_buffer(aligned)
    n   = ctypes.c_size_t(0)
    if _RPM(handle, ctypes.c_void_p(base), buf, aligned, ctypes.byref(n)) and n.value >= 4:
        count = n.value // 4
        return np.frombuffer(buf.raw[:count * 4], dtype=np.int32).copy()
    return None


# ── Snapshot-based scanner ─────────────────────────────────────────────────

class MemScanner:
    """
    Snapshot-based memory scanner operating on 4-byte aligned int32 values.

    workflow:
        scanner.snapshot()           # baseline
        # ... state changes in game ...
        scanner.diff_changed()       # keep only addresses whose value changed
        scanner.filter_range(0, 64)  # keep only small ints (state enums)
    """

    def __init__(self, handle: int):
        self._handle = handle
        # {base_addr: np.ndarray[int32]}
        self._snap: dict[int, np.ndarray] = {}
        # {addr: current_int32_value} — None means "all addresses are candidates"
        self._cands: Optional[dict[int, int]] = None

    # ── Snapshot ──────────────────────────────────────────────────────────

    def snapshot(self) -> int:
        """Take a full snapshot of all R/W regions.  Returns total MB captured."""
        self._snap.clear()
        total_ints = 0
        for base, size in _enum_rw_regions(self._handle):
            data = _read_region(self._handle, base, size)
            if data is not None:
                self._snap[base] = data
                total_ints += len(data)
        mb = total_ints * 4 / 1e6
        print(f"    Snapshot: {len(self._snap)} regions  ({mb:.0f} MB  "
              f"{total_ints:,} int32 values)")
        return mb

    # ── Diff ──────────────────────────────────────────────────────────────

    def diff_changed(self) -> int:
        """
        Re-read all regions.  Keep candidates whose int32 value has changed
        since the last snapshot.  Update internal snapshot.
        Returns number of surviving candidates.
        """
        new_cands: dict[int, int] = {}
        new_snap:  dict[int, np.ndarray] = {}

        for base, old in self._snap.items():
            new = _read_region(self._handle, base, len(old) * 4)
            if new is None or len(new) < len(old):
                continue
            new = new[:len(old)]
            new_snap[base] = new

            changed_idx = np.where(old != new)[0]
            for idx in changed_idx:
                addr = base + int(idx) * 4
                val  = int(new[idx])
                if self._cands is None or addr in self._cands:
                    new_cands[addr] = val

        self._snap   = new_snap
        self._cands  = new_cands
        print(f"    Candidates after diff: {len(new_cands):,}")
        return len(new_cands)

    # ── Filters ───────────────────────────────────────────────────────────

    def filter_range(self, lo: int, hi: int) -> int:
        """Discard candidates whose current value is outside [lo, hi]."""
        if self._cands is None:
            return 0
        self._cands = {a: v for a, v in self._cands.items() if lo <= v <= hi}
        print(f"    After range filter [{lo}–{hi}]: {len(self._cands):,}")
        return len(self._cands)

    # ── Accessors ─────────────────────────────────────────────────────────

    def current_values(self) -> dict[int, int]:
        """Return {addr: current_int32} for all surviving candidates."""
        if not self._cands:
            return {}
        result = {}
        for addr, val in self._cands.items():
            result[addr] = val
        return result

    @property
    def count(self) -> int:
        return len(self._cands) if self._cands is not None else -1


# ── Guided scan session ────────────────────────────────────────────────────

class GuidedScanner:

    def __init__(self, process_name: str):
        self._pname  = process_name
        self._handle = self._attach()
        self._scanner = MemScanner(self._handle)
        # stage_name -> {addr: int32_value_at_that_stage}
        self._stage_values: dict[str, dict[int, int]] = {}

    # ── Process attach ─────────────────────────────────────────────────────

    def _attach(self) -> int:
        try:
            import pymem
        except ImportError:
            sys.exit("\n  pymem not installed.  Run:  pip install pymem\n")

        candidates = [
            self._pname,
            self._pname.replace(".exe", "-Win64-Shipping.exe"),
            self._pname.replace("-Win64-Shipping.exe", ".exe"),
        ]
        for name in candidates:
            try:
                pm = pymem.Pymem(name)
                print(f"\n  Attached to  {name}  (PID {pm.process_id})")
                return pm.process_handle
            except Exception:
                pass

        sys.exit(
            f"\n  Could not find process '{self._pname}'.\n"
            "  Make sure Crimson Desert is running.\n"
            "  You can also pass the executable name as an argument:\n"
            "      python memory_scanner.py CrimsonDesert-Win64-Shipping.exe\n"
        )

    # ── F6 trigger ─────────────────────────────────────────────────────────

    def _wait_f6(self, label: str, prompt: str) -> bool:
        ev = threading.Event()

        def on_press(key):
            try:
                name = getattr(key, "name", None) or getattr(key, "char", "") or ""
                if name.lower() == CAPTURE_KEY:
                    ev.set()
                    return False
            except Exception:
                pass

        print(f"\n  [{label}]  {prompt}")
        print(f"  → Press {CAPTURE_KEY.upper()} from the game.  "
              f"(Timeout: {CAPTURE_TIMEOUT}s)")

        lis = kb.Listener(on_press=on_press, daemon=True)
        lis.start()
        fired = ev.wait(timeout=CAPTURE_TIMEOUT)
        lis.stop()

        if not fired:
            print(f"  Timed out on '{label}' — skipping this stage.")
        return fired

    # ── Main flow ──────────────────────────────────────────────────────────

    def run(self) -> None:
        print("\n=== Crimson Desert Fishing State Scanner ===")
        print(
            "Stay in-game. Press F6 at each prompt — no Alt-Tab needed.\n"
            "Each step has a 3-minute window before it times out and skips.\n"
            "Tip: have the fishing minigame ready to go before starting.\n"
        )

        for i, (stage, prompt) in enumerate(STAGES):
            label = f"{i+1}/{len(STAGES)}  {stage.upper()}"
            if not self._wait_f6(label, prompt):
                continue

            if i == 0:
                # Baseline — snapshot everything, no diff yet
                print("  Taking baseline snapshot…")
                self._scanner.snapshot()
                # Record the initial idle values once we have a candidate list
                # (will be populated after first diff narrows things down)
            else:
                print("  Scanning for changes…")
                self._scanner.diff_changed()
                self._scanner.filter_range(0, 128)

            self._stage_values[stage] = self._scanner.current_values()
            print(f"  Candidates: {self._scanner.count:,}")

        self._analyze_and_save()

    # ── Analysis & save ────────────────────────────────────────────────────

    def _analyze_and_save(self) -> None:
        print("\n\n  Analysing candidates…")

        stage_names = [s for s, _ in STAGES if s in self._stage_values]
        if len(stage_names) < 2:
            print("  ERROR: fewer than 2 stages captured — re-run the scanner.")
            return

        # Only look at stages after idle (idle has no diff yet in our approach)
        diff_stages = [s for s in stage_names if s != "idle"]

        # Collect all candidate addresses across all diff stages
        all_addrs: set[int] = set()
        for s in diff_stages:
            all_addrs.update(self._stage_values[s].keys())

        scored: list[tuple[int, int, dict[str, int]]] = []

        for addr in all_addrs:
            stage_vals: dict[str, int] = {}
            for s in diff_stages:
                v = self._stage_values[s].get(addr)
                if v is not None and 0 <= v <= 128:
                    stage_vals[s] = v

            if len(stage_vals) < 2:
                continue

            n_distinct = len(set(stage_vals.values()))
            scored.append((n_distinct, addr, stage_vals))

        if not scored:
            print(
                "  No suitable candidates found.\n"
                "  Tips:\n"
                "    • Make sure each F6 press was at the correct fishing stage.\n"
                "    • Try running the scanner again with more deliberate timing.\n"
                "    • Reduce MAX_REGION_BYTES if scan is too slow.\n"
            )
            return

        scored.sort(key=lambda x: -x[0])
        top = scored[:10]

        print(f"\n  Top {len(top)} candidates:\n")
        print(f"  {'Rank':<5} {'Address':<20} {'Distinct':<10} Stage values")
        print(f"  {'----':<5} {'-------':<20} {'--------':<10} ------------")
        for rank, (n_dist, addr, svals) in enumerate(top):
            vals_str = "  ".join(f"{s}={v}" for s, v in sorted(svals.items()))
            print(f"  [{rank}]  0x{addr:016X}  {n_dist:<10}  {vals_str}")

        # Auto-select: most distinct values across the most stages
        best_n, best_addr, best_vals = top[0]
        print(f"\n  Auto-selected: 0x{best_addr:016X}")
        print(f"  Value map:     {best_vals}")

        # value -> stage_name  (reverse map for runtime lookup)
        value_to_stage: dict[int, str] = {}
        for stage, val in best_vals.items():
            value_to_stage[val] = stage

        # Save to config
        cfg = config.load()
        cfg.setdefault("memory", {})
        cfg["memory"]["enabled"]       = True
        cfg["memory"]["process_name"]  = self._pname
        cfg["memory"]["state_address"] = best_addr
        # JSON keys must be strings
        cfg["memory"]["state_map"]     = {str(v): s for v, s in value_to_stage.items()}

        config.save(cfg)

        print(f"\n  Saved to config.json.")
        print(f"  Run  python main.py  to start fishing with memory-based detection.\n")
        print(
            "  NOTE: the state_address is for THIS game session.\n"
            "  If you restart Crimson Desert, re-run  python memory_scanner.py\n"
            "  to find the new address (ASLR moves it each launch).\n"
        )


# ── Entry point ────────────────────────────────────────────────────────────

def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PROCESS
    GuidedScanner(name).run()


if __name__ == "__main__":
    main()
