"""OCR parsing: turn region images into question text + option texts.

Primary engine: RapidOCR (ONNX runtime) - pip-only, no external binary.
The engine is created once and reused (pre-warmed) to avoid per-question
startup cost, which is critical for the <3s budget.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class ParsedQuestion:
    question: str
    options: dict[str, str] = field(default_factory=dict)  # {"A": "text", ...}
    raw_question_conf: float = 0.0
    question_type: str = "open"  # open | mcq | ordering

    def is_usable(self) -> bool:
        """Cukup baca teks soal — opsi tidak wajib (PG atau isian sendiri)."""
        return len(self.question.strip()) >= 3


_ORDERING_RE = re.compile(
    r"\b(order|arrange|rank|sort|sequence|urutkan|susun|ranking|rangking|"
    r"from\s+(?:most|least|highest|lowest|best|worst)|"
    r"terbesar|terkecil|tertinggi|terendah)\b",
    re.I,
)

_TYPING_HINT_RE = re.compile(
    r"\b(type\s+your|enter\s+your|write\s+(?:the|your)|fill\s+in|"
    r"ketik|tulis|masukkan)\b",
    re.I,
)

_OPTION_NOISE = re.compile(
    r"(dave|suggest\s+siap|ketik|klik|jawaban\s+anda|soal\s*\d|"
    r"\d+\s*%\s*\||\|\s*\d+\s*ms|hide\s+the|^\s*[-–—•]\s*$|"
    r"^\d+\s*ms\s*\||host\s*\d|\bllm\b|submit\s+answer|game\s+paused)",
    re.I,
)


def sanitize_options(options: dict[str, str]) -> dict[str, str]:
    """Buang noise (overlay Dave, meta UI) — sisakan tombol jawaban asli."""
    clean: dict[str, str] = {}
    labels = ["A", "B", "C", "D", "E", "F"]
    i = 0
    for _label, text in sorted(options.items()):
        t = (text or "").strip()
        if len(t) < 2:
            continue
        if _OPTION_NOISE.search(t):
            continue
        if re.fullmatch(r"[\d%|·\-–—\s.:]+", t):
            continue
        if i < len(labels):
            clean[labels[i]] = t
            i += 1
    return clean


def classify_question(question: str, options: dict[str, str]) -> str:
    """open = ketik manual, mcq = PG (3–4 opsi), ordering = susun urutan."""
    options = sanitize_options(options)
    q = question or ""
    if _ORDERING_RE.search(q):
        return "ordering"
    if _TYPING_HINT_RE.search(q):
        return "open"
    if len(options) >= 2:
        return "mcq"
    return "open"


_WHITESPACE = re.compile(r"\s+")


def _clean(text: str) -> str:
    text = _WHITESPACE.sub(" ", text or "").strip()
    return text


class OcrEngine:
    """Thin wrapper around RapidOCR (3.x API) with a warmup call.

    RapidOCR 3.x returns a RapidOCROutput object exposing `.txts` (tuple of
    strings) and `.scores` (tuple of floats); both may be None when nothing is
    detected.
    """

    def __init__(self) -> None:
        from rapidocr import RapidOCR

        self._ocr = RapidOCR()

    def warmup(self) -> None:
        dummy = np.full((32, 128, 3), 255, dtype=np.uint8)
        try:
            self._ocr(dummy)
        except Exception:
            pass

    def read(self, img: np.ndarray) -> tuple[str, float]:
        """Return (joined_text, mean_confidence)."""
        try:
            result = self._ocr(img)
        except Exception:
            return "", 0.0
        txts = getattr(result, "txts", None)
        scores = getattr(result, "scores", None)
        if not txts:
            return "", 0.0
        parts = [t for t in txts if t]
        text = _clean(" ".join(parts))
        if scores:
            confs = [float(s) for s in scores if s is not None]
            conf = float(np.mean(confs)) if confs else 0.0
        else:
            conf = 0.0
        return text, conf

    def read_lines(self, img: np.ndarray) -> list[tuple[str, float]]:
        """Return OCR lines sorted top-to-bottom, left-to-right (for option panels)."""
        items = self.read_raw(img)
        return [(it["text"], it["conf"]) for it in items]

    def read_raw(self, img: np.ndarray) -> list[dict]:
        """Return OCR items with geometry, sorted top-to-bottom, left-to-right.

        Each item: {text, conf, x0, y0, x1, y1, cx, cy, h, w}. Geometry lets the
        auto screen-reader locate the question (largest text / ends with '?')
        without any manual calibration.
        """
        try:
            result = self._ocr(img)
        except Exception:
            return []
        boxes = getattr(result, "boxes", None)
        txts = getattr(result, "txts", None)
        scores = getattr(result, "scores", None)
        if not txts:
            return []
        items: list[dict] = []
        for i, txt in enumerate(txts):
            if not txt or not str(txt).strip():
                continue
            conf = float(scores[i]) if scores is not None and i < len(scores) else 0.0
            if boxes is not None and i < len(boxes):
                box = boxes[i]
                xs = [float(p[0]) for p in box]
                ys = [float(p[1]) for p in box]
                x0, x1 = min(xs), max(xs)
                y0, y1 = min(ys), max(ys)
            else:
                x0 = y0 = float(i)
                x1 = y1 = float(i)
            items.append({
                "text": str(txt).strip(),
                "conf": conf,
                "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                "cx": (x0 + x1) / 2, "cy": (y0 + y1) / 2,
                "h": max(1.0, y1 - y0), "w": max(1.0, x1 - x0),
            })
        items.sort(key=lambda it: (it["cy"], it["cx"]))
        return items


class Parser:
    def __init__(self, engine: OcrEngine | None = None):
        self.engine = engine or OcrEngine()

    def warmup(self) -> None:
        self.engine.warmup()

    def parse(
        self,
        question_img: np.ndarray,
        option_imgs: list[tuple[str, np.ndarray]],
    ) -> ParsedQuestion:
        q_text, q_conf = self.engine.read(question_img)
        options: dict[str, str] = {}

        if len(option_imgs) == 1 and option_imgs[0][0] == "__panel__":
            options = self._parse_options_panel(option_imgs[0][1])
        else:
            for label, img in option_imgs:
                text, _ = self.engine.read(img)
                text = _strip_leading_label(text, label)
                if text:
                    options[label] = text
        options = sanitize_options(options)

        return ParsedQuestion(
            question=q_text,
            options=options,
            raw_question_conf=q_conf,
            question_type=classify_question(q_text, options),
        )

    def _parse_options_panel(self, img: np.ndarray) -> dict[str, str]:
        """OCR satu panel berisi tombol PG (3–6 opsi); urutkan posisi -> A, B, C..."""
        lines = self.engine.read_lines(img)
        labels = ["A", "B", "C", "D", "E", "F"]
        options: dict[str, str] = {}
        for i, (text, _) in enumerate(lines[:6]):
            text = _clean(text)
            # buang label A)/B) jika OCR menangkapnya
            text = _LEADING_LABEL.sub("", text).strip() or text
            if text:
                options[labels[i]] = text
        return options


_LEADING_LABEL = re.compile(r"^\s*([A-Da-d])[\.\)\:\-]\s*")


def _strip_leading_label(text: str, label: str) -> str:
    """Remove a leading 'A)' / 'A.' if OCR captured the option letter."""
    text = _clean(text)
    m = _LEADING_LABEL.match(text)
    if m:
        return text[m.end():].strip()
    return text
