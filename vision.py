"""
vision.py — Screen capture and computer-vision detection.

Two detection modes are used depending on what the game exposes:

  A. Template / colour matching  — used for the bite splash and catch-complete state,
     where a specific visual appears in a fixed screen region.

  B. Camera motion (phase correlation) — used for the fight phase.
     Crimson Desert has no visible direction HUD; instead the camera pans to follow
     the fish.  cv2.phaseCorrelate compares two frames and returns the (dx, dy)
     translation vector directly, which tells us which way the camera is moving.

     Convention:
       camera pans RIGHT  → dx negative  → fish going right → press A
       camera pans LEFT   → dx positive  → fish going left  → press D
       camera pans DOWN   → dy negative  → fish going down  → press S
       camera pans UP     → dy positive  → fish going up    → press W

     When the fish is tired the camera stops moving; phaseCorrelate magnitude ≈ 0.
"""

import os
import math
from typing import Optional

import cv2
import mss
import numpy as np

import logger

_template_cache: dict[str, np.ndarray] = {}


# ---------------------------------------------------------------------------
# Screen capture
# ---------------------------------------------------------------------------

def grab_region(region: Optional[dict]) -> Optional[np.ndarray]:
    """Capture a screen region; return BGR array or None if region is None."""
    if region is None:
        return None
    with mss.mss() as sct:
        shot = sct.grab(region)
    return np.array(shot)[:, :, :3]


def grab_full_screen() -> np.ndarray:
    with mss.mss() as sct:
        monitor = sct.monitors[1]
        shot = sct.grab(monitor)
    return np.array(shot)[:, :, :3]


# ---------------------------------------------------------------------------
# Template matching
# ---------------------------------------------------------------------------

def _load_template(path: str) -> Optional[np.ndarray]:
    if path in _template_cache:
        return _template_cache[path]
    if not os.path.exists(path):
        logger.warning("Template not found: %s", path)
        return None
    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        logger.warning("cv2 could not read template: %s", path)
        return None
    _template_cache[path] = img
    return img


