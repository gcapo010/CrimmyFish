"""
sprite_generator.py — Generate all UI sprites programmatically with PIL.

No spritesheet.png required. Run once before launching main.py:
    python sprite_generator.py

Writes sprites/extracted/*.png and immediately makes the app ready to run.
"""

import os
from PIL import Image, ImageDraw

BASE    = os.path.dirname(__file__)
OUT_DIR = os.path.join(BASE, "sprites", "extracted")

# Palette — mirrors gui.py constants exactly
BG       = (13,  11,  8)
PANEL_BG = (20,  16,  8)
GOLD_M   = (122, 92,  18)
GOLD_H   = (201, 168, 76)

GEM = {
    "green": ((10,  46,  20),  (26,  138, 64)),
    "red":   ((46,  10,  10),  (138, 26,  26)),
    "gold":  ((46,  32,  10),  (184, 144, 12)),
    "blue":  ((10,  20,  46),  (26,  64,  138)),
}


# ── Drawing primitives ─────────────────────────────────────────────────────

def _border(d: ImageDraw.ImageDraw, x1, y1, x2, y2, fill=PANEL_BG, corner=9):
    """Filled rect with double gold border and diamond corner ornaments."""
    d.rectangle([(x1, y1), (x2, y2)], fill=fill)
    d.rectangle([(x1, y1), (x2, y2)], outline=GOLD_M, width=1)
    o = 5
    d.rectangle([(x1+o, y1+o), (x2-o, y2-o)], outline=GOLD_H, width=1)
    s = corner
    for cx, cy in [(x1, y1), (x2, y1), (x1, y2), (x2, y2)]:
        d.polygon([(cx, cy-s), (cx+s, cy), (cx, cy+s), (cx-s, cy)],
                  fill=GOLD_M, outline=GOLD_H, width=1)


def _gem(d: ImageDraw.ImageDraw, cx, cy, r, color):
    base, hi = GEM[color]
    d.ellipse([(cx-r, cy-r), (cx+r, cy+r)], fill=base, outline=GOLD_H, width=1)
    # specular highlight
    d.ellipse([(cx - r//2, cy - r), (cx + r//4, cy - r//4)], fill=hi)


# ── Sprite builders ────────────────────────────────────────────────────────

def title_bar(w=800, h=62):
    img = Image.new("RGB", (w, h), BG)
    d   = ImageDraw.Draw(img)
    _border(d, 0, 0, w-1, h-1)
    _gem(d, 24,   h//2, 11, "green")
    _gem(d, w-24, h//2, 11, "red")
    return img


def panel(w, h):
    """Plain ornate border — header text and content drawn by gui.py."""
    img = Image.new("RGB", (w, h), BG)
    d   = ImageDraw.Draw(img)
    _border(d, 0, 0, w-1, h-1)
    return img


def button(w=186, h=46, color="green"):
    """
    Colour-coded ornate button background — NO text.
    Text is rendered by SpriteButton.create_text() so it stays dynamic
    (e.g. Pause ↔ Resume) and never doubles up on the sprite.
    """
    base, hi = GEM[color]
    img = Image.new("RGB", (w, h), BG)
    d   = ImageDraw.Draw(img)

    # Tinted fill
    d.rectangle([(2, 2), (w-2, h-2)], fill=base)

    # Gradient highlight strip at the top (lit-from-above effect)
    strip = max(3, h // 8)
    for row in range(strip):
        t   = row / strip
        rgb = tuple(int(hi[c] + (base[c] - hi[c]) * t) for c in range(3))
        d.line([(3, 3 + row), (w-4, 3 + row)], fill=rgb)

    # Double gold border
    d.rectangle([(0, 0), (w-1, h-1)], outline=GOLD_M, width=1)
    d.rectangle([(3, 3), (w-4, h-4)], outline=GOLD_H, width=1)

    # Diamond corner ornaments
    s = 7
    for cx, cy in [(0, 0), (w-1, 0), (0, h-1), (w-1, h-1)]:
        d.polygon([(cx, cy-s), (cx+s, cy), (cx, cy+s), (cx-s, cy)],
                  fill=GOLD_M, outline=GOLD_H, width=1)
    return img


def gem(size=22, color="green"):
    img = Image.new("RGB", (size, size), BG)
    d   = ImageDraw.Draw(img)
    _gem(d, size//2, size//2, size//2 - 1, color)
    return img


# ── Sprite manifest ────────────────────────────────────────────────────────

SPRITES = [
    ("title_bar",     lambda: title_bar(800, 62)),
    ("panel_status",  lambda: panel(470, 320)),
    ("panel_log",     lambda: panel(800, 210)),
    ("btn_start",     lambda: button(186, 46, "green")),
    ("btn_stop",      lambda: button(186, 46, "red")),
    ("btn_pause",     lambda: button(186, 46, "gold")),
    ("btn_calibrate", lambda: button(186, 46, "blue")),
    ("gem_green",     lambda: gem(22, "green")),
    ("gem_red",       lambda: gem(22, "red")),
]


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    for name, fn in SPRITES:
        img  = fn()
        path = os.path.join(OUT_DIR, f"{name}.png")
        img.save(path, "PNG")
        print(f"  {name:<20s}  {img.width:>4}×{img.height:<4}  {path}")
    print(f"\n{len(SPRITES)} sprites saved to {OUT_DIR}")
    print("Run  python main.py  to launch.\n")


if __name__ == "__main__":
    main()
