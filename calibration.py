"""
calibration.py — Interactive region selector and template saver.

Opens a fullscreen transparent overlay so the user can drag a rectangle to
select a screen region.  Coordinates are stored in config.json.

The three regions needed for Crimson Desert fishing:

  bite_indicator   — The water area where the fishing line lands.
                     Used for motion-burst (splash) detection.
                     Select a rectangle covering the water surface where
                     the float will sit.  Avoid the horizon and sky.

  motion_sample    — The central portion of the screen used to measure
                     camera pan direction during the fight phase.
                     A large region gives more stable phase-correlation
                     results.  Default covers the centre 50% of the screen.

  catch_indicator  — The area showing the player character after a catch,
                     used to detect when the fish is held in hand.
                     Select the lower-centre area where the character model
                     is visible.
"""

import os
import threading
import tkinter as tk
from typing import Callable, Optional

from pynput import keyboard as kb

import config
import logger
import vision


TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")

_REGION_PROMPTS = {
    "bite_indicator": (
        "BITE INDICATOR — water surface where the float lands.\n"
        "Drag a box covering the water area in front of the character.\n"
        "Avoid sky/horizon.  Used for splash motion detection."
    ),
    "motion_sample": (
        "MOTION SAMPLE — centre screen region for camera-pan detection.\n"
        "Drag a large box covering the centre 40–60% of the screen.\n"
        "Avoid UI edges.  Used to track fight direction and tired state."
    ),
    "catch_indicator": (
        "CATCH COMPLETE — area showing the player holding the fish.\n"
        "Drag a box around the player character in the lower-centre area.\n"
        "Used to detect when it is time to press F to stow the fish."
    ),
}

_TEMPLATE_PROMPTS = {
    "bite": (
        "BITE SPLASH template.\n"
        "Switch to the game and trigger a bite (or use a recording screenshot).\n"
        "Press Enter when the water splash is visible."
    ),
    "catch_complete": (
        "CATCH COMPLETE template.\n"
        "Switch to the game and pull in a fish so the character holds it.\n"
        "Press Enter when the character is holding the fish in hand."
    ),
}

# Maps template key → region key (for capturing the crop)
_TEMPLATE_REGION = {
    "bite": "bite_indicator",
    "catch_complete": "catch_indicator",
}


# ---------------------------------------------------------------------------
# Region selector overlay
# ---------------------------------------------------------------------------

class RegionSelector:
    """Fullscreen drag-to-select overlay. Returns region dict or None on cancel."""

    def __init__(self, prompt: str):
        self._prompt = prompt
        self._result: Optional[dict] = None

    def select(self) -> Optional[dict]:
        root = tk.Tk()
        root.attributes("-fullscreen", True)
        root.attributes("-alpha", 0.30)
        root.attributes("-topmost", True)
        root.configure(bg="black")
        root.title("CrimmyFish — Region Selector")

        canvas = tk.Canvas(root, cursor="crosshair", bg="black", highlightthickness=0)
        canvas.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            root,
            text=self._prompt + "\n\nDrag a rectangle then release.  Escape to skip.",
            fg="white",
            bg="#1a1a2e",
            font=("Helvetica", 13),
            justify="center",
            wraplength=900,
        ).place(relx=0.5, rely=0.03, anchor="n")

        start = [0, 0]
        rect_id = [None]

        def on_press(e):
            start[0], start[1] = e.x_root, e.y_root
            if rect_id[0]:
                canvas.delete(rect_id[0])

        def on_drag(e):
            if rect_id[0]:
                canvas.delete(rect_id[0])
            x1 = start[0] - root.winfo_rootx()
            y1 = start[1] - root.winfo_rooty()
            rect_id[0] = canvas.create_rectangle(
                x1, y1, e.x, e.y,
                outline="#89b4fa", width=2, fill="#89b4fa", stipple="gray25",
            )

        def on_release(e):
            x1, y1 = min(start[0], e.x_root), min(start[1], e.y_root)
            x2, y2 = max(start[0], e.x_root), max(start[1], e.y_root)
            w, h = x2 - x1, y2 - y1
            if w > 5 and h > 5:
                self._result = {"left": x1, "top": y1, "width": w, "height": h}
            root.destroy()

        canvas.bind("<ButtonPress-1>", on_press)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", on_release)
        root.bind("<Escape>", lambda _: root.destroy())
        root.mainloop()
        return self._result


