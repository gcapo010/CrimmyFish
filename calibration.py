"""
calibration.py — One-shot guided fishing calibration.

Press Calibrate in the GUI and follow the log prompts.
Stay in-game throughout — press F6 at each stage from inside Crimson Desert.
No Alt-Tab or region-dragging needed.

Stages captured with F6:
  1. idle     — rod held, line NOT in water
  2. waiting  — line sitting still in water, no bite yet
  3. bite     — camera is tilting RIGHT NOW (fish is biting)
  4. fight    — camera actively panning during the fight
  5. catch    — golden fish-info panel visible bottom-right

What gets written to config.json and templates/:
  • regions (motion_sample, bite_indicator, catch_indicator)
      derived automatically from screen dimensions
  • thresholds.bite_scene_change
      50 % of the measured waiting→bite scene difference
  • templates/catch_complete.png
      cropped from the catch frame (bottom-right panel area)
"""

import os
import threading
from typing import Callable, Optional

import cv2
import numpy as np
from pynput import keyboard as kb

import config
import logger
import vision

CAPTURE_KEY     = "f6"
CAPTURE_TIMEOUT = 120   # seconds per step before skipping

# ── Step definitions ───────────────────────────────────────────────────────

_STEPS = [
    (
        "idle",
        "IDLE — Stand still with your rod out, line NOT yet in the water.\n"
        "Press F6 to capture the idle baseline.",
    ),
    (
        "waiting",
        "WAITING FOR BITE — Cast your line and let the float settle.\n"
        "Press F6 once the float is sitting still and you are waiting.",
    ),
    (
        "bite",
        "BITE — A fish is biting RIGHT NOW (camera is shifting down).\n"
        "Press F6 the INSTANT the camera starts to tilt toward the water.",
    ),
    (
        "hooked",
        "FISH HOOKED — The fish is on the line and actively pulling.\n"
        "You should already see camera motion. Press F6 to capture this state.\n"
        "This is used to confirm the hook landed.",
    ),
    (
        "fight_left",
        "FIGHTING — FISH PULLING LEFT — Camera is panning RIGHT.\n"
        "Press F6 while the camera is visibly moving to the right.\n"
        "Two frames will be captured automatically to measure the motion vector.",
    ),
    (
        "fight_right",
        "FIGHTING — FISH PULLING RIGHT — Camera is panning LEFT.\n"
        "Press F6 while the camera is visibly moving to the left.\n"
        "Two frames will be captured automatically to measure the motion vector.",
    ),
    (
        "catch",
        "CATCH COMPLETE — Golden fish-info panel is visible bottom-right.\n"
        "Press F6 when the panel is fully on screen.",
    ),
]

# Steps that need two frames (captured at F6 + 150 ms later) to measure motion.
_PAIR_STEPS = {"fight_left", "fight_right"}


# ── Manager ────────────────────────────────────────────────────────────────

