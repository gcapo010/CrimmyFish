"""
gui.py — tkinter control panel for CrimmyFish.

The GUI runs on the main thread; the fishing loop runs in a daemon thread.
Communication uses threading.Event flags and a shared state dict so we never
block the UI.
"""

import os
import queue
import threading
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk
from typing import Callable, Optional

from PIL import Image, ImageDraw, ImageTk

import logger

# Colour palette
_BG     = "#1e1e2e"
_FG     = "#cdd6f4"
_ACCENT = "#89b4fa"
_GREEN  = "#a6e3a1"
_YELLOW = "#f9e2af"
_RED    = "#f38ba8"
_PANEL  = "#313244"

_LOGO_PATH = os.path.join(os.path.dirname(__file__), "assets", "logo.png")
_LOGO_SIZE = 768   # 75 % of the 1024 px native size
_BG_RGB    = (30, 30, 46)   # #1e1e2e as RGB tuple, must match _BG


def _load_logo() -> Optional[ImageTk.PhotoImage]:
    """
    Load assets/logo.png and blend it into the UI background.

    The artwork is a circle inscribed in a square canvas with a solid black
    background in the corners.  Rather than trying to detect background pixels
    by colour (which clips dark parts of the artwork), we draw a perfect
    ellipse that fills the image bounds and use it as the paste mask.  Anything
    outside the circle becomes the UI background colour; the artwork is
    untouched.
    """
    if not os.path.exists(_LOGO_PATH):
        return None
    try:
        img = Image.open(_LOGO_PATH).convert("RGB")

        # Circular mask: white inside the inscribed ellipse, black outside.
        mask = Image.new("L", img.size, 0)
        ImageDraw.Draw(mask).ellipse((0, 0, img.width - 1, img.height - 1), fill=255)

        canvas = Image.new("RGB", img.size, _BG_RGB)
        canvas.paste(img, mask=mask)

        canvas = canvas.resize((_LOGO_SIZE, _LOGO_SIZE), Image.LANCZOS)
        return ImageTk.PhotoImage(canvas)
    except Exception as exc:
        logger.warning("Could not load logo image: %s", exc)
        return None


