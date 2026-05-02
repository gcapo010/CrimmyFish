"""
sprite_slicer.py — Cut the reference sprite sheet into individual UI elements.

Usage:
    1. Drop your sprite sheet into sprites/ and name it spritesheet.png
    2. Run:  python sprite_slicer.py
    3. Launch:  python main.py   (will auto-detect the extracted sprites)

If any element looks wrong (cut in the wrong place or too much padding),
open sprites/sprite_config.json, adjust the x/y/w/h values for that element,
and re-run this script.  The _desc field in each entry tells you what to look for.

Tip: open spritesheet.png in Paint or any image editor and hover the cursor
over the corner of each element — most editors show the pixel coordinates
in the status bar.
"""

import json
import os
import sys

try:
    from PIL import Image
except ImportError:
    sys.exit("Pillow is required.  Run:  pip install Pillow")

BASE         = os.path.dirname(__file__)
SPRITES_DIR  = os.path.join(BASE, "sprites")
OUT_DIR      = os.path.join(SPRITES_DIR, "extracted")
CONFIG_PATH  = os.path.join(SPRITES_DIR, "sprite_config.json")


def _flatten_alpha(img: Image.Image, bg_rgb=(13, 11, 8)) -> Image.Image:
    """
    Composite an RGBA image onto a solid background so tkinter never has to
    handle alpha transparency (which behaves inconsistently on Windows).
    """
    bg = Image.new("RGB", img.size, bg_rgb)
    if img.mode == "RGBA":
        bg.paste(img, mask=img.split()[3])
    else:
        bg.paste(img.convert("RGB"))
    return bg


def main():
    if not os.path.exists(CONFIG_PATH):
        sys.exit(f"Config not found: {CONFIG_PATH}")

    with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)

    source = os.path.join(SPRITES_DIR, cfg["source"])
    if not os.path.exists(source):
        print(f"\n  Sprite sheet not found: {source}")
        print("  Drop your sprite sheet PNG into the sprites/ folder")
        print("  and make sure it is named  spritesheet.png\n")
        sys.exit(1)

    os.makedirs(OUT_DIR, exist_ok=True)
    sheet = Image.open(source).convert("RGBA")
    print(f"\nLoaded sprite sheet: {sheet.width} x {sheet.height} px")
    print(f"Output folder:  {OUT_DIR}\n")

    ok = 0
    for name, spec in cfg["elements"].items():
        # Skip metadata keys that start with _
        if name.startswith("_"):
            continue

        x, y, w, h = spec["x"], spec["y"], spec["w"], spec["h"]

        # Clamp to sheet bounds so a bad config doesn't crash
        x = max(0, min(x, sheet.width  - 1))
        y = max(0, min(y, sheet.height - 1))
        w = min(w, sheet.width  - x)
        h = min(h, sheet.height - y)

        crop = sheet.crop((x, y, x + w, y + h))

        tw = spec.get("target_w")
        th = spec.get("target_h")
        if tw and th and (tw != w or th != h):
            crop = crop.resize((tw, th), Image.LANCZOS)

        # Flatten so saved PNGs have no alpha surprises
        crop = _flatten_alpha(crop)

        out_path = os.path.join(OUT_DIR, f"{name}.png")
        crop.save(out_path, "PNG")
        print(f"  {name:<20s}  {crop.width:>4}x{crop.height:<4}  {out_path}")
        ok += 1

    print(f"\n{ok} sprites saved to sprites/extracted/")
    print("Run  python main.py  — the GUI will use the sprites automatically.\n")


if __name__ == "__main__":
    main()
