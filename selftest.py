"""Offline self-test for Dave's solve/scoring logic (no screen, no network).

Verifies that:
  - the bank + cache local sources work,
  - the scoring picks the right option from a context snippet,
  - imports resolve correctly.

Run:  python selftest.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.config import load_settings, load_llm_key  # noqa: E402
from src.parse import classify_question, sanitize_options  # noqa: E402
from src.solve import Solver  # noqa: E402


def main() -> int:
    settings = load_settings()
    # Disable network sources for a deterministic offline test.
    settings["sources"] = {"cache": True, "bank": True, "wikipedia": False,
                            "websearch": False, "llm": False}

    tmp = Path(tempfile.mkdtemp(prefix="dave_selftest_"))
    solver = Solver(settings, tmp)

    passed = 0
    failed = 0

    def check(name: str, cond: bool) -> None:
        nonlocal passed, failed
        if cond:
            passed += 1
            print(f"  [PASS] {name}")
        else:
            failed += 1
            print(f"  [FAIL] {name}")

    # 1) bank match
    solver.bank.add("Ibukota Australia adalah?", "Canberra")
    options = {"A": "Sydney", "B": "Melbourne", "C": "Canberra", "D": "Perth"}
    sug = solver.solve("Apa ibukota Australia?", options)
    check("bank answer Canberra", "canberra" in sug.answer_text.lower())
    check("bank hints option C", sug.label == "C")

    # 2) cache instant hit after remember
    solver.remember("Warna langit di siang hari?", "B", "Biru")
    opts2 = {"A": "Merah", "B": "Biru", "C": "Hijau", "D": "Kuning"}
    sug2 = solver.solve("Warna langit di siang hari?", opts2)
    check("cache answer Biru", "biru" in sug2.answer_text.lower())
    check("cache is fast (<50ms)", sug2.elapsed_ms < 50)

    # 3) isian bebas — tanpa opsi, jawaban tetap dari bank
    sug3 = solver.solve("Warna langit di siang hari?", {})
    check("open answer without options", "biru" in sug3.answer_text.lower())
    check("no option label required", sug3.label is None)

    # 4) petunjuk opsi dari jawaban teks
    hint = solver._match_option_hint("Nohoch Mul", {"A": "El Castillo", "B": "Nohoch Mul"})
    check("option hint from answer text", hint == "B")

    # 5) klasifikasi tipe soal
    check("classify mcq", classify_question("Who?", {"A": "alpha", "B": "beta"}) == "mcq")
    check("classify open", classify_question("Type the capital:", {}) == "open")
    check(
        "classify ordering",
        classify_question("Order these cities by size:", {"A": "X", "B": "Y"}) == "ordering",
    )
    check(
        "classify typing as open",
        classify_question("Type your answer below:", {"A": "foo", "B": "bar"}) == "open",
    )
    check(
        "sanitize strips overlay noise",
        sanitize_options({
            "A": "Dave", "B": "Suggest siap — ketik/klik jawaban Anda", "C": "Soal 5 88%",
        }) == {},
    )
    check(
        "sanitize keeps 3 real options",
        len(sanitize_options({"A": "Apple", "B": "Banana", "C": "Cherry"})) == 3,
    )

    solver.close()

    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
