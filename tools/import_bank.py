"""Import question->answer pairs into the local bank (data/bank.db).

Supports CSV (columns: question,answer) or JSON (list of {question, answer}).

Usage:
    python tools/import_bank.py path/to/soal.csv
    python tools/import_bank.py path/to/soal.json
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.bank import Bank  # noqa: E402

DATA_DIR = ROOT / "data"


def load_pairs(path: Path) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                q = (row.get("question") or row.get("soal") or "").strip()
                a = (row.get("answer") or row.get("jawaban") or "").strip()
                if q and a:
                    pairs.append((q, a))
    elif path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        for row in data:
            q = (row.get("question") or row.get("soal") or "").strip()
            a = (row.get("answer") or row.get("jawaban") or "").strip()
            if q and a:
                pairs.append((q, a))
    else:
        raise ValueError("Format tidak didukung. Pakai .csv atau .json")
    return pairs


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    path = Path(sys.argv[1])
    if not path.exists():
        print(f"[!] File tidak ditemukan: {path}")
        return 1

    pairs = load_pairs(path)
    if not pairs:
        print("[!] Tidak ada pasangan question/answer yang valid.")
        return 1

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    bank = Bank(DATA_DIR / "bank.db")
    n = bank.add_many(pairs)
    total = bank.count()
    bank.close()
    print(f"[OK] {n} soal diimpor. Total bank sekarang: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
