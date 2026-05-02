"""
calibration.py — Interactive region-selector and template-saver.

Opens a full-screen overlay window on top of the desktop where the user can
drag a rectangle to select a region.  The selected region coordinates are
returned so the caller can update config.json and optionally save a template
screenshot.

Works on X11/Wayland via tkinter (no extra deps beyond the standard library).
"""

import os
import threading
import tkinter as tk
from typing import Callable, Optional

import cv2
import numpy as np

import config
import logger
import vision


# ---------------------------------------------------------------------------
# Region selector overlay
# ---------------------------------------------------------------------------

class RegionSelector:
    """
    Draw a semi-transparent fullscreen overlay; user drags a rectangle.
    Returns {"left": x, "top": y, "width": w, "height": h} on completion,
    or None if cancelled (Escape).
    """

    def __init__(self, prompt: str = "Drag to select region. Press Escape to cancel."):
        self._prompt = prompt
        self._result: Optional[dict] = None
        self._start_x = self._start_y = 0
        self._rect_id = None
        self._root: Optional[tk.Tk] = None

    def select(self) -> Optional[dict]:
        self._root = tk.Tk()
        root = self._root
        root.attributes("-fullscreen", True)
        root.attributes("-alpha", 0.3)
        root.attributes("-topmost", True)
        root.configure(bg="black")
        root.title("CrimmyFish — Region Selector")

        canvas = tk.Canvas(root, cursor="crosshair", bg="black", highlightthickness=0)
        canvas.pack(fill=tk.BOTH, expand=True)

        label = tk.Label(
            root,
            text=self._prompt,
            fg="white",
            bg="#333333",
            font=("Helvetica", 14),
        )
        label.place(relx=0.5, rely=0.02, anchor="n")

        def on_press(event):
            self._start_x, self._start_y = event.x_root, event.y_root
            if self._rect_id:
                canvas.delete(self._rect_id)

        def on_drag(event):
            if self._rect_id:
                canvas.delete(self._rect_id)
            # Convert from root coords to canvas coords
            x1 = self._start_x - root.winfo_rootx()
            y1 = self._start_y - root.winfo_rooty()
            x2 = event.x
            y2 = event.y
            self._rect_id = canvas.create_rectangle(
                x1, y1, x2, y2, outline="red", width=2, fill="red", stipple="gray25"
            )

        def on_release(event):
            x1 = min(self._start_x, event.x_root)
            y1 = min(self._start_y, event.y_root)
            x2 = max(self._start_x, event.x_root)
            y2 = max(self._start_y, event.y_root)
            w, h = x2 - x1, y2 - y1
            if w > 5 and h > 5:
                self._result = {"left": x1, "top": y1, "width": w, "height": h}
            root.destroy()

        def on_escape(event):
            root.destroy()

        canvas.bind("<ButtonPress-1>", on_press)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", on_release)
        root.bind("<Escape>", on_escape)

        root.mainloop()
        return self._result


# ---------------------------------------------------------------------------
# Calibration controller
# ---------------------------------------------------------------------------

TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")

_REGION_LABELS = {
    "bite_indicator": "BITE INDICATOR — the area that flashes when a fish bites",
    "direction_indicator": "DIRECTION INDICATOR — the arrow/icon showing fish direction",
    "stamina_indicator": "STAMINA/TIRED INDICATOR — the bar or icon showing fish stamina",
    "catch_indicator": "CATCH COMPLETE INDICATOR — the prompt shown when the catch finishes",
}

_TEMPLATE_LABELS = {
    "bite": ("bite", "bite_indicator"),
    "direction_left": ("direction_left", "direction_indicator"),
    "direction_right": ("direction_right", "direction_indicator"),
    "direction_up": ("direction_up", "direction_indicator"),
    "direction_down": ("direction_down", "direction_indicator"),
    "tired": ("tired", "stamina_indicator"),
    "catch_complete": ("catch_complete", "catch_indicator"),
}


class CalibrationManager:
    """
    Drives the interactive calibration session.
    Optionally accepts a status_callback(str) for live GUI updates.
    """

    def __init__(self, status_callback: Optional[Callable[[str], None]] = None):
        self._cb = status_callback or (lambda msg: None)

    def _status(self, msg: str) -> None:
        logger.info(msg)
        self._cb(msg)

    def calibrate_regions(self) -> None:
        """Walk the user through selecting each detection region."""
        cfg = config.load()
        for key, prompt in _REGION_LABELS.items():
            self._status(f"Select region: {key}\n{prompt}")
            selector = RegionSelector(prompt=f"{prompt}\n\nDrag a rectangle, then release.")
            region = selector.select()
            if region is None:
                self._status(f"Skipped: {key}")
                continue
            cfg["regions"][key] = region
            self._status(f"Saved region {key}: {region}")
        config.save(cfg)
        self._status("All regions saved to config.json.")

    def calibrate_templates(self, keys: Optional[list] = None) -> None:
        """
        For each template key, grab the currently configured region and save a
        screenshot crop as the reference template.

        Call this AFTER calibrate_regions so the regions are set.
        """
        cfg = config.load()
        os.makedirs(TEMPLATE_DIR, exist_ok=True)

        targets = keys or list(_TEMPLATE_LABELS.keys())
        for tmpl_key in targets:
            filename, region_key = _TEMPLATE_LABELS[tmpl_key]
            region = cfg["regions"].get(region_key)
            if region is None:
                self._status(f"No region set for {tmpl_key} — run region calibration first.")
                continue

            save_path = os.path.join(TEMPLATE_DIR, f"{filename}.png")
            self._status(
                f"Capturing template '{tmpl_key}' from region '{region_key}'.\n"
                "Switch to the game, position the indicator, then press Enter."
            )
            input("  [Calibration] Press Enter when the game is showing the correct state...")

            ok = vision.save_region_screenshot(region, save_path)
            if ok:
                cfg["templates"][tmpl_key] = save_path
                self._status(f"Template saved: {save_path}")
            else:
                self._status(f"Failed to save template for {tmpl_key}.")

        config.save(cfg)
        self._status("Template calibration complete.")

    def preview_detection(self, state_key: str) -> None:
        """
        Grab the configured region for *state_key* and run detection, printing
        the result.  Useful for verifying thresholds without starting the loop.
        """
        cfg = config.load()
        region_map = {
            "bite": "bite_indicator",
            "tired": "stamina_indicator",
            "catch_complete": "catch_indicator",
        }
        region_key = region_map.get(state_key)
        if not region_key:
            self._status(f"Unknown state key: {state_key}")
            return

        region = cfg["regions"].get(region_key)
        tmpl_path = cfg["templates"].get(state_key)
        color_cfg = cfg["color_ranges"].get(state_key)
        thresh = cfg["thresholds"].get(state_key, 0.80)
        ratio = cfg["thresholds"].get("color_pixel_ratio", 0.05)

        detected, conf = vision.detect_state(region, tmpl_path, color_cfg, thresh, ratio)
        self._status(
            f"Preview [{state_key}]: detected={detected}, confidence={conf:.3f}"
        )