class FishingGUI:
    """Main control window."""

    def __init__(
        self,
        on_start: Callable,
        on_stop: Callable,
        on_pause: Callable,
        on_calibrate: Callable,
    ):
        self._on_start     = on_start
        self._on_stop      = on_stop
        self._on_pause     = on_pause
        self._on_calibrate = on_calibrate

        self._root = tk.Tk()
        self._root.title("CrimmyFish — Auto Fishing Assistant")
        self._root.configure(bg=_BG)
        self._root.resizable(False, False)
        self._root.minsize(820, 700)
        self._root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._status_var    = tk.StringVar(value="Idle")
        self._confidence_var = tk.StringVar(value="—")
        self._loop_count_var = tk.StringVar(value="0")

        self._log_queue = logger.get_gui_queue()
        self._logo_img: Optional[ImageTk.PhotoImage] = None  # kept alive by reference

        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = self._root

        # Thin accent bar at the very top
        tk.Frame(root, bg=_ACCENT, height=4).pack(fill=tk.X)

        # ---- Header: logo image or text fallback ----
        self._logo_img = _load_logo()
        if self._logo_img:
            tk.Label(
                root,
                image=self._logo_img,
                bg=_BG,
                bd=0,
            ).pack(pady=(14, 0))
        else:
            # Fallback when assets/logo.png is absent
            tk.Label(
                root,
                text="CrimmyFish",
                bg=_BG,
                fg=_ACCENT,
                font=tkfont.Font(family="Helvetica", size=20, weight="bold"),
            ).pack(pady=(14, 2))
            tk.Label(
                root,
                text="Crimson Desert Auto Fishing Assistant",
                bg=_BG,
                fg=_FG,
                font=tkfont.Font(family="Helvetica", size=10),
            ).pack(pady=(0, 10))

        ttk.Separator(root, orient="horizontal").pack(fill=tk.X, padx=10, pady=(10, 0))

        # ---- Status panel ----
        status_frame = tk.Frame(root, bg=_PANEL)
        status_frame.pack(fill=tk.X, padx=10, pady=8)

        self._make_stat_row(status_frame, "Status:",      self._status_var,     row=0)
        self._make_stat_row(status_frame, "Confidence:",  self._confidence_var, row=1)
        self._make_stat_row(status_frame, "Fish caught:", self._loop_count_var, row=2)

        ttk.Separator(root, orient="horizontal").pack(fill=tk.X, padx=10)

        # ---- Control buttons ----
        btn_frame = tk.Frame(root, bg=_BG)
        btn_frame.pack(pady=8)

        self._make_btn(btn_frame, "Start (F6)",  _GREEN,  self._on_start,      col=0)
        self._make_btn(btn_frame, "Stop (F8)",   _RED,    self._on_stop,       col=1)
        self._pause_btn = self._make_btn(
            btn_frame, "Pause (F7)", _YELLOW, self._toggle_pause, col=2
        )
        self._make_btn(btn_frame, "Calibrate",   _ACCENT, self._on_calibrate,  col=3)

        # ---- Log viewer ----
        log_frame = tk.Frame(root, bg=_BG)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(4, 6))

        tk.Label(
            log_frame, text="Log", bg=_BG, fg=_ACCENT,
            font=tkfont.Font(family="Helvetica", size=9, weight="bold"),
        ).pack(anchor="w")

        self._log_text = tk.Text(
            log_frame,
            height=10,
            bg=_PANEL,
            fg=_FG,
            font=tkfont.Font(family="Courier", size=9),
            state=tk.DISABLED,
            wrap=tk.WORD,
            relief=tk.FLAT,
        )
        scroll = ttk.Scrollbar(log_frame, command=self._log_text.yview)
        self._log_text.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # ---- Hotkey hint ----
        tk.Label(
            root,
            text="F6 Start  |  F7 Pause  |  F8 Emergency Stop",
            bg=_BG,
            fg="#585b70",
            font=tkfont.Font(family="Helvetica", size=8),
        ).pack(pady=(2, 8))

        self._poll_log()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_stat_row(self, parent, label: str, var: tk.StringVar, row: int) -> None:
        tk.Label(
            parent, text=label, bg=_PANEL, fg="#7f849c",
            font=tkfont.Font(family="Helvetica", size=9),
        ).grid(row=row, column=0, sticky="w", padx=8, pady=2)
        tk.Label(
            parent, textvariable=var, bg=_PANEL, fg=_FG,
            font=tkfont.Font(family="Helvetica", size=9, weight="bold"),
        ).grid(row=row, column=1, sticky="w", padx=4, pady=2)

    def _make_btn(
        self, parent, text: str, color: str, cmd: Callable, col: int
    ) -> tk.Button:
        btn = tk.Button(
            parent,
            text=text,
            bg=color,
            fg=_BG,
            activebackground=color,
            activeforeground=_BG,
            font=tkfont.Font(family="Helvetica", size=10, weight="bold"),
            relief=tk.FLAT,
            padx=10,
            pady=6,
            cursor="hand2",
            command=cmd,
        )
        btn.grid(row=0, column=col, padx=4, pady=4)
        return btn

    # ------------------------------------------------------------------
    # Log polling
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Button / window handlers
    # ------------------------------------------------------------------

    def _toggle_pause(self) -> None:
        self._on_pause()

    def _on_close(self) -> None:
        self._on_stop()
        self._root.destroy()

    # ------------------------------------------------------------------
    # Public update methods (called from the loop thread via root.after)
    # ------------------------------------------------------------------

    def set_status(self, text: str) -> None:
        self._root.after(0, lambda: self._status_var.set(text))

    def set_confidence(self, conf: float) -> None:
        self._root.after(0, lambda: self._confidence_var.set(f"{conf:.3f}"))

    def set_loop_count(self, n: int) -> None:
        self._root.after(0, lambda: self._loop_count_var.set(str(n)))

    def set_paused(self, paused: bool) -> None:
        label = "Resume (F7)" if paused else "Pause (F7)"
        self._root.after(0, lambda: self._pause_btn.configure(text=label))

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        self._root.mainloop()
