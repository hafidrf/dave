"""Calibration wizard — 2 langkah, klik 2x (tanpa tarik).

Cocok untuk layout Quiz.com: satu kotak soal + satu kotak semua tombol jawaban.

    Langkah 1: klik sudut kiri-atas soal, lalu sudut kanan-bawah soal
    Langkah 2: klik sudut kiri-atas area 4 tombol, lalu sudut kanan-bawah

ESC = batal. Hasil -> config/regions.json
"""

from __future__ import annotations

import json
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config"

STEPS = [
    (
        "question",
        "SOAL — klik pojok KIRI ATAS teks pertanyaan",
        "lalu klik pojok KANAN BAWAH teks pertanyaan",
    ),
    (
        "options_panel",
        "OPSI (opsional) — klik area SEMUA tombol jawaban, atau tekan S untuk lewati",
        "Lewati jika soal isian bebas / tanpa tombol pilihan",
    ),
]


class Calibrator:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Dave — Kalibrasi")
        self.root.attributes("-fullscreen", True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.35)
        self.root.configure(bg="#000000")

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()

        self.canvas = tk.Canvas(
            self.root, cursor="crosshair", bg="#111111",
            highlightthickness=0, width=sw, height=sh,
        )
        self.canvas.pack(fill="both", expand=True)

        # Banner instruksi (solid, selalu terbaca).
        self.canvas.create_rectangle(20, 15, sw - 20, 130, fill="#0f172a", outline="#22ff88", width=3)
        self.title_txt = self.canvas.create_text(
            sw // 2, 45, fill="#ffffff", font=("Segoe UI", 20, "bold"), text=""
        )
        self.hint_txt = self.canvas.create_text(
            sw // 2, 78, fill="#fbbf24", font=("Segoe UI", 14), text=""
        )
        self.step_txt = self.canvas.create_text(
            sw // 2, 108, fill="#94a3b8", font=("Segoe UI", 12),
            text="Klik 1 = kiri-atas  |  Klik 2 = kanan-bawah  |  S = lewati opsi  |  ESC = batal",
        )
        self.status_txt = self.canvas.create_text(
            sw // 2, sh - 50, fill="#22ff88", font=("Segoe UI", 16, "bold"), text=""
        )

        self.step = 0
        self.click_num = 0  # 0 = tunggu klik pertama, 1 = tunggu klik kedua
        self.corner1: tuple[int, int] | None = None
        self.preview_items: list[int] = []
        self.done_items: list[int] = []
        self.results: dict[str, dict] = {}

        self.canvas.bind("<Button-1>", self._click)
        self.root.bind("<Escape>", lambda e: self._cancel())
        self.root.bind("<s>", lambda e: self._skip_step())
        self.root.bind("<S>", lambda e: self._skip_step())
        self._show_step()

    def _skip_step(self) -> None:
        if STEPS[self.step][0] != "options_panel":
            return
        self.canvas.itemconfig(self.status_txt, text="Langkah opsi dilewati (mode isian bebas).")
        self.step += 1
        if self.step >= len(STEPS):
            self.root.after(400, self._finish)
        else:
            self._show_step()

    def _show_step(self) -> None:
        key, line1, line2 = STEPS[self.step]
        self.click_num = 0
        self.corner1 = None
        self.canvas.itemconfig(self.title_txt, text=f"[{self.step + 1}/{len(STEPS)}]  {line1}")
        self.canvas.itemconfig(self.hint_txt, text=line2)
        self.canvas.itemconfig(self.status_txt, text="Menunggu klik pertama (pojok kiri-atas)...")

    def _clear_preview(self) -> None:
        for item in self.preview_items:
            self.canvas.delete(item)
        self.preview_items.clear()

    def _click(self, event) -> None:
        x, y = event.x, event.y

        if self.click_num == 0:
            self.corner1 = (x, y)
            self.click_num = 1
            self._clear_preview()
            dot = self.canvas.create_oval(
                x - 12, y - 12, x + 12, y + 12,
                fill="#fbbf24", outline="#ffffff", width=3,
            )
            self.preview_items.append(dot)
            self.canvas.itemconfig(
                self.status_txt,
                text=f"Klik 1 OK ({x},{y}) — sekarang klik pojok KANAN BAWAH...",
            )
            return

        # Klik kedua — bentuk kotak.
        assert self.corner1 is not None
        x0, y0 = self.corner1
        left, top = min(x0, x), min(y0, y)
        width, height = abs(x - x0), abs(y - y0)

        if width < 30 or height < 20:
            self.canvas.itemconfig(
                self.status_txt,
                text=f"Kotak terlalu kecil ({width}x{height}). Ulangi: klik kiri-atas lalu kanan-bawah.",
            )
            self.click_num = 0
            self.corner1 = None
            self._clear_preview()
            return

        key = STEPS[self.step][0]
        box = {"left": left, "top": top, "width": width, "height": height}
        self.results[key] = box

        self._clear_preview()
        label = "SOAL" if key == "question" else "OPSI (A-D)"
        rid = self.canvas.create_rectangle(
            left, top, left + width, top + height,
            outline="#22ff88", width=4, fill="#14532d",
        )
        lid = self.canvas.create_text(
            left + 10, top + 10, text=label, anchor="nw",
            fill="#ffffff", font=("Segoe UI", 16, "bold"),
        )
        self.done_items.extend([rid, lid])

        self.canvas.itemconfig(
            self.status_txt,
            text=f"✓ {label} tersimpan ({width} x {height} px)",
        )

        self.step += 1
        if self.step >= len(STEPS):
            self.root.after(800, self._finish)
        else:
            self.root.after(600, self._show_step)

    def _finish(self) -> None:
        data: dict = {
            "monitor": 1,
            "question": self.results["question"],
        }
        if "options_panel" in self.results:
            data["options_panel"] = self.results["options_panel"]
        CONFIG.mkdir(parents=True, exist_ok=True)
        out = CONFIG / "regions.json"
        out.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(f"[OK] Region tersimpan -> {out}")
        self.canvas.itemconfig(self.title_txt, text="Selesai! Kalibrasi tersimpan.")
        self.canvas.itemconfig(self.hint_txt, text="Tutup jendela ini, lalu jalankan Dave.")
        self.root.after(1200, self.root.destroy)

    def _cancel(self) -> None:
        print("[batal] Kalibrasi dibatalkan.")
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    print("=" * 58)
    print("  Dave — Kalibrasi (2 langkah, klik 2x per area)")
    print("=" * 58)
    print("  1. Buka Quiz.com — tampilkan contoh soal di layar")
    print("  2. Langkah 1: area SOAL (wajib)")
    print("  3. Langkah 2: area OPSI (opsional — tekan S untuk lewati / isian bebas)")
    print("=" * 58)
    Calibrator().run()