class CalibrationManager:
    def __init__(self, status_callback: Optional[Callable[[str], None]] = None):
        self._cb = status_callback or (lambda _: None)

    def _status(self, msg: str) -> None:
        logger.info(msg)
        self._cb(msg)

    # ── F6 capture ─────────────────────────────────────────────────────────

    def _capture_on_f6(self, step_name: str, prompt: str) -> Optional[np.ndarray]:
        """Display prompt, block until F6 pressed in-game, return full-screen frame."""
        triggered = threading.Event()

        def on_press(key):
            try:
                name = key.name if hasattr(key, "name") else key.char
                if name and name.lower() == CAPTURE_KEY:
                    triggered.set()
                    return False
            except AttributeError:
                pass

        self._status(
            f"[{step_name.upper()}]  {prompt}\n"
            f"→ Switch to the game, get into position, then press "
            f"{CAPTURE_KEY.upper()} (no Alt-Tab needed).  "
            f"Timeout: {CAPTURE_TIMEOUT}s."
        )

        listener = kb.Listener(on_press=on_press, daemon=True)
        listener.start()
        fired = triggered.wait(timeout=CAPTURE_TIMEOUT)
        listener.stop()

        if not fired:
            self._status(
                f"Timed out waiting for {CAPTURE_KEY.upper()} on step "
                f"'{step_name}' — skipping this stage."
            )
            return None

        frame = vision.grab_full_screen()
        h, w = frame.shape[:2]
        self._status(f"  ✓  {step_name}  ({w}×{h} px captured)")
        return frame

    def _capture_on_f6_pair(self, step_name: str, prompt: str):
        """
        Capture a frame at F6 press, then a second frame 150 ms later.
        Returns (frame_a, frame_b) or None if the step was skipped.
        The two frames are used with phaseCorrelate to measure a motion vector.
        """
        import time as _time
        f1 = self._capture_on_f6(step_name, prompt)
        if f1 is None:
            return None
        _time.sleep(0.15)
        f2 = vision.grab_full_screen()
        self._status("  Second frame captured for motion measurement.")
        return (f1, f2)

    # ── Public entry point ─────────────────────────────────────────────────

    def run_full_calibration(self) -> None:
        """
        Walk through every fishing stage once and derive all detection
        parameters from the real captured frames.
        """
        self._status("=== Guided Fishing Calibration ===")
        self._status(
            "Stay in-game.  Press F6 at each prompt.\n"
            "Each step has a 2-minute window — skip any step by waiting it out.\n"
            "Defaults are kept for any skipped step."
        )

        frames: dict = {}
        for key, prompt in _STEPS:
            if key in _PAIR_STEPS:
                frames[key] = self._capture_on_f6_pair(key, prompt)
            else:
                frames[key] = self._capture_on_f6(key, prompt)

        self._derive_and_save(frames)

    # ── Derivation ─────────────────────────────────────────────────────────

    def _derive_and_save(self, frames: dict) -> None:
        self._status("Analysing captures — deriving thresholds and templates…")
        cfg = config.load()

        self._auto_regions(cfg, frames)
        self._derive_bite_threshold(cfg, frames)
        self._calibrate_fight_directions(cfg, frames)
        self._save_catch_template(cfg, frames)
        self._validate_fight(cfg, frames)

        config.save(cfg)
        self._status(
            "=== Calibration complete ===\n"
            "config.json updated.  Ready to fish — press Start (F6)."
        )

    def _auto_regions(self, cfg: dict, frames: dict) -> None:
        """
        Set all three detection regions from screen dimensions.
        No dragging needed — proportions match observed gameplay:
          motion_sample   = centre 50%   (stable for phase-correlation)
          bite_indicator  = upper-centre (horizon area most affected by bite tilt)
          catch_indicator = bottom-right 40%×45% (golden fish-info panel)
        """
        ref = next((f for f in frames.values() if f is not None), None)
        if ref is None:
            self._status("No frames captured — regions unchanged.")
            return
        h, w = ref.shape[:2]

        cfg["regions"]["motion_sample"] = {
            "left": w // 4,       "top": h // 4,
            "width": w // 2,      "height": h // 2,
        }
        cfg["regions"]["bite_indicator"] = {
            "left": w // 4,       "top": h // 6,
            "width": w // 2,      "height": h // 2,
        }
        cfg["regions"]["catch_indicator"] = {
            "left":  int(w * 0.60), "top":    int(h * 0.55),
            "width": int(w * 0.40), "height": int(h * 0.45),
        }
        self._status(
            f"Regions auto-set for {w}×{h} screen:\n"
            f"  motion_sample:   centre 50%\n"
            f"  bite_indicator:  upper-centre 50%\n"
            f"  catch_indicator: bottom-right 40%×45%"
        )

    def _derive_bite_threshold(self, cfg: dict, frames: dict) -> None:
        """
        Measure mean-abs-diff between 'waiting' and 'bite' frames on the
        motion_sample region, then set bite_scene_change = 50% of that value.

        50% keeps the threshold well above idle noise (~1–5) while staying
        safely below the actual bite magnitude (~25–60).
        """
        w_frame = frames.get("waiting")
        b_frame = frames.get("bite")
        if w_frame is None or b_frame is None:
            self._status(
                "Cannot derive bite threshold — 'waiting' or 'bite' frame missing.\n"
                f"  Keeping current value: "
                f"{cfg['thresholds'].get('bite_scene_change', 25.0)}"
            )
            return

        r  = cfg["regions"]["motion_sample"]
        x1 = r["left"];  y1 = r["top"]
        x2 = min(x1 + r["width"],  w_frame.shape[1])
        y2 = min(y1 + r["height"], w_frame.shape[0])
        cw = w_frame[y1:y2, x1:x2]
        cb = b_frame[y1:y2, x1:x2]

        if cw.size == 0 or cb.size == 0:
            self._status("motion_sample crop is empty — skipping bite threshold.")
            return

        g1   = cv2.cvtColor(cw, cv2.COLOR_BGR2GRAY).astype(np.float32)
        g2   = cv2.cvtColor(cb, cv2.COLOR_BGR2GRAY).astype(np.float32)
        diff = float(np.abs(g1 - g2).mean())

        # Report idle noise level for context
        idle_frame = frames.get("idle")
        idle_noise = 0.0
        if idle_frame is not None:
            ci = idle_frame[y1:y2, x1:x2]
            if ci.size > 0:
                g_i = cv2.cvtColor(ci, cv2.COLOR_BGR2GRAY).astype(np.float32)
                g_w = cv2.cvtColor(cw, cv2.COLOR_BGR2GRAY).astype(np.float32)
                idle_noise = float(np.abs(g_i - g_w).mean())

        # 65 % of the observed bite diff.
        # 50 % was too aggressive — small ambient variations in the waiting
        # scene or a slight cast-animation residual could exceed it.
        # 65 % leaves a wider gap above the noise floor while still sitting
        # comfortably below the actual bite magnitude.
        threshold = max(round(diff * 0.65, 1), 10.0)
        cfg["thresholds"]["bite_scene_change"] = threshold
        self._status(
            f"Bite scene-change threshold:\n"
            f"  idle baseline diff = {idle_noise:.1f}\n"
            f"  waiting→bite diff  = {diff:.1f}\n"
            f"  threshold set to   = {threshold:.1f}  (65% of bite diff)"
        )

    def _save_catch_template(self, cfg: dict, frames: dict) -> None:
        """Crop the catch_indicator region from the catch frame and save as template."""
        catch = frames.get("catch")
        if catch is None:
            self._status("Catch frame not captured — template unchanged.")
            return

        tmpl_dir = os.path.join(os.path.dirname(__file__), "templates")
        os.makedirs(tmpl_dir, exist_ok=True)

        r    = cfg["regions"]["catch_indicator"]
        h, w = catch.shape[:2]
        x1   = r["left"];  y1 = r["top"]
        x2   = min(x1 + r["width"], w)
        y2   = min(y1 + r["height"], h)
        crop = catch[y1:y2, x1:x2]

        if crop.size == 0:
            self._status("Catch crop region is empty — template not saved.")
            return

        path = os.path.join(tmpl_dir, "catch_complete.png")
        cv2.imwrite(path, crop)
        cfg["templates"]["catch_complete"] = path
        self._status(
            f"Catch template saved: {path}\n"
            f"  ({crop.shape[1]}×{crop.shape[0]} px)"
        )

    def _calibrate_fight_directions(self, cfg: dict, frames: dict) -> None:
        """
        Use the fight_left and fight_right frame pairs to:
          1. Measure actual phaseCorrelate dx/dy during active fighting.
          2. Set motion_threshold to 40% of the observed magnitude so
             normal fight motion is reliably detected.
          3. Confirm the direction convention (dx > 0 = world right =
             camera left = fish going left).
          4. Derive hook_confirm_motion from the hooked frame vs idle.
        """
        magnitudes = []
        r = cfg["regions"]["motion_sample"]

        def crop(f):
            h, w = f.shape[:2]
            x2 = min(r["left"] + r["width"],  w)
            y2 = min(r["top"]  + r["height"], h)
            return f[r["top"]:y2, r["left"]:x2]

        for label, direction_name in [("fight_left", "left"), ("fight_right", "right")]:
            pair = frames.get(label)
            if pair is None or not isinstance(pair, tuple):
                self._status(f"Skipping fight-direction calibration for '{label}'.")
                continue

            f1, f2 = pair
            c1, c2 = crop(f1), crop(f2)
            if c1.size == 0 or c2.size == 0:
                continue

            dx, dy  = vision.measure_camera_motion(c1, c2)
            mag     = (dx * dx + dy * dy) ** 0.5
            magnitudes.append(mag)

            # Direction convention check:
            #   fish pulling LEFT  → camera pans right → world shifts right → dx > 0
            #   fish pulling RIGHT → camera pans left  → world shifts left  → dx < 0
            if direction_name == "left":
                conv = "correct" if dx > 0 else "REVERSED — check direction_keys in config"
            else:
                conv = "correct" if dx < 0 else "REVERSED — check direction_keys in config"

            self._status(
                f"Fight {direction_name.upper()}: dx={dx:.2f} dy={dy:.2f} "
                f"mag={mag:.2f}  convention={conv}"
            )

        if magnitudes:
            avg_mag = sum(magnitudes) / len(magnitudes)
            # Set motion_threshold to 40% of observed fight magnitude.
            # This ensures normal fight motion comfortably exceeds the threshold
            # while the "still" / tired state (mag ≈ 0) remains well below it.
            new_threshold = max(round(avg_mag * 0.40, 2), 0.5)
            cfg["optical_flow"]["motion_threshold"] = new_threshold
            self._status(
                f"Fight motion_threshold set to {new_threshold:.2f} "
                f"(40% of avg observed magnitude {avg_mag:.2f})"
            )

        # Derive hook_confirm_motion from hooked vs idle frames
        hooked = frames.get("hooked")
        idle   = frames.get("idle")
        if hooked is not None and idle is not None:
            ci = crop(idle)
            ch = crop(hooked)
            if ci.size > 0 and ch.size > 0:
                dx, dy = vision.measure_camera_motion(ci, ch)
                mag    = (dx * dx + dy * dy) ** 0.5
                # Threshold at 40% of observed hook-state motion
                hook_thresh = max(round(mag * 0.40, 2), 0.5)
                cfg["thresholds"]["hook_confirm_motion"] = hook_thresh
                self._status(
                    f"Hook confirm motion set to {hook_thresh:.2f} "
                    f"(40% of measured hooked magnitude {mag:.2f})"
                )

    def _validate_fight(self, cfg: dict, frames: dict) -> None:
        """
        Run phaseCorrelate between idle and fight frames to confirm the
        motion_sample region gives usable direction signals during the fight.
        """
        idle  = frames.get("idle")
        fight = frames.get("fight")
        if idle is None or fight is None:
            return

        r    = cfg["regions"]["motion_sample"]
        h, w = idle.shape[:2]
        x1   = r["left"];  y1 = r["top"]
        x2   = min(x1 + r["width"], w)
        y2   = min(y1 + r["height"], h)
        ci   = idle[y1:y2, x1:x2]
        cf   = fight[y1:y2, x1:x2]

        if ci.size == 0 or cf.size == 0:
            return

        dx, dy = vision.measure_camera_motion(ci, cf)
        mag    = float(np.sqrt(dx * dx + dy * dy))
        mt     = cfg["optical_flow"]["motion_threshold"]
        ok     = mag > mt
        self._status(
            f"Fight motion check:\n"
            f"  dx={dx:.2f}  dy={dy:.2f}  magnitude={mag:.2f}\n"
            f"  motion_threshold={mt}  →  "
            f"{'OK' if ok else f'WARNING: mag too low — lower motion_threshold in config.json'}"
        )

    # ── Diagnostic ─────────────────────────────────────────────────────────

    def preview_motion(self) -> None:
        """Grab two frames 100 ms apart and report camera motion — useful for tuning."""
        import time
        cfg    = config.load()
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
            f"Motion preview: direction={direction}  "
            f"magnitude={magnitude:.3f}  is_tired={is_tired}"
        )
