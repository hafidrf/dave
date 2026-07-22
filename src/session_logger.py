"""Session logging + end-of-session report.

Writes one JSON-lines file per session (data/sessions/<timestamp>.jsonl) plus a
printed summary: suggest latency, per-stage breakdown, accuracy (if answers are
confirmed), and the dominant source.
"""

from __future__ import annotations

import json
import statistics
import time
from datetime import datetime
from pathlib import Path
from typing import Any


class SessionLogger:
    def __init__(self, sessions_dir: Path, bot_name: str = "Dave", total_questions: int = 20):
        self.dir = Path(sessions_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.bot_name = bot_name
        self.total_questions = total_questions
        self.started = datetime.now()
        stamp = self.started.strftime("%Y%m%d_%H%M%S")
        self.path = self.dir / f"{stamp}.jsonl"
        self.records: list[dict[str, Any]] = []
        self._fh = self.path.open("w", encoding="utf-8")
        self._write_meta()

    def _write_meta(self) -> None:
        meta = {
            "type": "meta",
            "bot": self.bot_name,
            "total_questions": self.total_questions,
            "started": self.started.isoformat(timespec="seconds"),
        }
        self._fh.write(json.dumps(meta, ensure_ascii=False) + "\n")
        self._fh.flush()

    def log_question(
        self,
        qnum: int,
        total: int,
        question: str,
        options: dict[str, str],
        suggestion_label: str | None,
        suggestion_text: str,
        confidence: float,
        source: str,
        capture_ms: int,
        parse_ms: int,
        solve_ms: int,
        total_ms: int,
        scores: dict[str, float] | None = None,
    ) -> None:
        rec = {
            "type": "question",
            "qnum": qnum,
            "total": total,
            "ts": time.time(),
            "question": question,
            "options": options,
            "suggest_label": suggestion_label,
            "suggest_text": suggestion_text,
            "confidence": confidence,
            "source": source,
            "capture_ms": capture_ms,
            "parse_ms": parse_ms,
            "solve_ms": solve_ms,
            "total_ms": total_ms,
            "scores": scores or {},
            "confirmed_correct": None,
        }
        self.records.append(rec)
        self._fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self._fh.flush()

    def summary(self) -> dict[str, Any]:
        qs = [r for r in self.records if r["type"] == "question"]
        if not qs:
            return {"questions": 0}
        totals = [r["total_ms"] for r in qs]
        parses = [r["parse_ms"] for r in qs]
        solves = [r["solve_ms"] for r in qs]
        sources: dict[str, int] = {}
        for r in qs:
            sources[r["source"]] = sources.get(r["source"], 0) + 1
        target = self.total_questions if self.total_questions > 0 else "∞"
        return {
            "questions": len(qs),
            "target_questions": target,
            "completion": f"{len(qs)}/{target}",
            "avg_total_ms": round(statistics.mean(totals)),
            "median_total_ms": round(statistics.median(totals)),
            "fastest_ms": min(totals),
            "slowest_ms": max(totals),
            "avg_parse_ms": round(statistics.mean(parses)),
            "avg_solve_ms": round(statistics.mean(solves)),
            "sources": sources,
            "under_3s": sum(1 for t in totals if t <= 3000),
        }

    def print_summary(self) -> None:
        s = self.summary()
        print("\n" + "=" * 52)
        print(f"  {self.bot_name} — Ringkasan Sesi")
        print("=" * 52)
        if s.get("questions", 0) == 0:
            print("  Belum ada soal terekam.")
            print("=" * 52)
            return
        print(f"  Soal terekam     : {s['questions']}/{s.get('target_questions', 20)}")
        print(f"  Rata-rata total : {s['avg_total_ms']} ms  (median {s['median_total_ms']} ms)")
        print(f"  Tercepat        : {s['fastest_ms']} ms")
        print(f"  Terlambat       : {s['slowest_ms']} ms")
        print(f"  <= 3 detik      : {s['under_3s']}/{s['questions']}")
        print(f"  Rata OCR/parse  : {s['avg_parse_ms']} ms")
        print(f"  Rata solve      : {s['avg_solve_ms']} ms")
        print(f"  Sumber dipakai  : {s['sources']}")
        print(f"  Log             : {self.path}")
        print("=" * 52)

    def close(self) -> None:
        try:
            summary_rec = {"type": "summary", **self.summary()}
            self._fh.write(json.dumps(summary_rec, ensure_ascii=False) + "\n")
            self._fh.close()
        except Exception:
            pass