# ---------------------------------------------------------------------------
# Calibration manager
# ---------------------------------------------------------------------------

CAPTURE_HOTKEY = "f6"   # Global key pressed in-game to trigger a template capture
CAPTURE_TIMEOUT = 60    # Seconds to wait before skipping if no key pressed


class CalibrationManager:
    def __init__(self, status_callback: Optional[Callable[[str], None]] = None):
        self._cb = status_callback or (lambda _: None)

    def _status(self, msg: str) -> None:
        logger.info(msg)
        self._cb(msg)

    def _wait_for_hotkey(self, prompt: str) -> bool:
        """
        Display *prompt* in the status bar, then block until the user presses
        the global capture hotkey (F6) from anywhere — including while the game
        window is in the foreground.

        Returns True if the hotkey was pressed within CAPTURE_TIMEOUT seconds,
        False if the wait timed out or was interrupted.
        """
        triggered = threading.Event()

        def on_press(key):
            try:
                name = key.name if hasattr(key, "name") else key.char
                if name and name.lower() == CAPTURE_HOTKEY:
                    triggered.set()
                    return False  # Stop this listener
            except AttributeError:
                pass

        self._status(
            f"{prompt}\n"
            f"→ Switch to the game, get the state visible, then press "
            f"{CAPTURE_HOTKEY.upper()} (you do NOT need to Alt-Tab back)."
        )

        listener = kb.Listener(on_press=on_press, daemon=True)
        listener.start()
        fired = triggered.wait(timeout=CAPTURE_TIMEOUT)
        listener.stop()

        if not fired:
            self._status(
                f"Timed out waiting for {CAPTURE_HOTKEY.upper()} — skipping this template."
            )
        return fired

    def calibrate_regions(self) -> None:
        """Walk the user through selecting each detection region."""
        cfg = config.load()
        for key, prompt in _REGION_PROMPTS.items():
            self._status(f"Select region: {key}")
            region = RegionSelector(prompt=prompt).select()
            if region is None:
                self._status(f"Skipped: {key}")
                continue
            cfg["regions"][key] = region
            self._status(f"Saved {key}: {region}")
        config.save(cfg)
        self._status("Regions saved.")

    def calibrate_templates(self, keys: Optional[list] = None) -> None:
        """
        Capture a reference screenshot for each template from the corresponding
        configured region.  Call AFTER calibrate_regions.
        """
        cfg = config.load()
        os.makedirs(TEMPLATE_DIR, exist_ok=True)
        targets = keys or list(_TEMPLATE_PROMPTS.keys())

        for tmpl_key in targets:
            region_key = _TEMPLATE_REGION.get(tmpl_key)
            region = cfg["regions"].get(region_key)
            if region is None:
                self._status(
                    f"Region '{region_key}' not set — run region calibration first."
                )
                continue

            fired = self._wait_for_hotkey(_TEMPLATE_PROMPTS[tmpl_key])
            if not fired:
                continue

            save_path = os.path.join(TEMPLATE_DIR, f"{tmpl_key}.png")
            ok = vision.save_region_screenshot(region, save_path)
            if ok:
                cfg["templates"][tmpl_key] = save_path
                self._status(f"Template saved: {save_path}")
            else:
                self._status(f"Failed to save template: {tmpl_key}")

        config.save(cfg)
        self._status("Template calibration complete.")

    def preview_motion(self) -> None:
        """
        Quick diagnostic: grab two frames from motion_sample 100 ms apart and
        print the measured (dx, dy) and whether the camera appears still.
        Useful for verifying optical_flow thresholds without starting the loop.
        """
        import time
        cfg = config.load()
        region = cfg["regions"].get("motion_sample")
        of_cfg = cfg["optical_flow"]

        if region is None:
            self._status("motion_sample region not configured.")
            return

        f1 = vision.grab_region(region)
        time.sleep(0.1)
        f2 = vision.grab_region(region)

        direction, magnitude, is_tired = vision.detect_fight_direction(
            f1, f2,
            motion_threshold=of_cfg["motion_threshold"],
            still_threshold=of_cfg["still_threshold"],
            dominance_ratio=of_cfg["direction_dominance_ratio"],
        )
        self._status(
            f"Motion preview: direction={direction}, magnitude={magnitude:.3f}, "
            f"is_tired={is_tired}"
        )
