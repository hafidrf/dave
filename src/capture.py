"""Screen capture: grab tight regions (question + options) as numpy arrays.

Uses mss for fast grabs (~1-5 ms per region). Kept stateless so the pipeline
can call it every poll tick cheaply.
"""

from __future__ import annotations

import threading
from typing import Any

import mss
import numpy as np


class Capturer:
    def __init__(self, regions: dict[str, Any] | None = None):
        self.regions = regions or {}
        self.monitor_index = int(self.regions.get("monitor", 1))
        # mss uses thread-local GDI on Windows; keep one instance per thread so
        # capture works whether called from the main or a worker thread.
        self._local = threading.local()

    @property
    def _sct(self) -> mss.base.MSSBase:
        sct = getattr(self._local, "sct", None)
        if sct is None:
            sct = mss.mss()
            self._local.sct = sct
        return sct

    def _grab(self, box: dict[str, int]) -> np.ndarray:
        area = {
            "left": int(box["left"]),
            "top": int(box["top"]),
            "width": int(box["width"]),
            "height": int(box["height"]),
        }
        shot = self._sct.grab(area)
        # BGRA -> drop alpha, keep BGR (RapidOCR accepts BGR ndarray)
        arr = np.asarray(shot)[:, :, :3]
        return arr

    def grab_question(self) -> np.ndarray:
        return self._grab(self.regions["question"])

    def grab_options(self) -> list[tuple[str, np.ndarray]]:
        # Mode baru: satu panel berisi semua tombol (Quiz.com 2x2).
        panel = self.regions.get("options_panel")
        if panel:
            return [("__panel__", self._grab(panel))]

        out: list[tuple[str, np.ndarray]] = []
        for opt in self.regions.get("options", []):
            out.append((opt["label"], self._grab(opt)))
        return out

    def grab_all(self) -> tuple[np.ndarray, list[tuple[str, np.ndarray]]]:
        return self.grab_question(), self.grab_options()

    def grab_monitor(self, index: int | None = None) -> np.ndarray:
        """Grab the whole monitor (auto mode, no calibration).

        mss.monitors[0] is the virtual union of all screens; [1] is the primary.
        """
        mons = self._sct.monitors
        idx = self.monitor_index if index is None else index
        if idx >= len(mons):
            idx = 1 if len(mons) > 1 else 0
        shot = self._sct.grab(mons[idx])
        return np.asarray(shot)[:, :, :3]

    def close(self) -> None:
        sct = getattr(self._local, "sct", None)
        if sct is not None:
            try:
                sct.close()
            except Exception:
                pass


def frame_signature(img: np.ndarray, size: int = 16) -> np.ndarray:
    """Cheap downscaled grayscale signature for change detection."""
    h, w = img.shape[:2]
    gray = img.mean(axis=2)
    ys = np.linspace(0, h - 1, size).astype(int)
    xs = np.linspace(0, w - 1, size).astype(int)
    small = gray[np.ix_(ys, xs)]
    return small.astype(np.float32) / 255.0


def signature_distance(a: np.ndarray | None, b: np.ndarray | None) -> float:
    if a is None or b is None or a.shape != b.shape:
        return 1.0
    return float(np.abs(a - b).mean())
