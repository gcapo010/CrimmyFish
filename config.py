"""
config.py — Load, validate, and persist config.json settings.
All other modules import from here so there is a single source of truth.
"""

import json
import os
from copy import deepcopy

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")

# Fallback defaults (mirrors config.json so the app works even if the file is missing)
_DEFAULTS: dict = {
    "cast_key": "space",
    "reel_key": "space",
    "hook_button": "left",
    "direction_keys": {"left": "a", "right": "d", "up": "w", "down": "s"},
    "hotkeys": {"emergency_stop": "f8", "pause_resume": "f7"},
    "regions": {
        "bite_indicator": None,
        "direction_indicator": None,
        "stamina_indicator": None,
        "catch_indicator": None,
    },
    "templates": {
        "bite": None,
        "direction_left": None,
        "direction_right": None,
        "direction_up": None,
        "direction_down": None,
        "tired": None,
        "catch_complete": None,
    },
    "color_ranges": {
        "bite": {"enabled": False, "lower_hsv": [0, 0, 200], "upper_hsv": [180, 30, 255]},
        "tired": {"enabled": False, "lower_hsv": [90, 50, 50], "upper_hsv": [130, 255, 255]},
        "catch_complete": {"enabled": False, "lower_hsv": [35, 50, 50], "upper_hsv": [85, 255, 255]},
    },
    "thresholds": {
        "bite": 0.80,
        "direction": 0.75,
        "tired": 0.80,
        "catch_complete": 0.80,
        "color_pixel_ratio": 0.05,
    },
    "delays": {
        "after_cast_min": 0.5,
        "after_cast_max": 1.0,
        "bite_poll_interval": 0.1,
        "after_hook_min": 0.2,
        "after_hook_max": 0.5,
        "fight_poll_interval": 0.05,
        "direction_key_hold": 0.15,
        "after_reel_min": 1.0,
        "after_reel_max": 2.0,
        "loop_restart_min": 0.8,
        "loop_restart_max": 1.5,
    },
    "max_bite_wait_seconds": 60,
    "max_fight_seconds": 120,
}


def _deep_merge(base: dict, overlay: dict) -> dict:
    """Recursively merge overlay into base so missing keys fall back to defaults."""
    result = deepcopy(base)
    for key, value in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load() -> dict:
    """Return config dict merged with defaults. Creates config.json if absent."""
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            on_disk = json.load(fh)
        return _deep_merge(_DEFAULTS, on_disk)
    # First run — write defaults so the user has a file to edit
    save(_DEFAULTS)
    return deepcopy(_DEFAULTS)


def save(cfg: dict) -> None:
    """Persist cfg to config.json."""
    with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)


def reload() -> dict:
    """Force re-read from disk (useful after calibration edits)."""
    return load()
