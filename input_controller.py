"""
input_controller.py — All keyboard and mouse output goes through here.

Uses pynput so we can drive input on both X11 and Wayland (with xdotool
fallback notes in the README for Wayland users).

Key design choices:
  - Every action checks a shared _paused / _stopped flag before executing.
  - Random micro-jitter is added to delays so the timing pattern is not perfectly
    regular, which avoids input-overlap issues common in rapid automation.
  - Mouse clicks go to the current cursor position by default (the game cursor
    is already in the right place after casting).
"""

import random
import time
from typing import Optional

from pynput import keyboard as kb
from pynput import mouse as ms

import logger

# ---------------------------------------------------------------------------
# Shared state flags — set by the main loop and hotkey listener
# ---------------------------------------------------------------------------

_stopped: bool = False
_paused: bool = False


def set_stopped(v: bool) -> None:
    global _stopped
    _stopped = v


def set_paused(v: bool) -> None:
    global _paused
    _paused = v


def is_stopped() -> bool:
    return _stopped


def is_paused() -> bool:
    return _paused


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_kb_controller = kb.Controller()
_ms_controller = ms.Controller()


def _check_active() -> bool:
    """Return True if we should send input right now."""
    return not _stopped and not _paused


def _jitter(base: float, spread: float = 0.03) -> float:
    """Add a small random offset to a delay value."""
    return max(0.0, base + random.uniform(-spread, spread))


def _key_from_str(key_str: str) -> kb.Key | str:
    """Convert a string like 'space', 'f8', or 'a' to a pynput key."""
    special = {
        "space": kb.Key.space,
        "enter": kb.Key.enter,
        "shift": kb.Key.shift,
        "ctrl": kb.Key.ctrl,
        "alt": kb.Key.alt,
        "tab": kb.Key.tab,
        "esc": kb.Key.esc,
        "escape": kb.Key.esc,
        "backspace": kb.Key.backspace,
        "delete": kb.Key.delete,
        "up": kb.Key.up,
        "down": kb.Key.down,
        "left": kb.Key.left,
        "right": kb.Key.right,
        "f1": kb.Key.f1,  "f2": kb.Key.f2,  "f3": kb.Key.f3,  "f4": kb.Key.f4,
        "f5": kb.Key.f5,  "f6": kb.Key.f6,  "f7": kb.Key.f7,  "f8": kb.Key.f8,
        "f9": kb.Key.f9,  "f10": kb.Key.f10, "f11": kb.Key.f11, "f12": kb.Key.f12,
    }
    return special.get(key_str.lower(), key_str)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def tap_key(key_str: str, hold: float = 0.05) -> None:
    """Press and release a key with an optional hold duration."""
    if not _check_active():
        return
    key = _key_from_str(key_str)
    try:
        _kb_controller.press(key)
        time.sleep(_jitter(hold))
        _kb_controller.release(key)
        logger.debug("tap_key(%s, hold=%.3f)", key_str, hold)
    except Exception as exc:
        logger.error("tap_key error: %s", exc)


def hold_key(key_str: str) -> None:
    """Press and hold a key (caller must call release_key)."""
    if not _check_active():
        return
    key = _key_from_str(key_str)
    try:
        _kb_controller.press(key)
        logger.debug("hold_key(%s)", key_str)
    except Exception as exc:
        logger.error("hold_key error: %s", exc)


def release_key(key_str: str) -> None:
    """Release a previously held key. Always runs even when paused/stopped."""
    key = _key_from_str(key_str)
    try:
        _kb_controller.release(key)
        logger.debug("release_key(%s)", key_str)
    except Exception as exc:
        logger.error("release_key error: %s", exc)


def click_mouse(button_str: str = "left") -> None:
    """Perform a single mouse click at the current cursor position."""
    if not _check_active():
        return
    btn_map = {
        "left": ms.Button.left,
        "right": ms.Button.right,
        "middle": ms.Button.middle,
    }
    btn = btn_map.get(button_str.lower(), ms.Button.left)
    try:
        _ms_controller.click(btn)
        logger.debug("click_mouse(%s)", button_str)
    except Exception as exc:
        logger.error("click_mouse error: %s", exc)


def safe_sleep(seconds: float) -> None:
    """
    Sleep in small increments so we can react to stop/pause mid-sleep.
    Wakes at most every 50 ms to check flags.
    """
    chunk = 0.05
    elapsed = 0.0
    while elapsed < seconds:
        if _stopped:
            return
        step = min(chunk, seconds - elapsed)
        time.sleep(step)
        elapsed += step


def random_sleep(min_s: float, max_s: float) -> None:
    """Sleep for a random duration between min_s and max_s seconds."""
    safe_sleep(random.uniform(min_s, max_s))


# ---------------------------------------------------------------------------
# Hotkey listener — runs in its own daemon thread
# ---------------------------------------------------------------------------

class HotkeyListener:
    """
    Listens for global hotkeys (emergency stop, pause/resume) without
    interfering with normal game input.
    """

    def __init__(self, stop_key: str, pause_key: str, on_stop=None, on_pause_toggle=None):
        self._stop_key = stop_key.lower()
        self._pause_key = pause_key.lower()
        self._on_stop = on_stop
        self._on_pause_toggle = on_pause_toggle
        self._listener: Optional[kb.Listener] = None

    def _on_press(self, key) -> None:
        try:
            key_name = key.name if hasattr(key, "name") else key.char
        except AttributeError:
            return

        if key_name and key_name.lower() == self._stop_key:
            logger.info("Emergency stop hotkey pressed.")
            set_stopped(True)
            if self._on_stop:
                self._on_stop()

        elif key_name and key_name.lower() == self._pause_key:
            new_state = not is_paused()
            set_paused(new_state)
            logger.info("Pause toggled — paused=%s", new_state)
            if self._on_pause_toggle:
                self._on_pause_toggle(new_state)

    def start(self) -> None:
        self._listener = kb.Listener(on_press=self._on_press, daemon=True)
        self._listener.start()
        logger.info(
            "Hotkey listener started (stop=%s, pause=%s).",
            self._stop_key,
            self._pause_key,
        )

    def stop(self) -> None:
        if self._listener:
            self._listener.stop()
