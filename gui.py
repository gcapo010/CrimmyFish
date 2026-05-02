"""
gui.py — RPG-themed control panel for CrimmyFish.

Recreates the fantasy UI aesthetic from the design mockup entirely in code:
  - Near-black stone background
  - Ornate gold double-line borders with diamond corner ornaments
  - Gem-coloured accents (green / red / gold / blue)
  - Georgia serif title font
  - Canvas-drawn OrnateButton widgets with hover/press states

No image assets required beyond assets/logo.png.
"""

import os
import queue
import tkinter as tk
from typing import Callable, Optional

from PIL import Image, ImageDraw, ImageTk

import logger

# ── Colour palette ────────────────────────────────────────────────────────
_WIN_BG      = "#0d0b08"   # near-black window / canvas fill
_PANEL_BG    = "#141008"   # panel interior
_ROW_BG      = "#1c1508"   # stat-row background
_GOLD_OUTER  = "#3d2c08"   # outermost shadow line
_GOLD_MID    = "#7a5c12"   # main border line
_GOLD_HI     = "#c9a84c"   # inner highlight / text accent
_TEXT        = "#e8d5a3"   # primary label text
_TEXT_DIM    = "#4a3a1a"   # dimmed / hint text
_LOG_BG      = "#0a0806"
_LOG_FG      = "#c8b888"

# Each gem: (dark fill, bright highlight)
_GEM = {
    "green": ("#0a2e14", "#1a8a40"),
    "red":   ("#2e0a0a", "#8a1a1a"),
    "gold":  ("#2e200a", "#b8900c"),
    "blue":  ("#0a142e", "#1a408a"),
}

_LOGO_PATH = os.path.join(os.path.dirname(__file__), "assets", "logo.png")
_LOGO_SIZE = 300          # logo rendered at this pixel size
_BG_RGB    = (13, 11, 8)  # _WIN_BG as an RGB tuple for PIL

_W = 800                  # content panel width (canvas units)


# ── Drawing helpers ────────────────────────────────────────────────────────

def _border(cv: tk.Canvas, x1: int, y1: int, x2: int, y2: int,
            fill: str = _PANEL_BG) -> None:
    """
    Ornate panel: solid fill, outer mid-gold line, inner bright-gold line,
    and a rotated-square (diamond) ornament at every corner.
    """
    cv.create_rectangle(x1, y1, x2, y2, fill=fill, outline="")
    cv.create_rectangle(x1, y1, x2, y2, outline=_GOLD_MID, width=1, fill="")
    o = 5
    cv.create_rectangle(x1+o, y1+o, x2-o, y2-o,
                        outline=_GOLD_HI, width=1, fill="")
    s = 9
    for cx, cy in [(x1, y1), (x2, y1), (x1, y2), (x2, y2)]:
        cv.create_polygon(
            cx, cy-s, cx+s, cy, cx, cy+s, cx-s, cy,
            fill=_GOLD_MID, outline=_GOLD_HI, width=1,
        )


