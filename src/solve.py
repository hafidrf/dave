"""Multi-source answer solver.

Untuk soal PG (opsi terdeteksi): jawaban WAJIB salah satu opsi di layar.
Untuk soal isian: jawaban teks bebas dari LLM / Wikipedia / web.
"""

from __future__ import annotations

import hashlib
import re
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import quote as requests_quote

from rapidfuzz import fuzz

from .bank import Bank
from .cache import Cache
from .parse import classify_question, sanitize_options


@dataclass
class SourceResult:
    source: str
    kind: str  # "answer" | "scores"
    text: str
    confidence: float = 0.6
    option_scores: dict[str, float] | None = None


@dataclass
class Suggestion:
    label: str | None          # petunjuk MCQ opsional (A-D), bisa None
    answer_text: str           # jawaban utama yang ditampilkan
    confidence: float
    source: str
    scores: dict[str, float]   # skor opsi jika ada, else {}
    elapsed_ms: int
    parsed_options: dict[str, str]
    option_text: str = ""      # teks opsi yang cocok (MCQ), untuk ditampilkan
    question_type: str = "open"  # open | mcq | ordering


_STOP = {
    "the", "a", "an", "of", "is", "are", "to", "in", "on", "and", "or", "what",
    "which", "who", "whom", "where", "when", "how", "why", "adalah", "yang",
    "apa", "siapa", "dimana", "di", "ke", "dari", "dan", "atau", "itu", "ini",
    "sebutkan", "berikut", "manakah", "merupakan", "name", "nama",
}
_WORD = re.compile(r"[a-z0-9]+", re.I)


def _keywords(text: str) -> list[str]:
    return [w for w in _WORD.findall((text or "").lower()) if w not in _STOP and len(w) > 2]


def normalize_question(q: str) -> str:
    return re.sub(r"\s+", " ", (q or "").strip().lower())


def question_hash(q: str) -> str:
    return hashlib.sha1(normalize_question(q).encode("utf-8")).hexdigest()[:16]


def _clean_answer(text: str, max_len: int = 120) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    text = text.strip("\"'.,;:!?")
    if len(text) > max_len:
        text = text[:max_len].rsplit(" ", 1)[0] + "…"
    return text


def _encode_frame(image, max_w: int = 1024) -> str:
    """Downscale a BGR screenshot and return a base64 JPEG (for vision LLM)."""
    try:
        import base64

        import cv2

        img = image
        h, w = img.shape[:2]
        if w > max_w:
            s = max_w / w
            img = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 82])
        if not ok:
            return ""
        return base64.b64encode(buf).decode()
    except Exception:
        return ""


class Solver:
    def __init__(self, settings: dict[str, Any], data_dir):
        self.settings = settings
        self.weights = settings["source_weights"]
        self.enabled = settings["sources"]
        self.timeouts = settings["timeouts_ms"]
        self.cache = Cache(data_dir / "qa_cache.json")
        self.bank = Bank(data_dir / "bank.db")
        self._llm_client = None
        self._session = None
        self._lang = "id" if settings.get("language") == "id" else "en"

    def warmup(self) -> None:
        self._get_session()
        if self.enabled.get("websearch"):
            threading.Thread(target=self._warm_websearch, daemon=True).start()
        if self.enabled.get("wikipedia"):
            threading.Thread(target=self._warm_wikipedia, daemon=True).start()
        if self.enabled.get("llm"):
            threading.Thread(target=self._warm_llm, daemon=True).start()
        if self.enabled.get("vision"):
            threading.Thread(target=self._warm_vision, daemon=True).start()

    def _warm_websearch(self) -> None:
        try:
            from ddgs import DDGS
            with DDGS() as d:
                list(d.text("warmup", max_results=1))
        except Exception:
            pass

    def _warm_wikipedia(self) -> None:
        try:
            self._get_session().get(
                f"https://{self._lang}.wikipedia.org/w/api.php",
                params={"action": "query", "meta": "siteinfo", "format": "json"},
                timeout=2,
            )
        except Exception:
            pass

    def _warm_llm(self) -> None:
        try:
            client = self._get_llm_client()
            if client:
                client.chat.completions.create(
                    model=self.settings["llm"]["model"],
                    messages=[{"role": "user", "content": "hi"}],
                    max_tokens=1,
                    temperature=0.0,
                )
        except Exception:
            pass

    def _warm_vision(self) -> None:
        try:
            client = self._get_llm_client()
            model = (
                self.settings["llm"].get("vision_model")
                or "meta-llama/llama-4-scout-17b-16e-instruct"
            )
            if client:
                client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": "hi"}],
                    max_tokens=1,
                    temperature=0.0,
                )
        except Exception:
            pass

    def _get_session(self):
        if self._session is None:
            import requests
            s = requests.Session()
            s.headers.update({"User-Agent": "Dave-QuizAssistant/0.1"})
            self._session = s
        return self._session

    # ---- public API ---------------------------------------------------

    def solve(
        self,
        question: str,
        options: dict[str, str] | None = None,
        question_type: str | None = None,
        image=None,
    ) -> Suggestion:
        """Cari jawaban sesuai tipe soal: mcq, open, atau ordering.

        Strategi: cache/bank (instan) -> VISION (screenshot, ~0.5s, akurat untuk
        semua tipe termasuk gambar/emoji) -> baru fallback ke pipeline teks.
        """
        options = sanitize_options(options or {})
        qtype = question_type or classify_question(question, options)
        t0 = time.perf_counter()
        answers: list[SourceResult] = []
        is_mcq = qtype == "mcq" and len(options) >= 2

        if self.enabled.get("cache"):
            r = self._src_cache(question)
            if r:
                answers.append(r)
        if self.enabled.get("bank"):
            r = self._src_bank(question)
            if r:
                answers.append(r)

        # Cache/bank hit with high confidence -> langsung pakai.
        best = self._best_answer(answers, options if is_mcq else None)
        if best and best.confidence >= 0.9:
            return self._finalize(best, answers, options, qtype, t0)

        # Vision-first: the screenshot answers every quiz type accurately in
        # ~0.5s. Only fall through to the (slower, text-only) network pipeline
        # if vision is unavailable or fails.
        if image is not None and self.enabled.get("vision"):
            v = self._src_llm_vision(question, options, qtype, image)
            if v and v.text:
                answers.append(v)
                return self._finalize(v, answers, options, qtype, t0)

        # Trust a direct-answer source (LLM/cache/bank) as soon as it lands.
        # Web/Wikipedia snippets are noisy, so they must NOT preempt the LLM —
        # we only early-exit on high confidence, which only trusted sources
        # reach. Otherwise the loop keeps collecting until the LLM answers or
        # the budget runs out, then picks the best available.
        trusted = {"llm", "cache", "bank"}
        for res in self._stream_network(question, options, qtype, t0):
            if res.kind == "answer" and res.text:
                answers.append(res)
                if res.source in trusted and res.confidence >= 0.8:
                    break
            elif res.kind == "scores" and res.option_scores and is_mcq:
                label = max(res.option_scores, key=res.option_scores.get)
                if res.option_scores[label] >= 0.5 and label in options:
                    answers.append(SourceResult(
                        res.source, "answer", options[label],
                        confidence=res.confidence * res.option_scores[label],
                    ))

        best = self._best_answer(answers, options if is_mcq else None)
        if not best and is_mcq:
            best = self._best_option_answer(answers, options)

        if not best:
            return Suggestion(
                None, "", 0.0, "none", {},
                int((time.perf_counter() - t0) * 1000), options,
                question_type=qtype,
            )

        return self._finalize(best, answers, options, qtype, t0)

    def remember(self, question: str, answer_label: str, answer_text: str) -> None:
        self.cache.put(question_hash(question), {"label": answer_label, "text": answer_text})

    def _finalize(
        self, best: SourceResult, all_results: list[SourceResult],
        options: dict[str, str], qtype: str, t0: float,
    ) -> Suggestion:
        answer = _clean_answer(best.text, max_len=200 if qtype == "ordering" else 120)
        label = None
        option_text = ""
        scores = self._option_scores(answer, options) if options else {}

        if qtype == "ordering":
            answer = self._format_ordering(answer, options)
        elif qtype == "mcq" and options:
            label, option_text = self._resolve_to_option(answer, options)
            if not label:
                label, option_text = self._best_scored_option(all_results, options)
            if not label and scores:
                top = max(scores, key=scores.get)
                if scores[top] >= 0.35:
                    label, option_text = top, options[top]
            if label:
                answer = option_text

        return Suggestion(
            label=label,
            answer_text=answer,
            confidence=round(best.confidence, 3),
            source=best.source,
            scores=scores,
            elapsed_ms=int((time.perf_counter() - t0) * 1000),
            parsed_options=options,
            option_text=option_text or (options.get(label, "") if label else ""),
            question_type=qtype,
        )

    @staticmethod
    def _format_ordering(answer: str, options: dict[str, str]) -> str:
        """Normalisasi jawaban urutan ke format 'A → B → C'."""
        parts = re.split(r"[,→|>\n]+", answer)
        parts = [p.strip().strip("0123456789.)") for p in parts if p.strip()]
        if not parts:
            return answer
        # Cocokkan ke nama item di opsi (OCR) bila ada.
        if options:
            opt_names = list(options.values())
            matched: list[str] = []
            for p in parts:
                best = max(opt_names, key=lambda o: fuzz.token_set_ratio(p.lower(), o.lower()))
                if fuzz.token_set_ratio(p.lower(), best.lower()) >= 70:
                    matched.append(best)
                else:
                    matched.append(p)
            parts = matched
        return " → ".join(parts)

    @staticmethod
    def _clean_options(options: dict[str, str]) -> dict[str, str]:
        return sanitize_options(options)

    @staticmethod
    def _resolve_to_option(answer: str, options: dict[str, str]) -> tuple[str | None, str]:
        if not answer or not options:
            return None, ""
        al = answer.lower().strip()
        for label, opt in options.items():
            if al == opt.lower().strip() or al == label.lower():
                return label, opt
        best_label, best_score = None, 0.0
        for label, opt in options.items():
            score = fuzz.token_set_ratio(al, opt.lower()) / 100.0
            if score > best_score:
                best_score, best_label = score, label
        if best_label and best_score >= 0.55:
            return best_label, options[best_label]
        return None, ""

    @staticmethod
    def _best_scored_option(
        results: list[SourceResult], options: dict[str, str],
    ) -> tuple[str | None, str]:
        merged: dict[str, float] = {}
        for r in results:
            if r.kind != "scores" or not r.option_scores:
                continue
            for label, val in r.option_scores.items():
                if label in options:
                    merged[label] = max(merged.get(label, 0.0), val * r.confidence)
        if not merged:
            return None, ""
        top = max(merged, key=merged.get)
        if merged[top] <= 0:
            return None, ""
        return top, options[top]

    @staticmethod
    def _best_option_answer(
        results: list[SourceResult], options: dict[str, str],
    ) -> SourceResult | None:
        label, text = Solver._best_scored_option(results, options)
        if not label:
            return None
        return SourceResult("wiki_options", "answer", text, confidence=0.6)

    @staticmethod
    def _best_answer(
        results: list[SourceResult], options: dict[str, str] | None = None,
    ) -> SourceResult | None:
        candidates = [r for r in results if r.kind == "answer" and r.text.strip()]
        if not candidates:
            return None
        if options:
            # PG: utamakan jawaban yang benar-benar salah satu opsi.
            matched = [r for r in candidates if Solver._resolve_to_option(r.text, options)[0]]
            if matched:
                return max(matched, key=lambda r: r.confidence)
        return max(candidates, key=lambda r: r.confidence)

    @staticmethod
    def _match_option_hint(answer: str, options: dict[str, str]) -> str | None:
        if not answer or not options:
            return None
        best_label, best_score = None, 0.0
        for label, opt in options.items():
            score = fuzz.token_set_ratio(answer.lower(), opt.lower()) / 100.0
            if score > best_score:
                best_score, best_label = score, label
        return best_label if best_score >= 0.55 else None

    @staticmethod
    def _option_scores(answer: str, options: dict[str, str]) -> dict[str, float]:
        if not options:
            return {}
        return {
            label: fuzz.token_set_ratio(answer.lower(), opt.lower()) / 100.0
            for label, opt in options.items()
        }

    # ---- network sources ----------------------------------------------

    def _stream_network(
        self, question: str, options: dict[str, str], qtype: str, t0: float,
    ):
        jobs: dict[str, Callable[[], SourceResult | None]] = {}
        if self.enabled.get("llm"):
            if qtype == "ordering":
                jobs["llm"] = lambda: self._src_llm_ordering(question, options)
            elif qtype == "mcq" and len(options) >= 2:
                jobs["llm"] = lambda: self._src_llm_mcq(question, options)
            else:
                jobs["llm"] = lambda: self._src_llm_open(question)
        if self.enabled.get("wikipedia"):
            jobs["wikipedia"] = lambda: self._src_wikipedia_answer(question)
        if self.enabled.get("websearch"):
            jobs["websearch"] = lambda: self._src_websearch_answer(question)
        if self.enabled.get("wiki_options") and qtype == "mcq" and len(options) >= 2:
            jobs["wiki_options"] = lambda: self._src_wiki_options(question, options)

        if not jobs:
            return

        budget = self.timeouts.get("total_solve", 5000) / 1000.0
        ex = ThreadPoolExecutor(max_workers=len(jobs))
        try:
            pending = {ex.submit(fn) for fn in jobs.values()}
            while pending:
                remaining = budget - (time.perf_counter() - t0)
                if remaining <= 0:
                    break
                done, pending = wait(pending, timeout=remaining, return_when=FIRST_COMPLETED)
                if not done:
                    break
                for fut in done:
                    try:
                        res = fut.result()
                    except Exception:
                        res = None
                    if res:
                        yield res
        finally:
            ex.shutdown(wait=False, cancel_futures=True)

    def _src_cache(self, question: str) -> SourceResult | None:
        hit = self.cache.get(question_hash(question))
        if hit and hit.get("text"):
            return SourceResult("cache", "answer", hit["text"], confidence=1.0)
        return None

    def _src_bank(self, question: str) -> SourceResult | None:
        row = self.bank.best_match(question)
        if row:
            answer, score = row
            if score >= 0.72:
                return SourceResult("bank", "answer", answer, confidence=min(1.0, score))
        return None

    def _src_llm_vision(
        self, question: str, options: dict[str, str], qtype: str, image,
    ) -> SourceResult | None:
        """Answer using the on-screen SCREENSHOT via a multimodal LLM.

        This is the primary engine: it reads the question, the illustration
        (anime frame, emoji, map, etc.), AND the answer buttons directly, so it
        works for every quiz.com type — including image-only questions that text
        OCR cannot possibly answer.
        """
        client = self._get_llm_client()
        if client is None or image is None:
            return None
        b64 = _encode_frame(image)
        if not b64:
            return None

        if qtype == "ordering":
            task = (
                "This is an ordering question. Reply with ONLY the items in the "
                "correct order, separated by commas, using their exact names."
            )
        elif qtype == "mcq" or len(options) >= 2:
            task = "Reply with ONLY the exact text of the correct answer choice."
        else:
            task = (
                "This is a fill-in / typing question. Reply with ONLY the short "
                "answer to type."
            )

        hint = ""
        if question and len(question) >= 4:
            hint += f"\nQuestion text (OCR, may be imperfect): {question}"
        if options:
            hint += "\nDetected choices: " + " | ".join(options.values())

        prompt = (
            "You are helping answer a quiz question shown in this quiz.com "
            "screenshot. Read the question, study the image, and read the answer "
            f"choices. {task} No explanation, no quotes, no extra words.{hint}"
        )
        model = (
            self.settings["llm"].get("vision_model")
            or "meta-llama/llama-4-scout-17b-16e-instruct"
        )
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    ],
                }],
                max_tokens=60 if qtype == "ordering" else 32,
                temperature=0.0,
            )
            raw = _clean_answer(
                resp.choices[0].message.content or "",
                max_len=200 if qtype == "ordering" else 90,
            )
            if raw:
                return SourceResult("vision", "answer", raw, confidence=0.93)
        except Exception:
            return None
        return None

    def _src_llm_mcq(self, question: str, options: dict[str, str]) -> SourceResult | None:
        client = self._get_llm_client()
        if not client or len(options) < 2:
            return None
        lines = "\n".join(f"- {v}" for v in options.values())
        # Ask for the answer TEXT (not a letter). This is robust to imperfect
        # option OCR: even if one button was mis-read or missed, the LLM still
        # returns the correct answer text, and we fuzzy-match it to the buttons
        # actually detected on screen (or show it verbatim as a fallback).
        prompt = (
            "Answer this multiple-choice quiz question. "
            "Reply with ONLY the exact text of the correct answer. "
            "No letter, no explanation.\n\n"
            f"Question: {question}\n\n"
            f"Options:\n{lines}\n\n"
            "Correct answer:"
        )
        try:
            resp = client.chat.completions.create(
                model=self.settings["llm"]["model"],
                messages=[{"role": "user", "content": prompt}],
                max_tokens=24,
                temperature=0.0,
            )
            raw = _clean_answer(resp.choices[0].message.content or "", max_len=80)
            if not raw:
                return None
            # Normalize to the exact on-screen option text when it clearly maps
            # to one, so the overlay shows what the button says.
            best_txt, best_score = raw, 0.0
            for txt in options.values():
                sc = fuzz.token_set_ratio(raw.lower(), txt.lower()) / 100.0
                if sc > best_score:
                    best_score, best_txt = sc, txt
            answer = best_txt if best_score >= 0.6 else raw
            return SourceResult("llm", "answer", answer, confidence=0.92)
        except Exception:
            return None
        return None

    def _src_llm_ordering(self, question: str, options: dict[str, str]) -> SourceResult | None:
        client = self._get_llm_client()
        if not client:
            return None
        items = ", ".join(options.values()) if options else ""
        prompt = (
            "You are solving an ordering quiz question. "
            "Put the items in the correct order as requested. "
            "Reply with ONLY the ordered list, items separated by commas. "
            "Use exact item names. No numbering, no explanation.\n\n"
            f"Question: {question}\n"
        )
        if items:
            prompt += f"\nItems: {items}\n"
        prompt += "\nCorrect order:"
        try:
            resp = client.chat.completions.create(
                model=self.settings["llm"]["model"],
                messages=[{"role": "user", "content": prompt}],
                max_tokens=80,
                temperature=0.0,
            )
            raw = _clean_answer(resp.choices[0].message.content or "", max_len=200)
            if raw:
                return SourceResult("llm", "answer", raw, confidence=0.9)
        except Exception:
            return None
        return None

    def _src_llm_open(self, question: str) -> SourceResult | None:
        client = self._get_llm_client()
        if not client:
            return None
        lang = "Indonesian" if self._lang == "id" else "English"
        prompt = (
            "You answer quiz questions with a short, direct answer. "
            "Reply with ONLY the answer text: a name, number, date, place, "
            "yes/no, or short phrase (max ~10 words). No explanation.\n"
            f"Prefer {lang} if the question is in {lang}, otherwise English.\n\n"
            f"Question: {question}\n\nAnswer:"
        )
        try:
            resp = client.chat.completions.create(
                model=self.settings["llm"]["model"],
                messages=[{"role": "user", "content": prompt}],
                max_tokens=48,
                temperature=0.0,
            )
            raw = _clean_answer(resp.choices[0].message.content or "")
            if raw:
                return SourceResult("llm", "answer", raw, confidence=0.88)
        except Exception:
            return None
        return None

    def _src_wikipedia_answer(self, question: str) -> SourceResult | None:
        timeout = self.timeouts.get("wikipedia", 3000) / 1000.0
        kws = _keywords(question)
        query = " ".join(kws[:8]) or question
        try:
            r = self._get_session().get(
                f"https://{self._lang}.wikipedia.org/w/api.php",
                params={
                    "action": "query", "generator": "search", "gsrsearch": query,
                    "gsrlimit": 2, "prop": "extracts", "exintro": 1,
                    "explaintext": 1, "exlimit": "max", "format": "json",
                },
                timeout=timeout,
            )
            pages = r.json().get("query", {}).get("pages", {})
            parts = []
            for p in pages.values():
                title = p.get("title", "")
                extract = (p.get("extract") or "").strip()
                if extract:
                    parts.append(f"{title}. {extract}")
            if not parts:
                return None
            snippet = parts[0]
            answer = self._extract_short_answer(question, snippet)
            if answer:
                return SourceResult("wikipedia", "answer", answer, confidence=0.65)
        except Exception:
            return None
        return None

    def _src_websearch_answer(self, question: str) -> SourceResult | None:
        try:
            from ddgs import DDGS
            snippets: list[str] = []
            with DDGS() as ddgs:
                for hit in ddgs.text(question, max_results=3):
                    snippets.append(f"{hit.get('title','')} {hit.get('body','')}")
            if not snippets:
                return None
            answer = self._extract_short_answer(question, snippets[0])
            if answer:
                return SourceResult("websearch", "answer", answer, confidence=0.55)
        except Exception:
            return None
        return None

    def _extract_short_answer(self, question: str, text: str) -> str:
        """Ambil frasa jawaban singkat dari cuplikan teks."""
        text = _clean_answer(text, max_len=500)
        if not text:
            return ""

        # Kalimat pertama sering mengandung jawaban fakta.
        first = text.split(".")[0].split("\n")[0].strip()
        first = re.sub(
            r"^(according to|berdasarkan|menurut)\s+[^,]+,\s*",
            "", first, flags=re.I,
        )

        # "X is the tallest..." -> ambil subjek atau objek kunci.
        m = re.search(
            r"(?:is|adalah|was|were)\s+(?:the\s+)?(.{3,80}?)(?:\.|,|$)",
            first, re.I,
        )
        if m:
            return _clean_answer(m.group(1))

        # Angka untuk soal hitung.
        if re.search(r"\d|berapa|how many|how much", question, re.I):
            nums = re.findall(r"\b\d+(?:[.,]\d+)?\b", text)
            if nums:
                return nums[0]

        return _clean_answer(first, max_len=80)

    def _src_wiki_options(self, question: str, options: dict[str, str]) -> SourceResult | None:
        q_kws = set(_keywords(question))
        if not q_kws:
            return None

        def score_option(text: str) -> float | None:
            try:
                r = self._get_session().get(
                    f"https://{self._lang}.wikipedia.org/api/rest_v1/page/summary/"
                    + requests_quote(text),
                    timeout=self.timeouts.get("wikipedia", 3000) / 1000.0,
                )
                if r.status_code != 200:
                    return None
                extract = (r.json().get("extract") or "").lower()
                if not extract:
                    return None
                return sum(1 for k in q_kws if k in extract) / len(q_kws)
            except Exception:
                return None

        scores: dict[str, float] = {}
        with ThreadPoolExecutor(max_workers=max(1, len(options))) as ex:
            futs = {ex.submit(score_option, txt): label for label, txt in options.items()}
            done, _ = wait(list(futs), timeout=self.timeouts.get("wikipedia", 3000) / 1000.0)
            for fut in done:
                label = futs[fut]
                try:
                    val = fut.result()
                except Exception:
                    val = None
                if val is not None:
                    scores[label] = val

        if not scores or max(scores.values(), default=0.0) <= 0.0:
            return None
        return SourceResult("wiki_options", "scores", "", confidence=0.75, option_scores=scores)

    def _get_llm_client(self):
        if self._llm_client is not None:
            return self._llm_client
        import os
        key = os.environ.get(self.settings["llm"].get("api_key_env", "DAVE_LLM_KEY"))
        if not key:
            return None
        try:
            from openai import OpenAI
            self._llm_client = OpenAI(
                base_url=self.settings["llm"].get("base_url"),
                api_key=key,
            )
        except Exception:
            self._llm_client = None
        return self._llm_client

    def close(self) -> None:
        self.cache.flush()
        self.bank.close()
