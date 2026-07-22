"""Local Q&A bank (SQLite) with fuzzy matching.

Holds curated / imported question->answer pairs (e.g. from previous Friday
boarding sessions). Matching is done in-memory with rapidfuzz over normalized
questions - fast for banks up to tens of thousands of rows.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from rapidfuzz import fuzz, process


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


class Bank:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS qa (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                qnorm TEXT NOT NULL
            )
            """
        )
        self._conn.commit()
        self._cache_loaded = False
        self._rows: list[tuple[str, str]] = []  # (qnorm, answer)

    def _ensure_loaded(self) -> None:
        if self._cache_loaded:
            return
        cur = self._conn.execute("SELECT qnorm, answer FROM qa")
        self._rows = cur.fetchall()
        self._cache_loaded = True

    def add(self, question: str, answer: str) -> None:
        self._conn.execute(
            "INSERT INTO qa (question, answer, qnorm) VALUES (?, ?, ?)",
            (question, answer, _norm(question)),
        )
        self._conn.commit()
        self._cache_loaded = False

    def add_many(self, pairs: list[tuple[str, str]]) -> int:
        self._conn.executemany(
            "INSERT INTO qa (question, answer, qnorm) VALUES (?, ?, ?)",
            [(q, a, _norm(q)) for q, a in pairs],
        )
        self._conn.commit()
        self._cache_loaded = False
        return len(pairs)

    def best_match(self, question: str) -> tuple[str, float] | None:
        """Return (answer, score 0..1) for the closest stored question."""
        self._ensure_loaded()
        if not self._rows:
            return None
        qn = _norm(question)
        choices = [r[0] for r in self._rows]
        match = process.extractOne(qn, choices, scorer=fuzz.token_set_ratio)
        if not match:
            return None
        _, score, idx = match
        return self._rows[idx][1], score / 100.0

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM qa").fetchone()[0]

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass
