"""
vision.py — Screen capture and computer-vision detection.

Detection strategy (in priority order for each state):
  1. Template matching — precise but requires a saved screenshot crop.
  2. Color threshold  — fast fallback when the indicator is a solid-color UI element.

Both methods return a (detected: bool, confidence: float) tuple so callers can
log confidence and the GUI can display it.
"""

import os
from typing import Optional

import cv2
import mss
import numpy as np

import logger

# Cached template images keyed by their config path so we don't re-read disk
# on every poll iteration.
_template_cache: dict[str, np.ndarray] = {}


# ---------------------------------------------------------------------------
# Screen capture
# ---------------------------------------------------------------------------

def grab_region(region: Optional[dict]) -> Optional[np.ndarray]:
    """
    Capture a screen region and return a BGR numpy array.

    region must be {"left": int, "top": int, "width": int, "height": int}
    or None (in which case None is returned).
    """
    if region is None:
        return None
    with mss.mss() as sct:
        shot = sct.grab(region)
    # mss returns BGRA; drop alpha channel
    frame = np.array(shot)[:, :, :3]
    return frame


def grab_full_screen() -> np.ndarray:
    """Return a BGR screenshot of the primary monitor."""
    with mss.mss() as sct:
        monitor = sct.monitors[1]  # monitors[0] is the virtual all-monitors rect
        shot = sct.grab(monitor)
    return np.array(shot)[:, :, :3]


# ---------------------------------------------------------------------------
# Template matching
# ---------------------------------------------------------------------------

def _load_template(path: str) -> Optional[np.ndarray]:
    """Load and cache a grayscale template from disk."""
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
    """
    Run normalised cross-correlation template matching.

    Returns (detected, best_confidence).
    Converts the frame to grayscale internally so templates should be saved
    as grayscale (or BGR — both work).
    """
    if frame is None:
        return False, 0.0

    tmpl = _load_template(template_path)
    if tmpl is None:
        return False, 0.0

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if len(frame.shape) == 3 else frame

    # Guard: template must be smaller than the region
    if tmpl.shape[0] > gray.shape[0] or tmpl.shape[1] > gray.shape[1]:
        logger.warning(
            "Template (%s) is larger than the capture region — skipping match.",
            template_path,
        )
        return False, 0.0

    result = cv2.matchTemplate(gray, tmpl, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, _ = cv2.minMaxLoc(result)
    detected = max_val >= threshold
    return detected, float(max_val)


# ---------------------------------------------------------------------------
# Color threshold detection
# ---------------------------------------------------------------------------

def detect_color(
    frame: np.ndarray,
    lower_hsv: list,
    upper_hsv: list,
    pixel_ratio_threshold: float = 0.05,
) -> tuple[bool, float]:
    """
    Detect whether enough pixels in *frame* fall within an HSV colour range.

    pixel_ratio_threshold — fraction of total pixels that must match (0–1).
    Returns (detected, ratio) where ratio is the actual matching pixel fraction.

    Tip: use an HSV colour picker to find the right range for your game's UI.
    For example, the bite-indicator glow in Crimson Desert is often a bright
    white/yellow flash — HSV upper_s near 30, upper_v near 255 works well.
    """
    if frame is None:
        return False, 0.0

    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    lo = np.array(lower_hsv, dtype=np.uint8)
    hi = np.array(upper_hsv, dtype=np.uint8)
    mask = cv2.inRange(hsv, lo, hi)
    ratio = float(np.count_nonzero(mask)) / float(mask.size)
    return ratio >= pixel_ratio_threshold, ratio


# ---------------------------------------------------------------------------
# Direction detection — multi-template approach
# ---------------------------------------------------------------------------

def detect_direction(
    frame: np.ndarray,
    templates: dict,   # {"left": path, "right": path, "up": path, "down": path}
    threshold: float = 0.75,
) -> tuple[Optional[str], float]:
    """
    Compare all direction templates against *frame* and return the best match.

    Returns (direction_name_or_None, confidence).
    direction_name will be one of "left", "right", "up", "down".
    """
    best_dir: Optional[str] = None
    best_conf: float = 0.0

    for direction, path in templates.items():
        if not path:
            continue
        detected, conf = match_template(frame, path, threshold)
        if detected and conf > best_conf:
            best_dir = direction
            best_conf = conf

    return best_dir, best_conf


# ---------------------------------------------------------------------------
# Combined detector — tries template first, falls back to colour if enabled
# ---------------------------------------------------------------------------

def detect_state(
    region: Optional[dict],
    template_path: Optional[str],
    color_cfg: Optional[dict],
    template_threshold: float = 0.80,
    color_pixel_ratio: float = 0.05,
) -> tuple[bool, float]:
    """
    Unified detector for a named game state (bite, tired, catch_complete).

    Tries template matching first (if a template path is configured).
    Falls back to colour detection if color_cfg["enabled"] is True.
    Returns (detected, confidence).
    """
    frame = grab_region(region)
    if frame is None:
        return False, 0.0

    # --- Template matching ---
    if template_path:
        detected, conf = match_template(frame, template_path, template_threshold)
        if detected:
            return True, conf
        # If template was configured but didn't fire, still try colour fallback

    # --- Colour threshold fallback ---
    if color_cfg and color_cfg.get("enabled"):
        detected, ratio = detect_color(
            frame,
            color_cfg["lower_hsv"],
            color_cfg["upper_hsv"],
            color_pixel_ratio,
        )
        # Express colour ratio as a 0–1 confidence analogue
        conf = min(ratio / max(color_pixel_ratio, 1e-6), 1.0)
        return detected, conf

    return False, 0.0


# ---------------------------------------------------------------------------
# Utility: save a region crop to disk (used by calibration)
# ---------------------------------------------------------------------------

def save_region_screenshot(region: dict, save_path: str) -> bool:
    """Grab *region* and save it as a PNG template. Returns True on success."""
    frame = grab_region(region)
    if frame is None:
        logger.error("Could not capture region for saving.")
        return False
    os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)
    ok = cv2.imwrite(save_path, frame)
    if ok:
        # Invalidate cache so the new template is loaded fresh
        _template_cache.pop(save_path, None)
        logger.info("Saved template: %s", save_path)
    else:
        logger.error("cv2.imwrite failed for path: %s", save_path)
    return ok
