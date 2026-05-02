"""
sprite_calibrator.py — Visually select sprite regions from the sheet.

Run this script once to set accurate crop coordinates:
    python sprite_calibrator.py

For each UI element it will:
  1. Open the sprite sheet in a window
  2. Tell you what to select (e.g. "drag over the title bar")
  3. Let you drag a rectangle with the mouse
  4. Save the coordinates to sprites/sprite_config.json
  5. Immediately slice and save that sprite to sprites/extracted/

When all elements are done, launch the app normally:
    python main.py
"""

import json
import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox

try:
    from PIL import Image, ImageTk
except ImportError:
    sys.exit("Pillow required. Run: pip install Pillow")

BASE         = os.path.dirname(__file__)
SPRITES_DIR  = os.path.join(BASE, "sprites")
EXTRACTED    = os.path.join(SPRITES_DIR, "extracted")
CONFIG_PATH  = os.path.join(SPRITES_DIR, "sprite_config.json")
SHEET_PATH   = os.path.join(SPRITES_DIR, "spritesheet.png")

# The order and descriptions shown to the user
ELEMENTS = [
    ("title_bar",     "TITLE BAR\nDrag over the full-width title banner at the top of the sheet.",
     800, 62),
    ("panel_status",  "STATUS PANEL\nDrag over the ornate panel on the RIGHT side of the header\n(the one next to the logo that shows Status / Confidence / Fish Caught).",
     470, 320),
    ("panel_log",     "LOG PANEL\nDrag over the large text/log area panel.",
     800, 210),
    ("btn_start",     "START BUTTON (green)\nDrag tightly around the green Start button.",
     186, 46),
    ("btn_stop",      "STOP BUTTON (red)\nDrag tightly around the red Stop button.",
     186, 46),
    ("btn_pause",     "PAUSE BUTTON (gold)\nDrag tightly around the gold/amber Pause button.",
     186, 46),
    ("btn_calibrate", "CALIBRATE BUTTON (blue)\nDrag tightly around the blue Calibrate button.",
     186, 46),
    ("gem_green",     "GREEN GEM\nDrag over the small green gem (usually top-left of title bar).",
     22, 22),
    ("gem_red",       "RED GEM\nDrag over the small red gem (usually top-right of title bar).",
     22, 22),
]