def _gem(cv: tk.Canvas, cx: int, cy: int,
         r: int = 10, color: str = "green") -> None:
    """Circular gem with a crescent specular highlight."""
    base, hi = _GEM[color]
    cv.create_oval(cx-r, cy-r, cx+r, cy+r,
                   fill=base, outline=_GOLD_HI, width=1)
    cv.create_oval(cx-r//2, cy-r, cx+r//4, cy-r//4, fill=hi, outline="")


def _divider(cv: tk.Canvas, y: int, x1: int = 12, x2: int = None) -> None:
    """Horizontal gold divider line."""
    if x2 is None:
        x2 = _W - 12
    cv.create_line(x1, y, x2, y, fill=_GOLD_MID, width=1)


def _section_label(cv: tk.Canvas, text: str, x: int, y: int,
                   anchor: str = "center") -> None:
    cv.create_text(x, y, text=text, fill=_GOLD_HI, anchor=anchor,
                   font=("Georgia", 9, "bold italic"))


# ── Logo loader ────────────────────────────────────────────────────────────

def _load_logo() -> Optional[ImageTk.PhotoImage]:
    """
    Load assets/logo.png.  The artwork is a circle on a square black canvas;
    we apply a geometric ellipse mask to replace the corners with _WIN_BG so
    the logo blends into the dark window without any colour analysis.
    """
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


# ── OrnateButton ───────────────────────────────────────────────────────────

class OrnateButton(tk.Canvas):
    """
    Canvas-drawn button styled to match the RPG mockup.
    Background colour comes from the gem palette; border is gold double-line
    with diamond corner accents.  Hover brightens the fill; press flashes gold.
    """
    BW, BH = 186, 46

    def __init__(self, parent: tk.Widget, text: str, gem_color: str,
                 command: Callable = None, **kw):
        super().__init__(parent, width=self.BW, height=self.BH,
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

    def _redraw(self, pressed: bool = False) -> None:
        self.delete("all")
        w, h       = self.BW, self.BH
        base, hi   = _GEM[self._color]
        fill       = _GOLD_HI if pressed else (hi if self._hovered else base)
        text_color = _WIN_BG  if (pressed or self._hovered) else _TEXT

        self.create_rectangle(2, 2, w-2, h-2, fill=fill, outline="")
        self.create_rectangle(0, 0, w-1, h-1, outline=_GOLD_MID, width=1, fill="")
        self.create_rectangle(3, 3, w-4, h-4, outline=_GOLD_HI,  width=1, fill="")
        s = 7
        for cx, cy in [(0, 0), (w-1, 0), (0, h-1), (w-1, h-1)]:
            self.create_polygon(
                cx, cy-s, cx+s, cy, cx, cy+s, cx-s, cy,
                fill=_GOLD_MID, outline=_GOLD_HI, width=1,
            )
        self.create_text(w//2, h//2, text=self._text,
                         fill=text_color, font=("Georgia", 11, "bold"))

    def _set_hover(self, v: bool) -> None:
        self._hovered = v
        self._redraw()

    def _press(self, _):
        self._redraw(pressed=True)

    def _release(self, _):
        self._redraw()
        if self._cmd:
            self._cmd()

    def set_text(self, text: str) -> None:
        self._text = text
        self._redraw()


# ── Main window ────────────────────────────────────────────────────────────

class FishingGUI:
    _PX = 14   # horizontal padding between window edge and content panels

    def __init__(self, on_start: Callable, on_stop: Callable,
                 on_pause: Callable, on_calibrate: Callable):
        self._on_start     = on_start
        self._on_stop      = on_stop
        self._on_pause     = on_pause
        self._on_calibrate = on_calibrate

        self._status_var = tk.StringVar(value="Idle")
        self._conf_var   = tk.StringVar(value="—")
        self._count_var  = tk.StringVar(value="0")
        self._log_queue  = logger.get_gui_queue()

        self._logo_img:  Optional[ImageTk.PhotoImage] = None
        self._pause_btn: Optional[OrnateButton]        = None

        self._root = tk.Tk()
        self._root.title("CrimmyFish — Auto Fishing Assistant")
        self._root.configure(bg=_WIN_BG)
        self._root.resizable(False, False)
        self._root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()

    # ── Construction ──────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        px = self._PX
        self._build_title(px)
        self._build_header(px)
        self._build_buttons()
        self._build_log(px)
        self._build_hint(px)
        self._poll_log()

    def _build_title(self, px: int) -> None:
        """Ornate title bar with green gem left, red gem right."""
        h  = 62
        cv = tk.Canvas(self._root, width=_W, height=h,
                       bg=_WIN_BG, highlightthickness=0)
        cv.pack(padx=px, pady=(14, 0))
        _border(cv, 0, 0, _W-1, h-1, fill=_PANEL_BG)
        _gem(cv, 24,    h//2, r=11, color="green")
        _gem(cv, _W-24, h//2, r=11, color="red")
        cv.create_text(_W//2, h//2,
                       text="CrimmyFish  —  Auto Fishing Assistant",
                       fill=_GOLD_HI, font=("Georgia", 15, "bold"))

    def _build_header(self, px: int) -> None:
        """
        Two-column header: circular logo on the left, status panel on the right.
        A gold ring is drawn around the logo canvas to echo the mockup's frame.
        """
        hdr = tk.Frame(self._root, bg=_WIN_BG)
        hdr.pack(padx=px, pady=(10, 0))

        # ---- Logo ----
        pad     = 20                      # extra canvas space around the circle
        lc_size = _LOGO_SIZE + pad
        logo_cv = tk.Canvas(hdr, width=lc_size, height=lc_size,
                            bg=_WIN_BG, highlightthickness=0)
        logo_cv.grid(row=0, column=0, padx=(0, 10))

        self._logo_img = _load_logo()
        c = lc_size // 2
        r = _LOGO_SIZE // 2
        if self._logo_img:
            logo_cv.create_image(c, c, image=self._logo_img)
        else:
            logo_cv.create_text(c, c, text="CrimmyFish",
                                fill=_GOLD_HI, font=("Georgia", 18, "bold"))

        # Concentric gold rings around the circular logo
        logo_cv.create_oval(c-r-6, c-r-6, c+r+6, c+r+6,
                            outline=_GOLD_MID, width=2)
        logo_cv.create_oval(c-r-2, c-r-2, c+r+2, c+r+2,
                            outline=_GOLD_HI, width=1)

        # ---- Status panel ----
        sw = _W - lc_size - 10
        sh = lc_size
        stat_cv = tk.Canvas(hdr, width=sw, height=sh,
                            bg=_WIN_BG, highlightthickness=0)
        stat_cv.grid(row=0, column=1, sticky="nsew")
        _border(stat_cv, 0, 0, sw-1, sh-1)
        _section_label(stat_cv, "◆  FISHING STATUS  ◆", sw//2, 18)
        stat_cv.create_line(14, 30, sw-14, 30, fill=_GOLD_MID)

        # Embed a Frame for the stat rows
        inner = tk.Frame(stat_cv, bg=_PANEL_BG)
        stat_cv.create_window(8, 36, anchor="nw", window=inner,
                              width=sw-16, height=sh-44)

        rows = [
            ("Status",      self._status_var),
            ("Confidence",  self._conf_var),
            ("Fish Caught", self._count_var),
        ]
        for label, var in rows:
            row = tk.Frame(inner, bg=_ROW_BG)
            row.pack(fill=tk.X, padx=4, pady=7)
            # Thin gold top-border accent
            tk.Frame(row, bg=_GOLD_OUTER, height=1).pack(fill=tk.X)
            body = tk.Frame(row, bg=_ROW_BG)
            body.pack(fill=tk.X, padx=12, pady=9)
            tk.Label(body, text=label + ":", bg=_ROW_BG,
                     fg=_TEXT_DIM, font=("Georgia", 9)).pack(side=tk.LEFT)
            tk.Label(body, textvariable=var, bg=_ROW_BG,
                     fg=_GOLD_HI, font=("Georgia", 13, "bold")).pack(side=tk.RIGHT)

    def _build_buttons(self) -> None:
        """Four OrnateButton widgets in a centred row."""
        frm = tk.Frame(self._root, bg=_WIN_BG)
        frm.pack(pady=(10, 0))
        defs = [
            ("Start (F6)",  "green", self._on_start),
            ("Stop (F8)",   "red",   self._on_stop),
            ("Pause (F7)",  "gold",  self._toggle_pause),
            ("Calibrate",   "blue",  self._on_calibrate),
        ]
        for i, (label, color, cmd) in enumerate(defs):
            btn = OrnateButton(frm, label, color, command=cmd)
            btn.grid(row=0, column=i, padx=6)
            if label == "Pause (F7)":
                self._pause_btn = btn

    def _build_log(self, px: int) -> None:
        """Ornate log panel containing a scrollable Text widget."""
        log_h = 210
        cv = tk.Canvas(self._root, width=_W, height=log_h,
                       bg=_WIN_BG, highlightthickness=0)
        cv.pack(padx=px, pady=(10, 0))
        _border(cv, 0, 0, _W-1, log_h-1)
        _section_label(cv, "◆  LOG", 18, 16, anchor="w")
        cv.create_line(8, 28, _W-8, 28, fill=_GOLD_MID)

        self._log_text = tk.Text(
            cv,
            bg=_LOG_BG, fg=_LOG_FG,
            font=("Courier New", 8),
            state=tk.DISABLED,
            wrap=tk.WORD,
            relief=tk.FLAT, bd=0,
        )
        sb = tk.Scrollbar(cv, orient="vertical",
                          command=self._log_text.yview)
        self._log_text.configure(yscrollcommand=sb.set)

        # Place text and scrollbar inside the canvas, respecting the inner border
        text_w = _W - 36
        cv.create_window(8,        32, anchor="nw",
                         window=self._log_text, width=text_w,  height=log_h-42)
        cv.create_window(_W-20,    32, anchor="nw",
                         window=sb,              width=14,       height=log_h-42)

    def _build_hint(self, px: int) -> None:
        """Bottom hint bar with a gold divider line."""
        cv = tk.Canvas(self._root, width=_W, height=28,
                       bg=_WIN_BG, highlightthickness=0)
        cv.pack(padx=px, pady=(8, 14))
        cv.create_line(0, 0, _W, 0, fill=_GOLD_OUTER)
        cv.create_text(_W//2, 15,
                       text="F6  Start    ◆    F7  Pause    ◆    F8  Emergency Stop",
                       fill=_TEXT_DIM, font=("Georgia", 8))

    # ── Log polling ────────────────────────────────────────────────────────

    def _poll_log(self) -> None:
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

    def _toggle_pause(self) -> None:
        self._on_pause()

    def _on_close(self) -> None:
        self._on_stop()
        self._root.destroy()

    # ── Public updates (thread-safe via root.after) ────────────────────────

    def set_status(self, text: str) -> None:
        self._root.after(0, lambda: self._status_var.set(text))

    def set_confidence(self, conf: float) -> None:
        self._root.after(0, lambda: self._conf_var.set(f"{conf:.3f}"))

    def set_loop_count(self, n: int) -> None:
        self._root.after(0, lambda: self._count_var.set(str(n)))

    def set_paused(self, paused: bool) -> None:
        label = "Resume (F7)" if paused else "Pause (F7)"
        if self._pause_btn:
            self._root.after(0, lambda: self._pause_btn.set_text(label))

    # ── Entry point ────────────────────────────────────────────────────────

    def run(self) -> None:
        self._root.mainloop()
