"""Always-on-top suggestion overlay (tkinter).

Runs its own Tk mainloop on the main thread; the pipeline pushes updates via a
thread-safe queue. Small, frameless, corner-anchored so it never covers the
answer options. Suggest-only: it displays, it never clicks.
"""

from __future__ import annotations

import queue
import tkinter as tk
from dataclasses import dataclass


@dataclass
class OverlayState:
    status: str = "Ready"
    qnum: str = ""
    label: str = "-"
    answer: str = ""
    option_text: str = ""
    question_type: str = "open"
    confidence: float = 0.0
    elapsed_ms: int = 0
    source: str = ""


def _conf_color(conf: float) -> str:
    if conf >= 0.75:
        return "#22c55e"  # green
    if conf >= 0.5:
        return "#eab308"  # yellow
    return "#ef4444"      # red


class Overlay:
    def __init__(self, corner: str = "bottom-right", opacity: float = 0.92, font_size: int = 14):
        self._q: "queue.Queue[OverlayState | str]" = queue.Queue()
        self.corner = corner
        self.opacity = opacity
        self.font_size = font_size
        self.root: tk.Tk | None = None

    def _build(self) -> None:
        root = tk.Tk()
        self.root = root
        root.title("Dave")
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        try:
            root.attributes("-alpha", self.opacity)
        except Exception:
            pass
        # Windows kadang menurunkan topmost; naikkan prioritas window.
        try:
            root.wm_attributes("-topmost", 1)
        except Exception:
            pass

        bg = "#0f172a"
        root.configure(bg=bg)
        pad = 12
        width, height = 340, 150

        fs = self.font_size
        self._title = tk.Label(root, text="Dave", fg="#38bdf8", bg=bg,
                               font=("Segoe UI Semibold", fs + 1), anchor="w")
        self._title.pack(fill="x", padx=pad, pady=(pad, 0))

        self._status = tk.Label(root, text="Ready", fg="#94a3b8", bg=bg,
                                font=("Segoe UI", fs - 3), anchor="w")
        self._status.pack(fill="x", padx=pad)

        self._suggest = tk.Label(root, text="-", fg="#e2e8f0", bg=bg,
                                 font=("Segoe UI Semibold", fs + 8), anchor="w",
                                 wraplength=width - 2 * pad, justify="left")
        self._suggest.pack(fill="x", padx=pad, pady=(4, 0))

        self._meta = tk.Label(root, text="", fg="#64748b", bg=bg,
                              font=("Segoe UI", fs - 4), anchor="w")
        self._meta.pack(fill="x", padx=pad, pady=(0, pad))

        root.update_idletasks()
        height = root.winfo_reqheight()
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        margin = 24
        if "right" in self.corner:
            x = sw - width - margin
        else:
            x = margin
        if "bottom" in self.corner:
            y = sh - height - margin * 3
        else:
            y = margin
        root.geometry(f"{width}x{height}+{x}+{y}")

        # allow drag to reposition
        for w in (root, self._title):
            w.bind("<Button-1>", self._start_drag)
            w.bind("<B1-Motion>", self._on_drag)

        self._poll()

    def _start_drag(self, event) -> None:
        self._dx, self._dy = event.x, event.y

    def _on_drag(self, event) -> None:
        if self.root is None:
            return
        x = self.root.winfo_x() + event.x - getattr(self, "_dx", 0)
        y = self.root.winfo_y() + event.y - getattr(self, "_dy", 0)
        self.root.geometry(f"+{x}+{y}")

    def _poll(self) -> None:
        assert self.root is not None
        try:
            while True:
                item = self._q.get_nowait()
                if item == "__QUIT__":
                    self.root.destroy()
                    return
                if isinstance(item, OverlayState):
                    self._render(item)
        except queue.Empty:
            pass
        # Tetap di atas semua jendela (quiz, browser, dll.)
        if self.root is not None:
            try:
                self.root.attributes("-topmost", True)
                self.root.lift()
            except Exception:
                pass
        self.root.after(50, self._poll)

    def _render(self, s: OverlayState) -> None:
        self._status.config(text=s.status)
        ans = (s.answer or "-")[:120]

        if s.question_type == "ordering" and ans != "-":
            self._suggest.config(
                text=ans,
                fg=_conf_color(s.confidence),
                font=("Segoe UI Semibold", self.font_size + 4),
            )
            self._meta.config(
                text=f"{s.qnum}  |  urutan  |  {int(s.confidence*100)}%  |  {s.elapsed_ms}ms  |  {s.source}"
            )
        elif s.label and s.label != "-" and (s.option_text or ans != "-"):
            opt = s.option_text or ans
            self._suggest.config(
                text=f"{s.label}. {opt}",
                fg=_conf_color(s.confidence),
                font=("Segoe UI Semibold", self.font_size + 8),
            )
            self._meta.config(
                text=f"{s.qnum}  |  {int(s.confidence*100)}%  |  {s.elapsed_ms}ms  |  {s.source}"
            )
        elif ans and ans != "-":
            self._suggest.config(
                text=ans,
                fg=_conf_color(s.confidence),
                font=("Segoe UI Semibold", self.font_size + 8),
            )
            hint = "isian" if s.question_type == "open" else ""
            self._meta.config(
                text=f"{s.qnum}  |  {hint}  {int(s.confidence*100)}%  |  {s.elapsed_ms}ms  |  {s.source}".replace("  |  |", " |")
            )
        else:
            self._suggest.config(text=ans, fg="#e2e8f0")
            self._meta.config(text=s.qnum)

    # ---- public (thread-safe) ----------------------------------------

    def update(self, state: OverlayState) -> None:
        self._q.put(state)

    def stop(self) -> None:
        self._q.put("__QUIT__")

    def run(self) -> None:
        """Blocking: build UI and run the Tk mainloop (call on main thread)."""
        self._build()
        assert self.root is not None
        self.root.mainloop()
