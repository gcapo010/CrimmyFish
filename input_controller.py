"""
input_controller.py — All keyboard and mouse output goes through here.

Uses pynput for both keyboard and mouse control on Windows 11 / X11.

Key design choices:
  - Every action checks _paused / _stopped before executing.
  - Random micro-jitter on delays so the timing pattern is not perfectly regular.
  - Mouse hold (press + sleep + release) is used for casting.
  - release_key / release_mouse always run even when paused/stopped so we
    never leave a key or button stuck down.
"""

import random
import time
from typing import Optional

from pynput import keyboard as kb
from pynput import mouse as ms

import logger

# ---------------------------------------------------------------------------
# Shared state flags
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
# Controllers
# ---------------------------------------------------------------------------

_kb_ctrl = kb.Controller()
_ms_ctrl = ms.Controller()


def _active() -> bool:
    return not _stopped and not _paused


def _jitter(base: float, spread: float = 0.025) -> float:
    return max(0.0, base + random.uniform(-spread, spread))


def _key(key_str: str) -> kb.Key | str:
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
        **{f"f{i}": getattr(kb.Key, f"f{i}") for i in range(1, 13)},
    }
    return special.get(key_str.lower(), key_str)


def _btn(button_str: str) -> ms.Button:
    return {
        "left": ms.Button.left,
        "right": ms.Button.right,
        "middle": ms.Button.middle,
    }.get(button_str.lower(), ms.Button.left)


# ---------------------------------------------------------------------------
# Keyboard API
# ---------------------------------------------------------------------------

def tap_key(key_str: str, hold: float = 0.05) -> None:
    """Press and release a key."""
    if not _active():
        return
    k = _key(key_str)
    try:
        _kb_ctrl.press(k)
        time.sleep(_jitter(hold))
        _kb_ctrl.release(k)
        logger.debug("tap_key(%s, %.3fs)", key_str, hold)
    except Exception as exc:
        logger.error("tap_key error: %s", exc)


def hold_key(key_str: str) -> None:
    """Press and hold; caller must call release_key."""
    if not _active():
        return
    try:
        _kb_ctrl.press(_key(key_str))
        logger.debug("hold_key(%s)", key_str)
    except Exception as exc:
        logger.error("hold_key error: %s", exc)


def release_key(key_str: str) -> None:
    """Release a held key. Always executes regardless of pause/stop state."""
    try:
        _kb_ctrl.release(_key(key_str))
        logger.debug("release_key(%s)", key_str)
    except Exception as exc:
        logger.error("release_key error: %s", exc)


# ---------------------------------------------------------------------------
# Mouse API
# ---------------------------------------------------------------------------

def click_mouse(button_str: str = "left") -> None:
    """Single click at the current cursor position."""
    if not _active():
        return
    try:
        _ms_ctrl.click(_btn(button_str))
        logger.debug("click_mouse(%s)", button_str)
    except Exception as exc:
        logger.error("click_mouse error: %s", exc)


def hold_mouse(button_str: str, seconds: float) -> None:
    """
    Press and hold a mouse button for *seconds*, then release.
    Used for casting: left-hold to charge, release to cast.
    Checks for stop/pause mid-hold so we can abort cleanly.
    """
    if not _active():
        return
    b = _btn(button_str)
    try:
        _ms_ctrl.press(b)
        logger.debug("hold_mouse(%s, %.2fs) start", button_str, seconds)
        elapsed = 0.0
        chunk = 0.05
        while elapsed < seconds:
            if _stopped:
                break
            step = min(chunk, seconds - elapsed)
            time.sleep(step)
            elapsed += step
        _ms_ctrl.release(b)
        logger.debug("hold_mouse(%s) released after %.2fs", button_str, elapsed)
    except Exception as exc:
        try:
            _ms_ctrl.release(b)
        except Exception:
            pass
        logger.error("hold_mouse error: %s", exc)


def release_mouse(button_str: str) -> None:
    """Release a mouse button. Always executes regardless of pause/stop state."""
    try:
        _ms_ctrl.release(_btn(button_str))
        logger.debug("release_mouse(%s)", button_str)
    except Exception as exc:
        logger.error("release_mouse error: %s", exc)


# ---------------------------------------------------------------------------
# Sleep helpers
# ---------------------------------------------------------------------------

def safe_sleep(seconds: float) -> None:
    """Sleep in small chunks so stop/pause can interrupt mid-sleep."""
    chunk = 0.05
    elapsed = 0.0
    while elapsed < seconds:
        if _stopped:
            return
        step = min(chunk, seconds - elapsed)
        time.sleep(step)
        elapsed += step


def random_sleep(min_s: float, max_s: float) -> None:
    safe_sleep(random.uniform(min_s, max_s))


# ---------------------------------------------------------------------------
# Hotkey listener (daemon thread)
# ---------------------------------------------------------------------------

class HotkeyListener:
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
        if not key_name:
            return
        name = key_name.lower()
        if name == self._stop_key:
            logger.info("Emergency stop hotkey pressed.")
            set_stopped(True)
            if self._on_stop:
                self._on_stop()
        elif name == self._pause_key:
            new_state = not is_paused()
            set_paused(new_state)
            logger.info("Pause toggled — paused=%s", new_state)
            if self._on_pause_toggle:
                self._on_pause_toggle(new_state)

    def start(self) -> None:
        self._listener = kb.Listener(on_press=self._on_press, daemon=True)
        self._listener.start()
        logger.info("Hotkey listener started (stop=%s, pause=%s).", self._stop_key, self._pause_key)

    def stop(self) -> None:
        if self._listener:
            self._listener.stop()
