# CrimmyFish — Crimson Desert Auto Fishing Assistant

A screen-vision-only desktop automation tool for the single-player game **Crimson Desert**.  
No memory injection, no packet manipulation, no anti-cheat bypass — only screenshots and normal OS input.

---

## Requirements

- Python 3.10+
- Linux (X11 preferred) or Windows
- Crimson Desert running in **windowed** or **borderless windowed** mode (fullscreen exclusive can block `mss`)

---

## Installation

```bash
# 1. Clone / copy this project
cd CrimmyFish

# 2. Create a virtual environment (recommended)
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Launch
python main.py
```

### Linux / Wayland note

`pynput` works natively on X11. On Wayland you may need to run the game and
the tool under XWayland, or install `xdotool` and switch `input_controller.py`
to shell out via `subprocess.run(["xdotool", ...])`.

---

## First-time Setup & Calibration

Calibration teaches the tool where to look on your screen and what each
game state looks like.

### Step 1 — Select screen regions

1. Start Crimson Desert and position the fishing UI on screen.
2. Launch CrimmyFish (`python main.py`).
3. Click **Calibrate**.
4. A transparent overlay will appear for each region in turn.  
   Drag a rectangle around the relevant UI element, then release the mouse.

| Region | What to select |
|--------|---------------|
| **Bite indicator** | The small icon/flash that appears when a fish bites |
| **Direction indicator** | The arrow or icon showing which way the fish is pulling |
| **Stamina indicator** | The bar or icon that shows when the fish is tired |
| **Catch indicator** | The prompt or banner that appears when a catch is complete |

### Step 2 — Capture templates

After regions are saved, the tool will ask you to capture a reference
screenshot for each game state:

1. Switch to Crimson Desert.
2. Trigger the relevant state (e.g. let a fish bite for the bite template).
3. Switch back to the terminal and press **Enter**.
4. The crop is saved to `templates/`.

> **Tip:** You can recapture individual templates by editing `config.json`
> to clear the `"templates"` paths and re-running calibration.

---

## Configuration (`config.json`)

All settings live in `config.json` in the project root.

```jsonc
{
  "cast_key": "space",          // Key to cast the rod
  "reel_key": "space",          // Key held to reel in
  "hook_button": "left",        // Mouse button to hook the fish
  "direction_keys": {
    "left":  "a",               // Counter key when fish pulls left
    "right": "d",
    "up":    "w",
    "down":  "s"
  },
  "hotkeys": {
    "emergency_stop": "f8",     // Immediately stop everything
    "pause_resume":   "f7"      // Pause/resume without stopping
  },
  "thresholds": {
    "bite":              0.80,  // Template match confidence to count as bite
    "direction":         0.75,  // Direction template confidence
    "tired":             0.80,  // Tired-state confidence
    "catch_complete":    0.80,  // Catch-complete confidence
    "color_pixel_ratio": 0.05   // Fraction of region pixels that must match colour
  },
  "delays": {
    "after_cast_min":      0.5, // Seconds to wait after casting (min)
    "after_cast_max":      1.0, // Seconds to wait after casting (max)
    "bite_poll_interval":  0.1, // How often to check for a bite
    "fight_poll_interval": 0.05 // How often to check direction / tired state
    // ... see config.json for all delay fields
  },
  "max_bite_wait_seconds": 60,  // Give up waiting for a bite after this long
  "max_fight_seconds":    120   // Give up fighting after this long
}
```

---

## Improving Detection Accuracy

### Template matching is unreliable

Template matching (`TM_CCOEFF_NORMED`) fails when:
- The game resolution or UI scale changes
- The indicator animates (changes size/colour between frames)
- Screenshots are taken mid-animation

**Fix:** Lower the threshold slightly (e.g. `0.70`) or switch to colour detection.

### Colour threshold detection

Enable colour detection in `config.json`:

```json
"color_ranges": {
  "bite": {
    "enabled": true,
    "lower_hsv": [20, 100, 200],
    "upper_hsv": [35, 255, 255]
  }
}
```

To find the right HSV values:
1. Take a screenshot of the indicator in its active state.
2. Open it in GIMP or use the helper snippet below:

```python
import cv2, numpy as np
img = cv2.imread("templates/bite.png")
hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
print("H min/max:", hsv[:,:,0].min(), hsv[:,:,0].max())
print("S min/max:", hsv[:,:,1].min(), hsv[:,:,1].max())
print("V min/max:", hsv[:,:,2].min(), hsv[:,:,2].max())
```

Typical indicators in Crimson Desert:
- **Bite flash** — bright white/yellow glow → H: 15-35, S: 50-180, V: 200-255
- **Stamina bar (full/tired)** — blue/grey vs. orange → adjust H accordingly
- **Catch complete** — green tick or banner → H: 40-80

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Nothing happens when I click Start | Check that calibration regions are set (`config.json` → `regions` must not be all `null`) |
| Bite never detected | Lower `thresholds.bite` or enable colour detection |
| Direction keys fire wrong | Re-capture direction templates while the fish is clearly pulling that way |
| Input keys don't reach the game | Make sure Crimson Desert window is focused; try running both as the same user |
| pynput keyboard error on Linux | Install `python3-xlib`: `sudo apt install python3-xlib` |
| `mss` captures a black screen | Switch game to borderless windowed mode |

---

## Project Structure

```
CrimmyFish/
├── main.py               # Entry point, fishing state machine
├── config.py             # Config load/save helpers
├── vision.py             # Screenshot capture, template & colour detection
├── input_controller.py   # Keyboard/mouse output + hotkey listener
├── calibration.py        # Interactive region selector + template saver
├── gui.py                # tkinter control panel
├── logger.py             # Structured logging (file + GUI queue)
├── config.json           # User settings
├── requirements.txt
├── templates/            # Saved reference screenshots (created on first calibration)
└── crimmyfish.log        # Runtime log (auto-rotated)
```

---

## Safety & Fair Play

- This tool only reads pixels and sends normal OS keyboard/mouse events.
- It does **not** read or write game memory, modify game files, intercept network traffic, or bypass any protection system.
- Use it only in single-player / offline sessions as intended.
