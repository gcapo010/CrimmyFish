"""
config.py — Load, validate, and persist config.json settings.
All other modules import from here so there is a single source of truth.
"""

import json
import os
from copy import deepcopy

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")

_DEFAULTS: dict = {
    "cast": {
        "button": "left",
        "hold_seconds_min": 0.8,
        "hold_seconds_max": 1.2,
    },
    "hook_button": "right",
    "reel_key": "space",
    "post_catch_key": "f",
    "direction_keys": {"left": "a", "right": "d", "up": "w", "down": "s"},
    "hotkeys": {"emergency_stop": "f8", "pause_resume": "f7"},
    "regions": {
        # Center-ish crop for bite splash detection
        "bite_indicator": {"left": 560, "top": 200, "width": 800, "height": 480},
        # Center half of the screen — used for optical flow / camera motion
        "motion_sample": {"left": 480, "top": 270, "width": 960, "height": 540},
        # Lower-center area where character holds the caught fish
        "catch_indicator": {"left": 680, "top": 600, "width": 560, "height": 280},
    },
    "templates": {
        "bite": None,
        "catch_complete": None,
    },
    "color_ranges": {
        "bite": {
            "enabled": False,
            "lower_hsv": [0, 0, 180],
            "upper_hsv": [180, 40, 255],
        },
    },
    "thresholds": {
        "bite_template": 0.78,
        # Mean per-pixel brightness change that counts as a motion burst (bite splash)
        "bite_motion": 18.0,
        "catch_complete": 0.80,
        "color_pixel_ratio": 0.04,
    },
    "optical_flow": {
        # Mean displacement (pixels/frame) required to count as active movement
        "motion_threshold": 2.0,
        # Below this magnitude → camera still → fish tired
        "still_threshold": 0.6,
        # Dominant axis must be this many times larger than the other to pick a direction
        "direction_dominance_ratio": 1.4,
    },
    "delays": {
        "after_cast_settle": 1.5,
        "bite_poll_interval": 0.12,
        "after_hook_min": 0.25,
        "after_hook_max": 0.55,
        "fight_frame_interval": 0.08,
        "direction_key_hold": 0.14,
        "reel_poll_interval": 0.12,
        "after_catch_min": 0.8,
        "after_catch_max": 1.4,
        "loop_restart_min": 0.6,
        "loop_restart_max": 1.2,
    },
    "max_bite_wait_seconds": 90,
    "max_fight_seconds": 180,
}


def _deep_merge(base: dict, overlay: dict) -> dict:
    result = deepcopy(base)
    for key, value in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load() -> dict:
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            on_disk = json.load(fh)
        return _deep_merge(_DEFAULTS, on_disk)
    save(_DEFAULTS)
    return deepcopy(_DEFAULTS)


def save(cfg: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)


def reload() -> dict:
    return load()
