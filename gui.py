"""
gui.py — RPG-themed control panel for CrimmyFish.

Rendering mode is chosen automatically at startup:
  • SPRITE mode  — sprites/extracted/ contains all required PNGs
                   (run sprite_slicer.py first)
  • CANVAS mode  — fallback drawn entirely with tkinter Canvas

Both modes use the same fixed window size and layout so the window cannot
be resized and sprites never need to stretch.
"""

import os
import queue
import tkinter as tk
from typing import Callable, Optional

from PIL import Image, ImageDraw, ImageEnhance, ImageTk

import logger

# ── Fixed layout dimensions ────────────────────────────────────────────────
# These must match the target_w / target_h values in sprite_config.json.

_W          = 800   # content panel width
_PX         = 14    # left/right padding from window edge to panels
_TITLE_H    = 62
_LOGO_SIZE  = 300
_LOGO_PAD   = 20    # extra canvas space around the circle
_LOGO_CV    = _LOGO_SIZE + _LOGO_PAD          # logo canvas side length
_STAT_W     = _W - _LOGO_CV - 10             # status panel width  (470)
_STAT_H     = _LOGO_CV                        # status panel height (320)
_BTN_W      = 186
_BTN_H      = 46
_LOG_H      = 210
_HINT_H     = 28

# ── Colour palette (used in canvas / fallback mode and for text overlay) ──
_WIN_BG     = "#0d0b08"
_PANEL_BG   = "#141008"
_ROW_BG     = "#1c1508"
_GOLD_OUTER = "#3d2c08"
_GOLD_MID   = "#7a5c12"
_GOLD_HI    = "#c9a84c"
_TEXT       = "#e8d5a3"
_TEXT_DIM   = "#4a3a1a"
_LOG_BG     = "#0a0806"
_LOG_FG     = "#c8b888"
_BG_RGB     = (13, 11, 8)

_GEM = {
    "green": ("#0a2e14", "#1a8a40"),
    "red":   ("#2e0a0a", "#8a1a1a"),
    "gold":  ("#2e200a", "#b8900c"),
    "blue":  ("#0a142e", "#1a408a"),
}

_LOGO_PATH      = os.path.join(os.path.dirname(__file__), "assets",  "logo.png")
_EXTRACTED_DIR  = os.path.join(os.path.dirname(__file__), "sprites", "extracted")

_REQUIRED_SPRITES = [
    "title_bar", "panel_status", "panel_log",
    "btn_start", "btn_stop", "btn_pause", "btn_calibrate",
]

# Button sprite name → gem colour (used as fallback tint if sprites absent)
_BTN_DEFS = [
    ("Start (F6)",  "btn_start",     "green"),
    ("Stop (F8)",   "btn_stop",      "red"),
    ("Pause (F7)",  "btn_pause",     "gold"),
    ("Calibrate",   "btn_calibrate", "blue"),
]


# ── Canvas-mode drawing helpers ────────────────────────────────────────────

def _border(cv: tk.Canvas, x1, y1, x2, y2, fill=_PANEL_BG):
    cv.create_rectangle(x1, y1, x2, y2, fill=fill, outline="")
    cv.create_rectangle(x1, y1, x2, y2, outline=_GOLD_MID, width=1, fill="")
    o = 5
    cv.create_rectangle(x1+o, y1+o, x2-o, y2-o, outline=_GOLD_HI, width=1, fill="")
    s = 9
    for cx, cy in [(x1, y1), (x2, y1), (x1, y2), (x2, y2)]:
        cv.create_polygon(cx, cy-s, cx+s, cy, cx, cy+s, cx-s, cy,
                          fill=_GOLD_MID, outline=_GOLD_HI, width=1)


