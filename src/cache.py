"""Tiny persistent cache: question_hash -> {label, text}.

Backed by a JSON file, loaded once and flushed on close. Cache hits are the
fastest possible source (<1 ms), so confirmed answers from past sessions make
Dave near-instant on repeated questions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class Cache:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._data: dict[str, Any] = {}
        self._dirty = False
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                self._data = {}

    def get(self, key: str) -> dict | None:
        return self._data.get(key)

    def put(self, key: str, value: dict) -> None:
        self._data[key] = value
        self._dirty = True
        # write-through so a crash mid-session doesn't lose confirmations
        self.flush()

    def flush(self) -> None:
        if not self._dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self._dirty = False
