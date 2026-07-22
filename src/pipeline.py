"""Orchestrator: detect new question -> capture -> OCR -> solve -> suggest -> log.

Runs in a background thread and pushes updates to the overlay (which owns the
main thread's Tk loop). New-question detection is visual: it watches the
question region's downscaled signature and fires once the image has changed and
then stayed stable for a couple of frames (so it reads a fully-rendered slide,
not a mid-transition frame).
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz

from .capture import Capturer, frame_signature, signature_distance
from .overlay import Overlay, OverlayState
from .parse import Parser
from .screen import ScreenReader
from .session_logger import SessionLogger
from .solve import Solver


def _ready_status(qtype: str) -> str:
    return {
        "mcq": "Suggest siap — klik opsi jawaban",
        "open": "Suggest siap — ketik jawaban Anda",
        "ordering": "Suggest siap — susun sesuai urutan",
    }.get(qtype, "Suggest siap")


class DavePipeline:
    def __init__(
        self,
        regions: dict[str, Any],
        settings: dict[str, Any],
        data_dir: Path,
        overlay: Overlay,
    ):
        self.settings = settings
        self.overlay = overlay
        self.total_questions = int(settings.get("session", {}).get("total_questions", 20))
        self.capturer = Capturer(regions)
        self.parser = Parser()
        self.solver = Solver(settings, data_dir)
        self.logger = SessionLogger(
            data_dir / "sessions", bot_name="Dave", total_questions=self.total_questions
        )

        self.poll_s = settings.get("poll_interval_ms", 120) / 1000.0
        self.threshold = settings.get("change_threshold", 0.06)
        self.stable_needed = settings.get("stable_frames", 2)

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._qnum = 0
        self._last_answered_sig = None

    # ---- lifecycle ----------------------------------------------------

    def warmup(self) -> None:
        self.overlay.update(OverlayState(status="Memuat OCR..."))
        self.parser.warmup()
        self.solver.warmup()
        self.overlay.update(OverlayState(status="Siap. Menunggu soal...", qnum=_qnum_str(0, self.total_questions)))

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="dave-pipeline", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        self.solver.close()
        self.logger.close()
        self.logger.print_summary()

    # ---- main loop ----------------------------------------------------

    def _loop(self) -> None:
        self.warmup()
        prev_sig = None
        stable_count = 0
        pending = False

        while not self._stop.is_set():
            t_capture = time.perf_counter()
            q_img = self.capturer.grab_question()
            sig = frame_signature(q_img)
            dist = signature_distance(prev_sig, sig)
            prev_sig = sig

            if dist > self.threshold:
                # scene is changing; wait for it to settle
                stable_count = 0
                pending = True
            else:
                stable_count += 1

            if pending and stable_count >= self.stable_needed:
                pending = False
                # avoid re-answering the same settled slide
                if signature_distance(self._last_answered_sig, sig) > self.threshold:
                    self._last_answered_sig = sig
                    self._handle_question(q_img, t_capture)

            self._stop.wait(self.poll_s)

    def _handle_question(self, q_img, t_capture: float) -> None:
        if self.total_questions > 0 and self._qnum >= self.total_questions:
            self.overlay.update(OverlayState(
                status=f"Sesi selesai ({self.total_questions} soal)",
                qnum=f"{self.total_questions}/{self.total_questions}",
                answer="Tutup overlay untuk laporan",
            ))
            return

        self._qnum += 1
        qtag = f"Soal {_qnum_str(self._qnum, self.total_questions)}"
        self.overlay.update(OverlayState(status="Membaca soal...", qnum=qtag))

        # capture options now (question already grabbed)
        opt_imgs = self.capturer.grab_options()
        capture_ms = int((time.perf_counter() - t_capture) * 1000)

        t_parse = time.perf_counter()
        parsed = self.parser.parse(q_img, opt_imgs)
        parse_ms = int((time.perf_counter() - t_parse) * 1000)

        if not parsed.is_usable():
            self.overlay.update(OverlayState(
                status="Soal tak terbaca (cek kalibrasi area SOAL)", qnum=qtag, answer="?"
            ))
            self.logger.log_question(
                self._qnum, self.total_questions, parsed.question, parsed.options, None, "", 0.0,
                "unreadable", capture_ms, parse_ms, 0,
                capture_ms + parse_ms,
            )
            return

        self.overlay.update(OverlayState(status="Mencari jawaban...", qnum=qtag))
        t_solve = time.perf_counter()
        sug = self.solver.solve(
            parsed.question, parsed.options, parsed.question_type,
        )
        solve_ms = int((time.perf_counter() - t_solve) * 1000)
        total_ms = capture_ms + parse_ms + solve_ms

        self.overlay.update(OverlayState(
            status=_ready_status(sug.question_type),
            qnum=qtag,
            label=sug.label or "-",
            answer=sug.answer_text or "(tidak yakin)",
            option_text=sug.option_text,
            question_type=sug.question_type,
            confidence=sug.confidence,
            elapsed_ms=total_ms,
            source=sug.source,
        ))

        self.logger.log_question(
            self._qnum,
            self.total_questions,
            parsed.question,
            parsed.options,
            sug.label,
            sug.answer_text,
            sug.confidence,
            sug.source,
            capture_ms,
            parse_ms,
            solve_ms,
            total_ms,
            sug.scores,
        )


class AutoPipeline:
    """Live-Caption style: no calibration. Scans the whole screen, auto-detects
    the question via OCR, answers it, and shows the suggestion in the overlay.
    """

    def __init__(
        self,
        settings: dict[str, Any],
        data_dir: Path,
        overlay: Overlay,
    ):
        self.settings = settings
        self.overlay = overlay
        self.total_questions = int(settings.get("session", {}).get("total_questions", 20))
        cap_cfg = settings.get("capture", {})
        self.capturer = Capturer({"monitor": cap_cfg.get("monitor", 1)})
        # OCR is tuned for speed within a ~5s quiz budget: crop to the left
        # region (quiz content), downscale, and drop low-confidence background
        # tokens from the animated matrix rain.
        self.reader = ScreenReader(
            max_width=int(settings.get("ocr_width", 820)),
            roi_left_frac=float(settings.get("ocr_roi_left_frac", 0.60)),
            min_conf=float(settings.get("ocr_min_conf", 0.5)),
        )
        self.solver = Solver(settings, data_dir)
        self.logger = SessionLogger(
            data_dir / "sessions", bot_name="Dave", total_questions=self.total_questions
        )

        # Light poll to grab frames; answer when a new slide settles, and also
        # periodically (fallback for animated backgrounds that never settle).
        self.poll_s = settings.get("poll_interval_ms", 150) / 1000.0
        self.ocr_interval_s = settings.get("ocr_interval_ms", 450) / 1000.0
        self.min_q_len = int(settings.get("min_question_len", 8))
        self.change_threshold = float(settings.get("change_threshold", 0.06))
        self.stable_frames = int(settings.get("stable_frames", 2))
        self.new_slide_dist = float(settings.get("new_slide_dist", 0.02))

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._qnum = 0
        self._last_solved_norm = ""
        self._answered_sig = None

    # ---- lifecycle ----------------------------------------------------

    def warmup(self) -> None:
        self.overlay.update(OverlayState(status="Memuat OCR (mode live)..."))
        self.reader.warmup()
        self.solver.warmup()
        self.overlay.update(OverlayState(
            status="Live — mengawasi layar...", qnum=_qnum_str(0, self.total_questions)
        ))

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="dave-auto", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        self.solver.close()
        self.logger.close()
        self.logger.print_summary()

    # ---- main loop ----------------------------------------------------

    def _loop(self) -> None:
        self.warmup()
        prev_sig = None
        stable = 0
        last_attempt = 0.0

        while not self._stop.is_set():
            try:
                frame = self.capturer.grab_monitor()
            except Exception:
                self._stop.wait(self.poll_s)
                continue

            now = time.perf_counter()
            sig = frame_signature(frame, size=24)
            moved = signature_distance(prev_sig, sig) > self.change_threshold
            prev_sig = sig
            stable = 0 if moved else stable + 1
            settled = stable >= self.stable_frames
            new_slide = signature_distance(self._answered_sig, sig) > self.new_slide_dist
            due = (now - last_attempt) >= self.ocr_interval_s

            # Attempt an answer when a NEW slide has settled (static bg), or
            # periodically (fallback for animated backgrounds).
            if not ((settled and new_slide) or due):
                self._stop.wait(self.poll_s)
                continue

            last_attempt = now
            t0 = time.perf_counter()
            try:
                parsed = self.reader.read(frame)
            except Exception:
                parsed = None
            if parsed is None:
                self._stop.wait(self.poll_s)
                continue

            q = parsed.question.strip()
            if len(q) >= self.min_q_len:
                norm = _normalize(q)
                if not _similar(norm, self._last_solved_norm):
                    self._last_solved_norm = norm
                    self._answered_sig = sig
                    self._handle(parsed, frame, t0)
            elif settled and new_slide and self.solver.enabled.get("vision"):
                # Image/emoji slide with little OCR text: let vision read it.
                self._answered_sig = sig
                self._last_solved_norm = ""
                self._handle(parsed, frame, t0)

            self._stop.wait(self.poll_s)

    def _handle(self, parsed, frame, t0: float) -> None:
        if self.total_questions > 0 and self._qnum >= self.total_questions:
            self.overlay.update(OverlayState(
                status=f"Sesi selesai ({self.total_questions} soal)",
                qnum=f"{self.total_questions}/{self.total_questions}",
                answer="Tutup overlay untuk laporan",
            ))
            return

        self._qnum += 1
        qtag = f"Soal {_qnum_str(self._qnum, self.total_questions)}"
        parse_ms = int((time.perf_counter() - t0) * 1000)
        self.overlay.update(OverlayState(status="Mencari jawaban...", qnum=qtag))

        t_solve = time.perf_counter()
        sug = self.solver.solve(
            parsed.question, parsed.options, parsed.question_type, image=frame,
        )
        solve_ms = int((time.perf_counter() - t_solve) * 1000)
        total_ms = parse_ms + solve_ms

        self.overlay.update(OverlayState(
            status=_ready_status(sug.question_type),
            qnum=qtag,
            label=sug.label or "-",
            answer=sug.answer_text or "(tidak yakin)",
            option_text=sug.option_text,
            question_type=sug.question_type,
            confidence=sug.confidence,
            elapsed_ms=total_ms,
            source=sug.source,
        ))

        self.logger.log_question(
            self._qnum, self.total_questions, parsed.question, parsed.options,
            sug.label, sug.answer_text, sug.confidence, sug.source,
            0, parse_ms, solve_ms, total_ms, sug.scores,
        )


def _qnum_str(n: int, total: int) -> str:
    """Counter untuk overlay. total<=0 berarti tanpa batas soal."""
    return f"{n}/{total}" if total > 0 else str(n)


def _normalize(text: str) -> str:
    import re
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _similar(a: str, b: str, threshold: int = 88) -> bool:
    if not a or not b:
        return False
    return fuzz.ratio(a, b) >= threshold