def match_template(
    frame: np.ndarray,
    template_path: str,
    threshold: float = 0.80,
) -> tuple[bool, float]:
    """Normalised cross-correlation template match. Returns (detected, confidence)."""
    if frame is None:
        return False, 0.0
    tmpl = _load_template(template_path)
    if tmpl is None:
        return False, 0.0
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if len(frame.shape) == 3 else frame
    if tmpl.shape[0] > gray.shape[0] or tmpl.shape[1] > gray.shape[1]:
        logger.warning("Template larger than capture region — skipping: %s", template_path)
        return False, 0.0
    result = cv2.matchTemplate(gray, tmpl, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, _ = cv2.minMaxLoc(result)
    return max_val >= threshold, float(max_val)


# ---------------------------------------------------------------------------
# Colour threshold detection
# ---------------------------------------------------------------------------

def detect_color(
    frame: np.ndarray,
    lower_hsv: list,
    upper_hsv: list,
    pixel_ratio_threshold: float = 0.05,
) -> tuple[bool, float]:
    """
    Returns (detected, ratio) where ratio is matching pixel fraction.
    Enable this as a fallback when template matching is unreliable — e.g. the
    bite flash is a bright white glow: lower_hsv=[0,0,200], upper_hsv=[180,30,255].
    """
    if frame is None:
        return False, 0.0
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(
        hsv,
        np.array(lower_hsv, dtype=np.uint8),
        np.array(upper_hsv, dtype=np.uint8),
    )
    ratio = float(np.count_nonzero(mask)) / float(mask.size)
    conf = min(ratio / max(pixel_ratio_threshold, 1e-6), 1.0)
    return ratio >= pixel_ratio_threshold, conf


# ---------------------------------------------------------------------------
# Bite detection
# ---------------------------------------------------------------------------

def detect_bite_motion(
    frame_prev: np.ndarray,
    frame_curr: np.ndarray,
    motion_threshold: float = 18.0,
) -> tuple[bool, float]:
    """
    Detect the bite splash by looking for a sudden brightness-change burst
    between two consecutive frames of the water region.

    The splash animation creates a large per-pixel difference compared to the
    gently rippling idle water.  motion_threshold is the mean absolute difference
    (0–255 scale) that must be exceeded.

    Returns (detected, mean_diff).
    """
    if frame_prev is None or frame_curr is None:
        return False, 0.0
    gray_prev = cv2.cvtColor(frame_prev, cv2.COLOR_BGR2GRAY)
    gray_curr = cv2.cvtColor(frame_curr, cv2.COLOR_BGR2GRAY)
    diff = cv2.absdiff(gray_prev, gray_curr)
    mean_diff = float(diff.mean())
    return mean_diff >= motion_threshold, mean_diff


def detect_bite_scene_change(
    frame_prev: np.ndarray,
    frame_curr: np.ndarray,
    threshold: float = 25.0,
) -> tuple[bool, float]:
    """
    Detect the dramatic camera tilt that occurs when a fish bites in
    Crimson Desert.

    When a fish bites the camera pitches sharply downward — the horizon
    shifts from ~30% to ~10% of the frame height and water fills the
    majority of the screen.  This creates a very large mean-absolute-
    difference (typically 25–60) between consecutive frames captured on
    the centre-screen motion_sample region, far above the idle ripple
    baseline (1–5) and gradual fight panning (5–15).

    We use simple mean-abs-diff rather than phaseCorrelate here because
    the angular change is large enough that frame content no longer
    overlaps, which causes phaseCorrelate to alias and return incorrect
    displacement vectors.

    Returns (detected, mean_diff).
    """
    if frame_prev is None or frame_curr is None:
        return False, 0.0
    g1 = cv2.cvtColor(frame_prev, cv2.COLOR_BGR2GRAY)
    g2 = cv2.cvtColor(frame_curr, cv2.COLOR_BGR2GRAY)
    mean_diff = float(cv2.absdiff(g1, g2).mean())
    return mean_diff >= threshold, mean_diff


def detect_bite(
    region: Optional[dict],
    template_path: Optional[str],
    prev_frame: Optional[np.ndarray],
    color_cfg: Optional[dict],
    template_threshold: float = 0.78,
    motion_threshold: float = 18.0,
    color_pixel_ratio: float = 0.04,
) -> tuple[bool, float, np.ndarray]:
    """
    Unified bite detector.  Tries (in order):
      1. Template match on current frame
      2. Motion burst between prev_frame and current frame
      3. Colour threshold on current frame (if enabled)

    Returns (detected, confidence, current_frame).
    current_frame is returned so the caller can pass it as prev_frame next iteration.
    """
    frame = grab_region(region)
    if frame is None:
        return False, 0.0, prev_frame

    # 1. Template
    if template_path:
        detected, conf = match_template(frame, template_path, template_threshold)
        if detected:
            return True, conf, frame

    # 2. Motion burst
    if prev_frame is not None:
        detected, mean_diff = detect_bite_motion(prev_frame, frame, motion_threshold)
        if detected:
            conf = min(mean_diff / max(motion_threshold, 1e-6), 1.0)
            return True, conf, frame

    # 3. Colour fallback
    if color_cfg and color_cfg.get("enabled"):
        detected, conf = detect_color(
            frame,
            color_cfg["lower_hsv"],
            color_cfg["upper_hsv"],
            color_pixel_ratio,
        )
        if detected:
            return True, conf, frame

    return False, 0.0, frame


# ---------------------------------------------------------------------------
# Camera motion detection — fight phase
# ---------------------------------------------------------------------------

def _to_float32_gray(frame: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if len(frame.shape) == 3 else frame
    return gray.astype(np.float32)


def measure_camera_motion(
    frame_prev: np.ndarray,
    frame_curr: np.ndarray,
) -> tuple[float, float]:
    """
    Use phase correlation to find the (dx, dy) translation between two frames.

    Phase correlation is essentially an FFT-based normalised cross-correlation.
    It returns the sub-pixel shift of frame_curr relative to frame_prev.
    A large value means the camera is panning; near-zero means the camera is still.

    Returns (dx, dy) in pixels.  Positive dx = world moved right = camera panned left.
    """
    f1 = _to_float32_gray(frame_prev)
    f2 = _to_float32_gray(frame_curr)
    # Hanning window reduces edge artefacts
    win = cv2.createHanningWindow((f1.shape[1], f1.shape[0]), cv2.CV_32F)
    (dx, dy), _ = cv2.phaseCorrelate(f1, f2, win)
    return float(dx), float(dy)


def detect_fight_direction(
    frame_prev: np.ndarray,
    frame_curr: np.ndarray,
    motion_threshold: float = 2.0,
    still_threshold: float = 0.6,
    dominance_ratio: float = 1.4,
) -> tuple[Optional[str], float, bool]:
    """
    Classify camera motion into a fish direction and detect the tired state.

    Camera pan direction maps to fish movement (and therefore the counter key)
    as follows — the world moves opposite to the camera pan:

        dx > 0  (world right / camera left)  → fish going LEFT  → press D
        dx < 0  (world left  / camera right) → fish going RIGHT → press A
        dy > 0  (world down  / camera up)    → fish going UP    → press S
        dy < 0  (world up    / camera down)  → fish going DOWN  → press W

    Returns (direction_or_None, magnitude, is_tired).
    is_tired is True when the camera is still (magnitude < still_threshold).
    direction is one of "left", "right", "up", "down", or None (ambiguous/idle).
    """
    if frame_prev is None or frame_curr is None:
        return None, 0.0, False

    dx, dy = measure_camera_motion(frame_prev, frame_curr)
    magnitude = math.sqrt(dx * dx + dy * dy)

    if magnitude < still_threshold:
        return None, magnitude, True  # Fish is tired

    if magnitude < motion_threshold:
        return None, magnitude, False  # Moving but too slow to classify yet

    abs_x, abs_y = abs(dx), abs(dy)

    # Only commit to a direction when one axis clearly dominates
    if abs_x >= abs_y * dominance_ratio:
        direction = "left" if dx > 0 else "right"
    elif abs_y >= abs_x * dominance_ratio:
        direction = "up" if dy > 0 else "down"
    else:
        direction = None  # Diagonal — wait for a clearer reading

    return direction, magnitude, False


# ---------------------------------------------------------------------------
# Catch-complete detection
# ---------------------------------------------------------------------------

def detect_catch_complete(
    region: Optional[dict],
    template_path: Optional[str],
    threshold: float = 0.80,
) -> tuple[bool, float]:
    """
    Detect the fish-caught state.

    Primary: template match against templates/catch_complete.png saved
    during calibration.  This is the most reliable method.

    Fallback (only when no template exists): golden HSV colour density in
    the catch_indicator region.  The fish-info panel title text is a warm
    amber-gold (HSV H≈30-48, S≈140, V≈160).  The threshold is set high
    (8 % of pixels) to avoid false positives from the gold-coloured fishing
    rod, character trim, and UI elements visible during normal gameplay.

    Returns (detected, confidence).
    """
    frame = grab_region(region)
    if frame is None:
        return False, 0.0

    # Primary: calibrated template match
    if template_path:
        detected, conf = match_template(frame, template_path, threshold)
        if detected:
            return True, conf
        # Template exists but didn't match — skip colour fallback to avoid
        # the false-positive risk when a good template is already in place.
        return False, conf

    # Fallback (no template): golden panel colour detection.
    # Requires a large fraction of golden pixels (8 %) so ambient gold from
    # gear/rod/UI doesn't trigger it.  Run calibration to get a template
    # and avoid relying on this path.
    hsv   = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask  = cv2.inRange(
        hsv,
        np.array([28, 140, 160], dtype=np.uint8),
        np.array([48, 255, 255], dtype=np.uint8),
    )
    ratio = float(np.count_nonzero(mask)) / max(float(mask.size), 1)
    if ratio >= 0.08:
        return True, min(ratio / 0.12, 1.0)

    return False, 0.0


# ---------------------------------------------------------------------------
# Utility — save a region crop to disk (used by calibration)
# ---------------------------------------------------------------------------

def save_region_screenshot(region: dict, save_path: str) -> bool:
    frame = grab_region(region)
    if frame is None:
        logger.error("Could not capture region for saving.")
        return False
    os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)
    ok = cv2.imwrite(save_path, frame)
    if ok:
        _template_cache.pop(save_path, None)
        logger.info("Saved template: %s", save_path)
    else:
        logger.error("cv2.imwrite failed for path: %s", save_path)
    return ok
