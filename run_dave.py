"""Dave entrypoint.

Wiring: the overlay owns the main thread (Tk mainloop); the detection/solve
pipeline runs in a background thread and pushes suggestions to the overlay.

Usage:
    python run_dave.py

Prereqs:
    1) python calibrate.py            # once, to set screen regions
    2) (optional) copy config/settings.example.json -> config/settings.json
    3) (optional) set DAVE_LLM_KEY to enable the LLM source
"""

from __future__ import annotations

import signal
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.config import load_llm_key, load_regions, load_settings  # noqa: E402
from src.overlay import Overlay  # noqa: E402
from src.pipeline import AutoPipeline, DavePipeline  # noqa: E402

DATA_DIR = ROOT / "data"


def main() -> int:
    settings = load_settings()
    mode = settings.get("mode", "auto")
    total = settings.get("session", {}).get("total_questions", 20)

    if settings.get("sources", {}).get("llm"):
        if load_llm_key(settings):
            print("[i] LLM aktif sebagai sumber/tie-breaker.")
        else:
            env_name = settings.get("llm", {}).get("api_key_env", "DAVE_LLM_KEY")
            print(
                f"[!] LLM diaktifkan tapi API key belum ada.\n"
                f"    Tempel key ke: config\\llm_key.txt  (atau set env {env_name}).\n"
                f"    Dave tetap jalan; sumber lain (wiki/websearch) tetap dipakai."
            )

    ov_cfg = settings.get("overlay", {})
    overlay = Overlay(
        corner=ov_cfg.get("corner", "bottom-right"),
        opacity=ov_cfg.get("opacity", 0.92),
        font_size=ov_cfg.get("font_size", 14),
    )

    if mode == "region":
        try:
            regions = load_regions()
        except (FileNotFoundError, ValueError) as e:
            print(f"[!] {e}")
            return 1
        print(f"[i] Sesi: {total} soal | mode: region (kalibrasi)")
        pipeline = DavePipeline(regions, settings, DATA_DIR, overlay)
    else:
        print(f"[i] Sesi: {total} soal | mode: LIVE (auto-scan layar, tanpa kalibrasi)")
        pipeline = AutoPipeline(settings, DATA_DIR, overlay)

    def shutdown(*_):
        pipeline.stop()
        overlay.stop()

    signal.signal(signal.SIGINT, shutdown)

    pipeline.start()
    try:
        overlay.run()  # blocks until window closed
    finally:
        pipeline.stop()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