class SpriteCalibratorApp:
    def __init__(self):
        # Root MUST be created first — ImageTk.PhotoImage requires a live Tk instance
        self._root = tk.Tk()
        self._root.title("CrimmyFish — Sprite Calibrator")
        self._root.resizable(False, False)

        sheet_path = self._resolve_sheet()
        if sheet_path is None:
            self._root.destroy()
            return

        os.makedirs(EXTRACTED, exist_ok=True)

        if not os.path.exists(CONFIG_PATH):
            messagebox.showerror(
                "Config missing",
                f"sprite_config.json not found:\n{CONFIG_PATH}",
            )
            self._root.destroy()
            return

        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            self._cfg = json.load(fh)

        try:
            self._sheet_pil = Image.open(sheet_path).convert("RGBA")
        except Exception as exc:
            messagebox.showerror("Image error", f"Could not open sprite sheet:\n{exc}")
            self._root.destroy()
            return

        sw, sh = self._sheet_pil.size
        print(f"\nSprite sheet: {sw} x {sh} px")

        # Scale so the sheet fits on screen (max 1200x800 display area)
        max_w, max_h = 1200, 800
        self._scale = min(max_w / sw, max_h / sh, 1.0)
        disp_w = int(sw * self._scale)
        disp_h = int(sh * self._scale)

        # ImageTk.PhotoImage is safe here because tk.Tk() already exists above
        self._sheet_img = ImageTk.PhotoImage(
            self._sheet_pil.resize((disp_w, disp_h), Image.LANCZOS)
        )

        self._disp_w = disp_w
        self._disp_h = disp_h
        self._index  = 0
        self._start  = (0, 0)
        self._rect   = None

        self._build_ui(disp_w, disp_h)
        self._prompt_next()
        self._root.mainloop()

    def _resolve_sheet(self) -> "str | None":
        """Return a path to the sprite sheet, prompting the user if needed."""
        if os.path.exists(SHEET_PATH):
            return SHEET_PATH

        # Sheet not in the default location — ask the user to locate it
        messagebox.showinfo(
            "Sprite sheet not found",
            f"spritesheet.png was not found at:\n{SHEET_PATH}\n\n"
            "Click OK to browse for your sprite sheet, or Cancel to quit.",
        )
        chosen = filedialog.askopenfilename(
            title="Select your sprite sheet",
            initialdir=SPRITES_DIR,
            filetypes=[("PNG images", "*.png"), ("All files", "*.*")],
        )
        if not chosen:
            return None

        # Copy it to the expected location so future runs find it automatically
        import shutil
        os.makedirs(SPRITES_DIR, exist_ok=True)
        shutil.copy2(chosen, SHEET_PATH)
        print(f"Copied sprite sheet to {SHEET_PATH}")
        return SHEET_PATH

    def _build_ui(self, w, h):
        info_frame = tk.Frame(self._root, bg="#111", pady=6)
        info_frame.pack(fill=tk.X)

        self._info_var = tk.StringVar()
        tk.Label(
            info_frame,
            textvariable=self._info_var,
            bg="#111", fg="#c9a84c",
            font=("Georgia", 11),
            justify="center",
            wraplength=w - 20,
        ).pack(padx=10)

        self._skip_btn = tk.Button(
            info_frame, text="Skip this element",
            bg="#2a1f0e", fg="#c9a84c",
            relief=tk.FLAT, cursor="hand2",
            command=self._skip,
        )
        self._skip_btn.pack(pady=(4, 0))

        self._canvas = tk.Canvas(
            self._root, width=w, height=h,
            cursor="crosshair", highlightthickness=0,
        )
        self._canvas.pack()
        self._canvas.create_image(0, 0, anchor="nw", image=self._sheet_img)

        progress_frame = tk.Frame(self._root, bg="#111", pady=4)
        progress_frame.pack(fill=tk.X)
        self._progress_var = tk.StringVar()
        tk.Label(
            progress_frame,
            textvariable=self._progress_var,
            bg="#111", fg="#5a4a2a",
            font=("Courier", 9),
        ).pack()

        self._canvas.bind("<ButtonPress-1>",   self._press)
        self._canvas.bind("<B1-Motion>",        self._drag)
        self._canvas.bind("<ButtonRelease-1>",  self._release)

    def _prompt_next(self):
        if self._index >= len(ELEMENTS):
            self._finish()
            return
        name, desc, tw, th = ELEMENTS[self._index]
        self._info_var.set(
            f"[{self._index + 1}/{len(ELEMENTS)}]  {desc}\n"
            f"Target size: {tw} × {th} px  —  your selection will be resized to fit."
        )
        self._progress_var.set(
            "  ".join(
                f"{'✓' if i < self._index else ('→' if i == self._index else '○')} {ELEMENTS[i][0]}"
                for i in range(len(ELEMENTS))
            )
        )

    def _press(self, e):
        self._start = (e.x, e.y)
        if self._rect:
            self._canvas.delete(self._rect)
            self._rect = None

    def _drag(self, e):
        if self._rect:
            self._canvas.delete(self._rect)
        x0, y0 = self._start
        self._rect = self._canvas.create_rectangle(
            x0, y0, e.x, e.y,
            outline="#c9a84c", width=2,
            fill="#c9a84c", stipple="gray25",
        )

    def _release(self, e):
        x0, y0 = self._start
        x1, y1 = e.x, e.y

        # Normalise so top-left < bottom-right
        lx, ly = min(x0, x1), min(y0, y1)
        rx, ry = max(x0, x1), max(y0, y1)
        sel_w, sel_h = rx - lx, ry - ly

        if sel_w < 4 or sel_h < 4:
            return  # Too small — probably a mis-click, ignore

        # Convert display coords → original sheet coords
        s = self._scale
        ox  = int(lx / s)
        oy  = int(ly / s)
        ow  = int(sel_w / s)
        oh  = int(sel_h / s)

        name, _, tw, th = ELEMENTS[self._index]

        # Update config
        if name not in self._cfg["elements"]:
            self._cfg["elements"][name] = {}
        self._cfg["elements"][name].update(
            x=ox, y=oy, w=ow, h=oh,
            target_w=tw, target_h=th,
        )
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(self._cfg, fh, indent=2)

        # Immediately extract and save this sprite
        crop = self._sheet_pil.crop((ox, oy, ox + ow, oy + oh))
        if tw and th:
            crop = crop.resize((tw, th), Image.LANCZOS)
        # Flatten alpha onto dark background
        bg = Image.new("RGB", crop.size, (13, 11, 8))
        if crop.mode == "RGBA":
            bg.paste(crop, mask=crop.split()[3])
        else:
            bg.paste(crop.convert("RGB"))
        out = os.path.join(EXTRACTED, f"{name}.png")
        bg.save(out, "PNG")

        print(f"  ✓  {name:<20s}  source {ow}x{oh} → saved {tw}x{th}  ({out})")

        # Draw a green confirmation rectangle and advance
        if self._rect:
            self._canvas.delete(self._rect)
        self._canvas.create_rectangle(
            lx, ly, rx, ry,
            outline="#2db85a", width=2,
        )

        self._index += 1
        self._root.after(300, self._prompt_next)

    def _skip(self):
        name = ELEMENTS[self._index][0]
        print(f"  –  Skipped: {name}")
        self._index += 1
        self._prompt_next()

    def _finish(self):
        print("\nAll elements calibrated.")
        print("Run  python main.py  to launch with the sprite-based UI.\n")
        messagebox.showinfo(
            "Calibration complete",
            "All sprites saved to sprites/extracted/\n\nRun python main.py to launch.",
        )
        self._root.destroy()


if __name__ == "__main__":
    SpriteCalibratorApp()
