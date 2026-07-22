"""Auto screen reader (Live-Caption style): OCR the whole screen and detect the
question automatically — no manual calibration / region drawing.

Heuristics (tuned for Quiz.com-style slides but generic):
    - Question = text block that ends with "?", else the largest-font text in
      the upper part of the screen. Multi-line questions are merged.
    - Options  = best-effort cluster of similar-height lines below the question
      (used only as an optional MCQ hint; Dave answers from the question text).
"""

from __future__ import annotations

import re

import numpy as np

from .parse import OcrEngine, ParsedQuestion, _clean, classify_question, sanitize_options

_LEADING_LABEL = re.compile(r"^\s*([A-Da-d])[\.\)\:\-]\s*")


class ScreenReader:
    def __init__(
        self,
        engine: OcrEngine | None = None,
        max_width: int = 820,
        roi_left_frac: float = 0.60,
        min_conf: float = 0.5,
    ):
        self.engine = engine or OcrEngine()
        self.max_width = max_width
        # Quiz.com layout: question + answer buttons live in the LEFT portion of
        # the screen; the right side is a (often animated) illustration. Cropping
        # to the left both removes that noise AND ~halves OCR work.
        self.roi_left_frac = roi_left_frac
        # RapidOCR confidence gate: the faint animated "matrix rain" background
        # produces low-confidence garbage tokens; drop them so they can't be
        # mistaken for the question. Real question/option text scores high.
        self.min_conf = min_conf

    def warmup(self) -> None:
        self.engine.warmup()

    def _prep(self, img: np.ndarray) -> np.ndarray:
        h, w = img.shape[:2]
        if 0.0 < self.roi_left_frac < 1.0:
            img = img[:, : int(w * self.roi_left_frac)]
            h, w = img.shape[:2]
        if w > self.max_width:
            scale = self.max_width / w
            try:
                import cv2

                img = cv2.resize(img, (int(w * scale), int(h * scale)),
                                 interpolation=cv2.INTER_AREA)
            except Exception:
                step = max(1, int(round(1 / scale)))
                img = img[::step, ::step]
        return img

    def read(self, img: np.ndarray) -> ParsedQuestion:
        img = self._prep(img)
        items = self.engine.read_raw(img)
        if self.min_conf > 0:
            items = [it for it in items if it["conf"] >= self.min_conf]
        if not items:
            return ParsedQuestion(question="", options={})
        h = img.shape[0]
        w = img.shape[1]
        question, anchor = _detect_question(items, h, w)
        options = sanitize_options(
            _detect_options(items, anchor, h, w) if anchor else {}
        )
        qtype = classify_question(question, options)
        return ParsedQuestion(question=question, options=options, question_type=qtype)


def _median_height(items: list[dict]) -> float:
    hs = sorted(it["h"] for it in items)
    return hs[len(hs) // 2] if hs else 1.0


def _detect_question(items: list[dict], img_h: int, img_w: int) -> tuple[str, dict | None]:
    if not items:
        return "", None

    # 1) Prefer a line that ends with '?'.
    q_marks = [it for it in items if it["text"].rstrip().endswith("?")]
    ends_with_q = bool(q_marks)
    if q_marks:
        anchor = max(q_marks, key=lambda it: it["h"])
    else:
        # 2) Otherwise the largest-font text in the top ~65% of the screen.
        top = [it for it in items if it["cy"] < img_h * 0.65]
        pool = top or items
        anchor = max(pool, key=lambda it: it["h"])

    ah = anchor["h"]

    def _same_line_or_wrap(it: dict) -> bool:
        # similar font size and horizontally overlapping the question block
        if abs(it["h"] - ah) > 0.5 * ah:
            return False
        overlap = min(it["x1"], anchor["x1"]) - max(it["x0"], anchor["x0"])
        return overlap > -0.15 * img_w  # roughly same column area

    if ends_with_q:
        # '?' sits on the LAST line -> only merge lines ABOVE the anchor
        # (wrapped question), never lines below (those are options/timer).
        group = [
            it for it in items
            if it["cy"] < anchor["cy"] - 0.4 * ah
            and (anchor["cy"] - 2.6 * ah) <= it["cy"]
            and _same_line_or_wrap(it)
        ]
    else:
        group = [
            it for it in items
            if abs(it["cy"] - anchor["cy"]) <= 1.4 * ah
            and _same_line_or_wrap(it)
        ]
    if anchor not in group:
        group.append(anchor)
    group.sort(key=lambda it: (it["cy"], it["cx"]))
    text = _clean(" ".join(it["text"] for it in group))
    return text, anchor


_NOISE = re.compile(
    r"^(ai\s*generated|aigenerated|slide|pin|dave|suggest\s+siap|"
    r"ketik|klik|jawaban\s+anda|soal\s+\d+)\b",
    re.I,
)


def _detect_options(items: list[dict], anchor: dict, img_h: int, img_w: int) -> dict[str, str]:
    """Best-effort MCQ options: the largest cluster of similar-height lines below
    the question. Height-clustering naturally drops odd noise (timer, watermark).
    """
    q_bottom = anchor["y1"]
    # Options live in a band under the question, not at the very bottom
    # (that zone is the OS taskbar / watermark on a full-screen grab).
    band_bottom = min(img_h * 0.94, q_bottom + img_h * 0.55)

    candidates = []
    for it in items:
        if it is anchor:
            continue
        if it["cy"] <= q_bottom + 0.3 * anchor["h"]:
            continue  # above / overlapping the question
        if it["cy"] > band_bottom:
            continue  # taskbar / footer zone
        if it["cy"] > img_h * 0.82:
            continue  # taskbar zone
        if it["cx"] < img_w * 0.02 or it["cx"] > img_w * 0.98:
            continue  # far screen-edge tokens (image already cropped away)
        txt = it["text"].strip()
        if len(txt) < 2 or txt.rstrip().endswith("?"):
            continue
        if _NOISE.match(txt):
            continue
        if re.fullmatch(r"\d{1,4}", txt):
            continue  # lone timer / score number
        candidates.append(it)

    if len(candidates) < 2:
        return {}

    # Cluster by height: keep the biggest group of lines within ±30% height.
    best_cluster: list[dict] = []
    for pivot in candidates:
        ph = pivot["h"]
        cluster = [it for it in candidates if abs(it["h"] - ph) <= 0.3 * ph]
        if len(cluster) > len(best_cluster):
            best_cluster = cluster

    if len(best_cluster) < 2:
        return {}

    best_cluster.sort(key=lambda it: (it["cy"], it["cx"]))
    labels = ["A", "B", "C", "D", "E", "F"]
    options: dict[str, str] = {}
    # PG quiz.com: biasanya 3 atau 4 tombol; dukung sampai 6.
    for i, it in enumerate(best_cluster[:6]):
        text = _LEADING_LABEL.sub("", _clean(it["text"])).strip() or _clean(it["text"])
        if text and not _NOISE.match(text):
            options[labels[i]] = text
    return options
