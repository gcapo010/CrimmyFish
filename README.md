# CrimmyFish — Crimson Desert Auto Fishing Assistant

Screen-vision-only desktop automation for the single-player game **Crimson Desert**.  
No memory injection, no packet manipulation, no anti-cheat bypass — only screenshots and normal OS mouse/keyboard input.

---

## How the fishing loop works

| Step | What happens in-game | What CrimmyFish does |
|------|----------------------|----------------------|
| Cast | Aim at water, hold LMB ~1 s then release | Holds left mouse button for a random 0.8–1.2 s |
| Wait for bite | Fish bites → water splashes at float | Detects a motion burst (frame-diff spike) in the water region |
| Hook | Character reacts, right-click to set hook | Sends a right mouse click |
| Fight | Camera follows fish; move rod opposite direction | Measures camera-pan via `phaseCorrelate`; presses opposite WASD key |
| Fish tired | Camera stops moving | Detects near-zero motion magnitude |
| Reel | Hold Space | Holds Space until catch-complete detected |
| Stow | Character holds fish; press F | Presses F, waits, then restarts loop |

---

## Requirements

- **Python 3.10+**
- **Windows 11** (also works on Linux/X11)
- Crimson Desert running in **windowed** or **borderless windowed** mode  
  (fullscreen exclusive can block `mss` screen capture)
- Screen resolution: **1920 × 1080** (default regions pre-configured for this)

---

## Installation

```bash
# 1. Navigate to the project folder
cd CrimmyFish

# 2. Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux/macOS

# 3. Install dependencies
pip install -r requirements.txt

# 4. Launch
python main.py
```

---

## First-time Calibration

Calibration sets the screen regions CrimmyFish watches and captures reference
screenshots for template matching.  Run it once before starting the loop.

### Step 1 — Select screen regions

1. Start Crimson Desert, go to a fishing spot, and cast your line so the float is visible.
2. Launch CrimmyFish and click **Calibrate**.
3. A transparent overlay will appear for each region.  Drag a rectangle, then release.

| Region | What to select |
|--------|---------------|
| **Bite indicator** | The water surface area where the float sits. Cover roughly where splashes appear. Avoid sky/horizon. |
| **Motion sample** | A large centre-screen rectangle (roughly 40–60 % of screen). This is used to measure camera movement during the fight. Avoid UI chrome near the edges. |
| **Catch indicator** | The lower-centre area showing your character model. Used to detect when your character is holding a caught fish. |

### Step 2 — Capture templates

After region selection, the calibration wizard will ask you to capture two templates:

**Bite splash template**
1. Switch to Crimson Desert.
2. Get a fish to bite (or use a screenshot from a previous session where the splash is visible).
3. Press **Enter** in the terminal when the splash is on screen.

**Catch complete template**
1. Pull in a fish until your character is holding it in hand.
2. Press **Enter** in the terminal.

> Templates are saved to the `templates/` folder and paths are written to `config.json`.  
> You can recapture any template by deleting its path in `config.json` and re-running calibration.

---

## Configuration (`config.json`)

```jsonc
{
  "cast": {
    "button": "left",
    "hold_seconds_min": 0.8,   // Minimum hold time for casting
    "hold_seconds_max": 1.2    // Maximum hold time (randomised each cast)
  },
  "hook_button": "right",      // Right-click to set the hook
  "reel_key": "space",         // Key held to reel in
  "post_catch_key": "f",       // Key pressed to stow the fish

  "direction_keys": {
    "left":  "a",              // Fish going left  → camera pans left  → press D
    "right": "d",              // Fish going right → camera pans right → press A
    "up":    "w",              // Fish going up    → press S
    "down":  "s"               // Fish going down  → press W
  },

  "optical_flow": {
    "motion_threshold": 2.0,   // Min camera displacement (px/frame) to count as movement
    "still_threshold":  0.6,   // Below this → fish is tired
    "direction_dominance_ratio": 1.4  // One axis must be this × larger to pick a direction
  },

  "thresholds": {
    "bite_template":    0.78,  // Template match confidence for bite
    "bite_motion":      18.0,  // Mean frame-diff (0–255) to count as a splash
    "catch_complete":   0.80   // Template confidence for catch-complete
  }
}
```

---

## Tuning Detection

### Bite never detected

The motion detector fires when the mean per-pixel brightness change in the
water region exceeds `thresholds.bite_motion` (default 18.0 out of 255).

- **Too many false positives** (fires on ripples): raise the value, e.g. `25.0`.
- **Misses bites**: lower it, e.g. `12.0`.
- Alternatively, enable template matching by capturing a `bite` template during
  calibration — it will fire first if confidence exceeds `thresholds.bite_template`.

### Fight direction wrong or erratic

`optical_flow.motion_threshold` and `direction_dominance_ratio` control when
a direction is committed to.

- If the bot presses keys when the fish is idle, **raise** `motion_threshold`.
- If the bot frequently chooses no direction when the fish is clearly moving,
  **lower** `motion_threshold` or **lower** `direction_dominance_ratio`.
- Make the `motion_sample` region as large as practical — a larger region gives
  a more stable `phaseCorrelate` result.

### Fish treated as tired too early / too late

Adjust `optical_flow.still_threshold`:
- Tired detected too early (fish still fighting): **raise** it, e.g. `1.0`.
- Tired never detected (loop times out): **lower** it, e.g. `0.3`.

### Catch complete never detected

Make sure you captured the `catch_complete` template during calibration.  If the
character's pose varies between catches, lower `thresholds.catch_complete` to
`0.70–0.75`.  As a fallback you can increase `reel_poll_interval` and simply let
the loop time out naturally after 45 seconds.

---

## Hotkeys

| Key | Action |
|-----|--------|
| **F7** | Pause / Resume |
| **F8** | Emergency Stop (instantly halts all input) |

Both hotkeys work globally even when the game window is focused.

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Nothing happens after clicking Start | Check that `config.json → regions` are set (not default values for a different resolution) |
| `mss` captures a black screen | Switch Crimson Desert to borderless windowed mode |
| Keys/clicks don't reach the game | Ensure the game is the active foreground window before starting |
| `pynput` permission error on Windows | Run the terminal as the same user as the game (not administrator vs normal) |
| phaseCorrelate always returns ~0 | The motion_sample region may be capturing a static UI overlay — move it to cover the game world |

---

## Project Structure

```
CrimmyFish/
├── main.py               Entry point + fishing state machine
├── config.py             Config load/save with deep-merge defaults
├── vision.py             Screen capture, template match, motion detection
├── input_controller.py   Mouse hold/click, key tap/hold, hotkey listener
├── calibration.py        Interactive region selector + template saver
├── gui.py                tkinter dark-theme control panel
├── logger.py             Rotating file log + GUI queue handler
├── config.json           User settings (edit directly or via calibration)
├── requirements.txt
├── templates/            Reference screenshots (created during calibration)
└── crimmyfish.log        Runtime log (auto-rotated, max 2 MB × 3 files)
```

---

## Safety & Fair Play

This tool only reads screen pixels and sends normal OS-level mouse and keyboard
events — the same as a human operating the game.  It does **not** read or write
game memory, modify game files, intercept network traffic, or bypass any
protection mechanism.  Use it in single-player / offline sessions only.
