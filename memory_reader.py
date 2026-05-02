"""
memory_reader.py — Real-time fishing state reader via process memory.

After running memory_scanner.py once, config.json contains:
    "memory": {
        "enabled": true,
        "process_name": "CrimsonDesert.exe",
        "state_address": 140234567890,
        "state_map": {"3": "idle", "5": "waiting", "7": "bite", ...}
    }

This module attaches to the process and provides:
    reader.get_state()            -> "idle" | "bite" | "fight" | "caught" | None
    reader.wait_for_state(s, t)   -> bool
    reader.wait_for_any(set, t)   -> str | None

The reader is a singleton — call get_reader() from any module.
Call release_reader() on shutdown to close the process handle.

NOTE: state_address is ASLR-affected and changes each game launch.
Re-run memory_scanner.py whenever Crimson Desert is restarted.
"""

import ctypes
import ctypes.wintypes
import struct
import time
from typing import Optional

import logger

# ── Windows constants ──────────────────────────────────────────────────────
PROCESS_VM_READ           = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400

_RPM  = ctypes.windll.kernel32.ReadProcessMemory
_OH   = ctypes.windll.kernel32.OpenProcess
_CH   = ctypes.windll.kernel32.CloseHandle

# ── Poll rate ─────────────────────────────────────────────────────────────
_POLL_INTERVAL = 0.05   # 50 ms — fast enough for game state, low CPU cost


class MemoryReader:
    """
    Read a single int32 from a remote process and map it to a fishing state string.

    Usage:
        reader = MemoryReader(cfg["memory"])
        ok = reader.connect()
        if ok:
            state = reader.get_state()          # "idle" | "bite" | ... | None
            bit   = reader.wait_for_state("bite", timeout=90)
    """

    def __init__(self, mem_cfg: dict):
        self._process_name = mem_cfg.get("process_name", "CrimsonDesert.exe")
        self._address      = int(mem_cfg.get("state_address", 0))
        # JSON keys are strings; convert to int for lookup
        raw_map: dict = mem_cfg.get("state_map", {})
        self._state_map: dict[int, str] = {int(k): v for k, v in raw_map.items()}
        self._handle: Optional[int] = None
        self._pid:    Optional[int] = None

    # ── Connection ────────────────────────────────────────────────────────

    def connect(self) -> bool:
        """Attach to the game process. Returns True on success."""
        if self._address == 0:
            logger.error("MemoryReader: no state_address in config — run memory_scanner.py first.")
            return False

        try:
            import pymem
            pm = pymem.Pymem(self._process_name)
            self._pid    = pm.process_id
            self._handle = pm.process_handle
            logger.info(
                "MemoryReader: attached to %s (PID %d), address=0x%X",
                self._process_name, self._pid, self._address,
            )
            return True
        except ImportError:
            logger.error("MemoryReader: pymem not installed — pip install pymem")
            return False
        except Exception as exc:
            logger.warning(
                "MemoryReader: could not attach to %s (%s). "
                "Is the game running?  Will retry on first read.",
                self._process_name, exc,
            )
            return False

    def disconnect(self) -> None:
        if self._handle is not None:
            try:
                _CH(self._handle)
            except Exception:
                pass
            self._handle = None
            self._pid    = None
            logger.info("MemoryReader: disconnected.")

    def is_connected(self) -> bool:
        return self._handle is not None

    # ── Single read ───────────────────────────────────────────────────────

    def _read_int32(self) -> Optional[int]:
        """Read a 4-byte little-endian int from the state address. Returns None on error."""
        if self._handle is None:
            return None
        buf = ctypes.create_string_buffer(4)
        n   = ctypes.c_size_t(0)
        ok  = _RPM(self._handle, ctypes.c_void_p(self._address), buf, 4, ctypes.byref(n))
        if ok and n.value == 4:
            return struct.unpack_from("<i", buf.raw)[0]
        return None

    def _reconnect_if_needed(self) -> bool:
        """Try to reconnect if the handle went stale (e.g. game restarted)."""
        if self._handle is not None:
            return True
        return self.connect()

    # ── Public API ────────────────────────────────────────────────────────

    def get_state(self) -> Optional[str]:
        """
        Return the current fishing state name or None.

        None means: read failed, unknown value, or not connected.
        Callers should treat None as "no information" and fall back to CV.
        """
        if not self._reconnect_if_needed():
            return None
        raw = self._read_int32()
        if raw is None:
            return None
        return self._state_map.get(raw)

    def get_raw(self) -> Optional[int]:
        """Return the raw int32 value at the state address (useful for debugging)."""
        if not self._reconnect_if_needed():
            return None
        return self._read_int32()

    def wait_for_state(
        self,
        target: str,
        timeout: float,
        stop_fn=None,
    ) -> bool:
        """
        Block until the fishing state equals `target` or `timeout` seconds pass.

        stop_fn is an optional callable returning True when the loop should abort
        (e.g. ``ic.is_stopped``).

        Returns True if the target state was reached, False on timeout/abort.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if stop_fn and stop_fn():
                return False
            state = self.get_state()
            if state == target:
                return True
            time.sleep(_POLL_INTERVAL)
        return False

    def wait_for_any(
        self,
        targets: set,
        timeout: float,
        stop_fn=None,
    ) -> Optional[str]:
        """
        Block until the state is one of `targets` or `timeout` passes.

        Returns the matched state name, or None on timeout/abort.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if stop_fn and stop_fn():
                return None
            state = self.get_state()
            if state in targets:
                return state
            time.sleep(_POLL_INTERVAL)
        return None

    def wait_for_change(
        self,
        from_state: Optional[str],
        timeout: float,
        stop_fn=None,
    ) -> Optional[str]:
        """
        Block until the state *changes away* from `from_state`.

        Returns the new state (or None on timeout/abort).
        Useful in fight phase: wait until state is no longer "fight".
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if stop_fn and stop_fn():
                return None
            state = self.get_state()
            if state != from_state:
                return state
            time.sleep(_POLL_INTERVAL)
        return None


# ── Module-level singleton ─────────────────────────────────────────────────

_reader: Optional[MemoryReader] = None


def get_reader(cfg: Optional[dict] = None) -> Optional[MemoryReader]:
    """
    Return the module singleton, creating it if `cfg` is provided.

    If memory is not configured or disabled, returns None so callers
    can transparently fall back to CV detection.

    Typical usage (in main.py bootstrap):
        import memory_reader
        memory_reader.get_reader(config.load())   # initialise once

    And in the fishing loop:
        reader = memory_reader.get_reader()
        if reader:
            reader.wait_for_state("bite", 90, stop_fn=ic.is_stopped)
        else:
            # CV fallback
    """
    global _reader
    if cfg is not None:
        mem_cfg = cfg.get("memory", {})
        if not mem_cfg.get("enabled", False):
            _reader = None
            return None
        _reader = MemoryReader(mem_cfg)
        _reader.connect()   # non-fatal if game not running yet
    return _reader


def release_reader() -> None:
    """Disconnect and discard the singleton. Call on app shutdown."""
    global _reader
    if _reader is not None:
        _reader.disconnect()
        _reader = None