def _gem(cv, cx, cy, r=10, color="green"):
    base, hi = _GEM[color]
    cv.create_oval(cx-r, cy-r, cx+r, cy+r, fill=base, outline=_GOLD_HI, width=1)
    cv.create_oval(cx-r//2, cy-r, cx+r//4, cy-r//4, fill=hi, outline="")


# ── Logo loader ────────────────────────────────────────────────────────────

def _load_logo() -> Optional[ImageTk.PhotoImage]:
    if not os.path.exists(_LOGO_PATH):
        return None
    try:
        img  = Image.open(_LOGO_PATH).convert("RGB")
        mask = Image.new("L", img.size, 0)
        ImageDraw.Draw(mask).ellipse((0, 0, img.width-1, img.height-1), fill=255)
        bg   = Image.new("RGB", img.size, _BG_RGB)
        bg.paste(img, mask=mask)
        bg   = bg.resize((_LOGO_SIZE, _LOGO_SIZE), Image.LANCZOS)
        return ImageTk.PhotoImage(bg)
    except Exception as exc:
        logger.warning("Could not load logo: %s", exc)
        return None


# ── Sprite loader ──────────────────────────────────────────────────────────

def _flatten(img: Image.Image) -> Image.Image:
    """Composite RGBA onto _WIN_BG so no alpha reaches tkinter."""
    bg = Image.new("RGB", img.size, _BG_RGB)
    if img.mode == "RGBA":
        bg.paste(img, mask=img.split()[3])
    else:
        bg.paste(img.convert("RGB"))
    return bg


class SpriteLoader:
    """
    Loads extracted sprite PNGs from sprites/extracted/.
    Pre-generates brightened (hover) and darkened (pressed) button variants
    using PIL ImageEnhance so OrnateButton can animate without extra assets.

    self.ok  → True if all required sprites loaded successfully.
    """

    def __init__(self):
        self._ph:  dict[str, ImageTk.PhotoImage] = {}
        self._pil: dict[str, Image.Image]        = {}
        self.ok = self._load()
        if self.ok:
            logger.info("Sprite mode: all sprites loaded from sprites/extracted/")
        else:
            logger.info("Canvas mode: sprites not found, using canvas-drawn UI")

    def _load(self) -> bool:
        if not os.path.isdir(_EXTRACTED_DIR):
            return False
        for name in _REQUIRED_SPRITES:
            path = os.path.join(_EXTRACTED_DIR, f"{name}.png")
            if not os.path.exists(path):
                logger.warning("Missing sprite: %s — run sprite_slicer.py", name)
                return False
            try:
                pil = _flatten(Image.open(path))
                self._pil[name] = pil
                self._ph[name]  = ImageTk.PhotoImage(pil)
            except Exception as exc:
                logger.warning("Failed to load sprite %s: %s", name, exc)
                return False

        # Pre-generate hover (brighter) and pressed (darker) button variants
        for _, sprite_name, _ in _BTN_DEFS:
            base = self._pil[sprite_name]
            hover   = ImageEnhance.Brightness(base).enhance(1.45)
            pressed = ImageEnhance.Brightness(base).enhance(0.65)
            self._ph[f"{sprite_name}_hover"]   = ImageTk.PhotoImage(hover)
            self._ph[f"{sprite_name}_pressed"] = ImageTk.PhotoImage(pressed)

        return True

    def img(self, name: str) -> Optional[ImageTk.PhotoImage]:
        return self._ph.get(name)


# ── Button widgets ─────────────────────────────────────────────────────────

class SpriteButton(tk.Canvas):
    """Button backed by a sprite image. Hover/press use pre-brightened variants."""

    def __init__(self, parent, text, sprite_name, sprites: SpriteLoader,
                 command=None, **kw):
        super().__init__(parent, width=_BTN_W, height=_BTN_H,
                         bg=_WIN_BG, highlightthickness=0, **kw)
        self._text        = text
        self._sprite_name = sprite_name
        self._sprites     = sprites
        self._cmd         = command
        self._hovered     = False
        self._redraw()
        self.bind("<ButtonPress-1>",   self._press)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Enter>",  lambda _: self._set_hover(True))
        self.bind("<Leave>",  lambda _: self._set_hover(False))
        self.configure(cursor="hand2")

    def _redraw(self, pressed=False):
        self.delete("all")
        key = (f"{self._sprite_name}_pressed" if pressed
               else f"{self._sprite_name}_hover" if self._hovered
               else self._sprite_name)
        img = self._sprites.img(key)
        if img:
            self.create_image(0, 0, anchor="nw", image=img)
        # Text colour: dark when on bright hover/press, light otherwise
        tc = _WIN_BG if (pressed or self._hovered) else _TEXT
        self.create_text(_BTN_W//2, _BTN_H//2, text=self._text,
                         fill=tc, font=("Georgia", 11, "bold"))

    def _set_hover(self, v):
        self._hovered = v
        self._redraw()

    def _press(self, _):
        self._redraw(pressed=True)

    def _release(self, _):
        self._redraw()
        if self._cmd:
            self._cmd()

    def set_text(self, text):
        self._text = text
        self._redraw()


class OrnateButton(tk.Canvas):
    """Canvas-drawn fallback button used when sprites are not available."""

    def __init__(self, parent, text, gem_color, command=None, **kw):
        super().__init__(parent, width=_BTN_W, height=_BTN_H,
                         bg=_WIN_BG, highlightthickness=0, **kw)
        self._text    = text
        self._color   = gem_color
        self._cmd     = command
        self._hovered = False
        self._redraw()
        self.bind("<ButtonPress-1>",   self._press)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Enter>",  lambda _: self._set_hover(True))
        self.bind("<Leave>",  lambda _: self._set_hover(False))
        self.configure(cursor="hand2")

    def _redraw(self, pressed=False):
        self.delete("all")
        w, h       = _BTN_W, _BTN_H
        base, hi   = _GEM[self._color]
        fill       = _GOLD_HI if pressed else (hi if self._hovered else base)
        text_color = _WIN_BG  if (pressed or self._hovered) else _TEXT
        self.create_rectangle(2, 2, w-2, h-2, fill=fill, outline="")
        self.create_rectangle(0, 0, w-1, h-1, outline=_GOLD_MID, width=1, fill="")
        self.create_rectangle(3, 3, w-4, h-4, outline=_GOLD_HI,  width=1, fill="")
        s = 7
        for cx, cy in [(0, 0), (w-1, 0), (0, h-1), (w-1, h-1)]:
            self.create_polygon(cx, cy-s, cx+s, cy, cx, cy+s, cx-s, cy,
                                fill=_GOLD_MID, outline=_GOLD_HI, width=1)
        self.create_text(w//2, h//2, text=self._text,
                         fill=text_color, font=("Georgia", 11, "bold"))

    def _set_hover(self, v):
        self._hovered = v
        self._redraw()

    def _press(self, _):
        self._redraw(pressed=True)

    def _release(self, _):
        self._redraw()
        if self._cmd:
            self._cmd()

    def set_text(self, text):
        self._text = text
        self._redraw()


# ── Main window ────────────────────────────────────────────────────────────

class FishingGUI:

    def __init__(self, on_start: Callable, on_stop: Callable,
                 on_pause: Callable, on_calibrate: Callable):
        self._on_start     = on_start
        self._on_stop      = on_stop
        self._on_pause     = on_pause
        self._on_calibrate = on_calibrate

        # Root window first — required before creating StringVars (Python 3.14+)
        self._root = tk.Tk()
        self._root.title("CrimmyFish — Auto Fishing Assistant")
        self._root.configure(bg=_WIN_BG)
        self._root.resizable(False, False)
        self._root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._status_var = tk.StringVar(value="Idle")
        self._conf_var   = tk.StringVar(value="—")
        self._count_var  = tk.StringVar(value="0")
        self._log_queue  = logger.get_gui_queue()

        self._logo_img:  Optional[ImageTk.PhotoImage] = None
        self._pause_btn: Optional[tk.Canvas]           = None
        self._pause_sprite_name: str                   = "btn_pause"

        self._build_ui()

    # ── Build ──────────────────────────────────────────────────────────────

    def _build_ui(self):
        self._sprites = SpriteLoader()
        sp = self._sprites

        self._build_title(sp)
        self._build_header(sp)
        self._build_buttons(sp)
        self._build_log(sp)
        self._build_hint()
        self._poll_log()

    # ── Title bar ──────────────────────────────────────────────────────────

    def _build_title(self, sp: SpriteLoader):
        cv = tk.Canvas(self._root, width=_W, height=_TITLE_H,
                       bg=_WIN_BG, highlightthickness=0)
        cv.pack(padx=_PX, pady=(14, 0))

        if sp.ok:
            cv.create_image(0, 0, anchor="nw", image=sp.img("title_bar"))
        else:
            _border(cv, 0, 0, _W-1, _TITLE_H-1, fill=_PANEL_BG)
            _gem(cv, 24,    _TITLE_H//2, r=11, color="green")
            _gem(cv, _W-24, _TITLE_H//2, r=11, color="red")

        cv.create_text(_W//2, _TITLE_H//2,
                       text="CrimmyFish  —  Auto Fishing Assistant",
                       fill=_GOLD_HI, font=("Georgia", 15, "bold"))

    # ── Header (logo + status) ─────────────────────────────────────────────

    def _build_header(self, sp: SpriteLoader):
        hdr = tk.Frame(self._root, bg=_WIN_BG)
        hdr.pack(padx=_PX, pady=(10, 0))

        # Logo canvas
        logo_cv = tk.Canvas(hdr, width=_LOGO_CV, height=_LOGO_CV,
                            bg=_WIN_BG, highlightthickness=0)
        logo_cv.grid(row=0, column=0, padx=(0, 10))

        self._logo_img = _load_logo()
        c = _LOGO_CV // 2
        r = _LOGO_SIZE // 2
        if self._logo_img:
            logo_cv.create_image(c, c, image=self._logo_img)
        else:
            logo_cv.create_text(c, c, text="CrimmyFish",
                                fill=_GOLD_HI, font=("Georgia", 18, "bold"))
        # Gold ring around logo circle
        logo_cv.create_oval(c-r-6, c-r-6, c+r+6, c+r+6,
                            outline=_GOLD_MID, width=2)
        logo_cv.create_oval(c-r-2, c-r-2, c+r+2, c+r+2,
                            outline=_GOLD_HI, width=1)

        # Status panel canvas
        stat_cv = tk.Canvas(hdr, width=_STAT_W, height=_STAT_H,
                            bg=_WIN_BG, highlightthickness=0)
        stat_cv.grid(row=0, column=1, sticky="nsew")

        if sp.ok:
            stat_cv.create_image(0, 0, anchor="nw", image=sp.img("panel_status"))
        else:
            _border(stat_cv, 0, 0, _STAT_W-1, _STAT_H-1)
            stat_cv.create_text(_STAT_W//2, 18, text="◆  FISHING STATUS  ◆",
                                fill=_GOLD_HI, font=("Georgia", 9, "bold italic"))
            stat_cv.create_line(14, 30, _STAT_W-14, 30, fill=_GOLD_MID)

        # Stat rows (embedded Frame works over both sprite and canvas backgrounds)
        inner = tk.Frame(stat_cv, bg=_PANEL_BG)
        # In sprite mode the panel sprite already has a title; start content lower
        top_offset = 38 if sp.ok else 36
        stat_cv.create_window(8, top_offset, anchor="nw", window=inner,
                              width=_STAT_W-16, height=_STAT_H-top_offset-8)

        for label, var in [("Status",      self._status_var),
                            ("Confidence",  self._conf_var),
                            ("Fish Caught", self._count_var)]:
            row = tk.Frame(inner, bg=_ROW_BG)
            row.pack(fill=tk.X, padx=4, pady=7)
            tk.Frame(row, bg=_GOLD_OUTER, height=1).pack(fill=tk.X)
            body = tk.Frame(row, bg=_ROW_BG)
            body.pack(fill=tk.X, padx=12, pady=9)
            tk.Label(body, text=label + ":", bg=_ROW_BG,
                     fg=_TEXT_DIM, font=("Georgia", 9)).pack(side=tk.LEFT)
            tk.Label(body, textvariable=var, bg=_ROW_BG,
                     fg=_GOLD_HI, font=("Georgia", 13, "bold")).pack(side=tk.RIGHT)

    # ── Buttons ────────────────────────────────────────────────────────────

    def _build_buttons(self, sp: SpriteLoader):
        frm = tk.Frame(self._root, bg=_WIN_BG)
        frm.pack(pady=(10, 0))

        cmds = [self._on_start, self._on_stop,
                self._toggle_pause, self._on_calibrate]

        for i, ((label, sprite_name, gem), cmd) in enumerate(zip(_BTN_DEFS, cmds)):
            if sp.ok:
                btn = SpriteButton(frm, label, sprite_name, sp, command=cmd)
            else:
                btn = OrnateButton(frm, label, gem, command=cmd)
            btn.grid(row=0, column=i, padx=6)
            if sprite_name == "btn_pause":
                self._pause_btn = btn
                self._pause_sprite_name = sprite_name

    # ── Log panel ──────────────────────────────────────────────────────────

    def _build_log(self, sp: SpriteLoader):
        cv = tk.Canvas(self._root, width=_W, height=_LOG_H,
                       bg=_WIN_BG, highlightthickness=0)
        cv.pack(padx=_PX, pady=(10, 0))

        if sp.ok:
            cv.create_image(0, 0, anchor="nw", image=sp.img("panel_log"))
        else:
            _border(cv, 0, 0, _W-1, _LOG_H-1)
            cv.create_text(18, 16, text="◆  LOG", fill=_GOLD_HI,
                           font=("Georgia", 9, "bold italic"), anchor="w")
            cv.create_line(8, 28, _W-8, 28, fill=_GOLD_MID)

        self._log_text = tk.Text(
            cv, bg=_LOG_BG, fg=_LOG_FG,
            font=("Courier New", 8),
            state=tk.DISABLED, wrap=tk.WORD,
            relief=tk.FLAT, bd=0,
        )
        sb = tk.Scrollbar(cv, orient="vertical", command=self._log_text.yview)
        self._log_text.configure(yscrollcommand=sb.set)
        cv.create_window(8,     32, anchor="nw",
                         window=self._log_text, width=_W-36, height=_LOG_H-42)
        cv.create_window(_W-20, 32, anchor="nw",
                         window=sb, width=14, height=_LOG_H-42)

    # ── Hint bar ───────────────────────────────────────────────────────────

    def _build_hint(self):
        cv = tk.Canvas(self._root, width=_W, height=_HINT_H,
                       bg=_WIN_BG, highlightthickness=0)
        cv.pack(padx=_PX, pady=(8, 14))
        cv.create_line(0, 0, _W, 0, fill=_GOLD_OUTER)
        cv.create_text(_W//2, 15,
                       text="F6  Start    ◆    F7  Pause    ◆    F8  Emergency Stop",
                       fill=_TEXT_DIM, font=("Georgia", 8))

    # ── Log polling ────────────────────────────────────────────────────────

    def _poll_log(self):
        try:
            while True:
                msg = self._log_queue.get_nowait()
                self._log_text.configure(state=tk.NORMAL)
                self._log_text.insert(tk.END, msg + "\n")
                self._log_text.see(tk.END)
                self._log_text.configure(state=tk.DISABLED)
        except queue.Empty:
            pass
        self._root.after(100, self._poll_log)

    # ── Handlers ──────────────────────────────────────────────────────────

    def _toggle_pause(self):
        self._on_pause()

    def _on_close(self):
        self._on_stop()
        self._root.destroy()

    # ── Public updates (thread-safe) ───────────────────────────────────────

    def set_status(self, text: str):
        self._root.after(0, lambda: self._status_var.set(text))

    def set_confidence(self, conf: float):
        self._root.after(0, lambda: self._conf_var.set(f"{conf:.3f}"))

    def set_loop_count(self, n: int):
        self._root.after(0, lambda: self._count_var.set(str(n)))

    def set_paused(self, paused: bool):
        label = "Resume (F7)" if paused else "Pause (F7)"
        if self._pause_btn:
            self._root.after(0, lambda: self._pause_btn.set_text(label))

    # ── Entry point ────────────────────────────────────────────────────────

    def run(self):
        self._root.mainloop()
