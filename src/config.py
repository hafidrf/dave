"""Load and merge configuration (regions + settings) with sensible defaults."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


def load_llm_key(settings: dict[str, Any] | None = None) -> bool:
    """If the LLM key env var isn't already set, try to read it from
    config/llm_key.txt (gitignored). Returns True if a key is available.

    This lets the desktop shortcut work without manually exporting an env var
    every session.
    """
    env_name = "DAVE_LLM_KEY"
    if settings:
        env_name = settings.get("llm", {}).get("api_key_env", env_name)

    if os.environ.get(env_name):
        return True

    key_file = CONFIG_DIR / "llm_key.txt"
    if key_file.exists():
        key = key_file.read_text(encoding="utf-8").strip()
        # ignore comment/placeholder lines
        key = "\n".join(
            ln for ln in key.splitlines() if ln.strip() and not ln.strip().startswith("#")
        ).strip()
        if key:
            os.environ[env_name] = key
            return True
    return False

DEFAULT_SETTINGS: dict[str, Any] = {
    "mode": "auto",
    "capture": {
        "monitor": 1,
        "max_width": 1440,
    },
    "min_question_len": 8,
    "ocr_width": 820,
    "ocr_roi_left_frac": 0.60,
    "ocr_min_conf": 0.5,
    "poll_interval_ms": 150,
    "ocr_interval_ms": 450,
    "language": "id",
    "sources": {
        "cache": True,
        "bank": True,
        "vision": True,
        "wiki_options": True,
        "wikipedia": True,
        "websearch": True,
        "llm": True,
    },
    "change_threshold": 0.06,
    "stable_frames": 2,
    "new_slide_dist": 0.02,
    "source_weights": {
        "cache": 1.0,
        "bank": 0.95,
        "wiki_options": 0.9,
        "wikipedia": 0.6,
        "websearch": 0.6,
        "llm": 0.85,
    },
    "timeouts_ms": {
        "wikipedia": 2000,
        "websearch": 2000,
        "llm": 2500,
        "total_solve": 2800,
    },
    "llm": {
        "base_url": "https://api.groq.com/openai/v1",
        "model": "llama-3.3-70b-versatile",
        "vision_model": "meta-llama/llama-4-scout-17b-16e-instruct",
        "api_key_env": "DAVE_LLM_KEY",
    },
    "overlay": {
        "corner": "bottom-right",
        "opacity": 0.92,
        "font_size": 14,
    },
    "session": {
        "total_questions": 0,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        if key.startswith("_"):
            continue
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_settings(path: Path | None = None) -> dict[str, Any]:
    path = path or (CONFIG_DIR / "settings.json")
    if path.exists():
        user = json.loads(path.read_text(encoding="utf-8"))
        return _deep_merge(DEFAULT_SETTINGS, user)
    return json.loads(json.dumps(DEFAULT_SETTINGS))


def load_regions(path: Path | None = None) -> dict[str, Any]:
    path = path or (CONFIG_DIR / "regions.json")
    if not path.exists():
        raise FileNotFoundError(
            f"Region belum dikalibrasi: {path}\n"
            "Jalankan dulu:  python calibrate.py"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    if "question" not in data:
        raise ValueError("regions.json tidak valid: butuh 'question'.")
    has_panel = "options_panel" in data or "options" in data
    if not has_panel:
        print("[i] regions.json: hanya area soal (mode isian bebas / tanpa opsi).")
    return data
