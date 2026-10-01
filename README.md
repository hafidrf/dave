# Dave: Quiz Suggest Assistant

**Dave** is a local desktop assistant for live quiz sessions. It watches your screen, detects questions automatically, consults multiple answer sources, and shows a concise suggestion in a small always-on-top overlay. **You remain in control**: Dave never clicks or types on your behalf.

Think of it as **live captions for quizzes**: continuous, light-weight screen awareness, with suggestions that appear when a new question settles on screen.

- **LIVE mode:** No manual region calibration: full-screen OCR and automatic question detection
- **Answer-first:** Suggests the substance of the answer (multiple-choice or free text), not merely a letter A–D
- **Suggest-only:** Assistance, not auto-play
- **No quiz-platform login:** Dave reads the screen only; it does not call quiz-site APIs or touch your account

> **Responsible use:** Dave is intended as a transparent study and practice aid for informal or training quizzes. Do not use it to cheat in formal examinations, graded assessments, or competitive events where external assistance is prohibited.

---

## How LIVE mode works

```
Dave watches the screen (light poll ~250 ms)
   -> screen changes, then settles (new slide/question)
   -> full-screen OCR; question text detected automatically
   -> multi-source solve (LLM / Wikipedia / web / cache / bank)
   -> overlay shows the SUGGESTED ANSWER (+ option hint if MCQ)
   -> you type or click yourself  ->  latency + source logged
```

Question detection relies on **cheap screen-change signals**. OCR runs only when the display has changed and then stabilised, similar in spirit to live captioning. Question text is inferred automatically (for example lines ending in `?`, or the most prominent heading near the top of the frame).

### Answer sources (fast → slow)

| # | Source | When it helps | Typical latency |
|---|--------|---------------|-----------------|
| 1 | Cache | Question seen or confirmed before | ~ms |
| 2 | Local bank (SQLite) | Fuzzy match against saved Q&A | ~ms |
| 3 | Wiki-options (inverse lookup) | Compare each option’s Wikipedia article to question keywords | ~0.5–2 s |
| 4 | Wikipedia (extracts) | Article summaries from search | ~0.5–2 s |
| 5 | Web search (DuckDuckGo) | Specific facts; no API key required | ~1–3 s |
| 6 | LLM (optional) | Reasoning / tie-break on hard items; needs an API key | ~1–2 s |

Network sources run **in parallel** under a shared deadline (`total_solve`, default 5 s), with **early exit**: once confidence is high enough (≥75%), Dave shows the suggestion without waiting for slower sources. Candidates are scored against options A–D when present; the highest-scoring option becomes the suggestion.

> **Strong default path:** *wiki-options* fetches a Wikipedia article for **each option**, then measures how well the question’s keywords appear in that article, especially effective for entity-style questions (people, places, works, scientific names). For close or ambiguous scores, enable the **LLM** as a tie-breaker.

---

## Installation (Windows, Python 3.11+)

```powershell
cd dave
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

OCR uses **RapidOCR (ONNX)**; a separate Tesseract install is not required.

---

## Setup and running

### 1. Start Dave (no calibration)

```powershell
python run_dave.py
```

Or double-click the **Dave** desktop/taskbar shortcut. The overlay appears with status such as `Live: watching screen…`. Open your quiz in the browser (or any full-screen quiz UI), join the session as usual, and leave Dave running. When a question appears, Dave reads it and shows a suggestion.

> **Optional legacy `region` mode:** to restrict OCR to a calibrated rectangle, set `"mode": "region"` in `config/settings.json` and run `python calibrate.py`. The default is **`auto`** (live, no calibration).

### 2. Optional settings

```powershell
copy config\settings.example.json config\settings.json
```

#### Enable the LLM (tie-breaker; may already be on in your settings)

Ensure `sources.llm` is `true`, then supply an API key in one of these ways:

**Option A: key file (convenient with a desktop shortcut):**

```powershell
copy config\llm_key.txt.example config\llm_key.txt
# open config\llm_key.txt and paste your key (remove the sample text)
```

**Option B: environment variable:**

```powershell
$env:DAVE_LLM_KEY = "gsk_...."
```

A free **Groq** key works well: https://console.groq.com/keys (keys usually begin with `gsk_`). For OpenAI or another OpenAI-compatible provider, adjust `llm.base_url` and `llm.model` in `config/settings.json`.

> Dave still runs without an LLM key; wiki-options, Wikipedia, and web search remain available; only the LLM tie-breaker is disabled.

### 3. Optional: import a question bank

```powershell
python tools\import_bank.py path\to\questions.csv    # columns: question,answer
python tools\import_bank.py path\to\questions.json   # [{"question":"...","answer":"..."}]
```

### 4. Run Dave

```powershell
python run_dave.py
```

The Dave overlay stays on top and can be dragged. Start your quiz session; when the host (or app) shows a question, Dave suggests an answer.

Close the overlay window (or press Ctrl+C in the terminal) to stop; a session summary is printed automatically.

---

## Quick verification (offline)

```powershell
python selftest.py
```

Exercises bank, cache, and scoring logic without screen capture or network calls.

---

## Benchmark output

Each session writes `data/sessions/<timestamp>.jsonl` (one line per question) and a terminal summary, for example:

```
====================================================
  Dave: Session summary
====================================================
  Questions answered : 20
  Average total      : 1840 ms  (median 1620 ms)
  Fastest            : 210 ms
  Slowest            : 4300 ms
  <= 3 seconds       : 18/20
  Avg OCR/parse      : 780 ms
  Avg solve          : 990 ms
  Sources used       : {'wikipedia': 9, 'bank': 6, 'cache': 3, 'websearch': 2}
====================================================
```

---

## Layout

```
dave/
├── calibrate.py          # optional screen-region calibration wizard
├── run_dave.py           # entry point (overlay + pipeline)
├── selftest.py           # offline solver/scoring checks
├── requirements.txt
├── config/
│   ├── regions.example.json
│   └── settings.example.json
├── tools/
│   └── import_bank.py    # import CSV/JSON into the bank
├── src/
│   ├── capture.py        # region screenshot (mss)
│   ├── parse.py          # OCR → question + options (RapidOCR)
│   ├── solve.py          # multi-source solver + scoring
│   ├── cache.py          # answer cache (JSON)
│   ├── bank.py           # Q&A bank (SQLite + fuzzy match)
│   ├── overlay.py        # suggestion overlay (tkinter)
│   ├── session_logger.py # logging + summary
│   ├── pipeline.py       # orchestrator + new-question detection
│   └── config.py         # configuration loader
└── data/                 # cache, bank, session logs (gitignored)
```

---

## Performance tips

- Prefer a **tight calibrated region** only if you use region mode; smaller frames mean faster, cleaner OCR.
- **Import a bank** of familiar questions for instant cache/bank hits.
- Lower `timeouts_ms.total_solve` when the quiz timer is strict (for example 2500).
- Enable the **LLM** only on a reliable connection; it is the slowest fallback.
- If suggestions arrive late, raise `poll_interval_ms` slightly or shrink the capture region.

---

## Training from Quiz.com references

- **Ethical:** Learn from **your** session logs and public title seeds only; do not scrape live private games.
- **Commands:**
  ```powershell
  python tools/seed_anime_bank.py
  python tools/train_from_sessions.py
  ```
- Restart Dave after training so the updated bank is loaded.
- Image quizzes still need vision; the bank helps when options OCR works.

### All categories

Quiz.com’s eight categories: Art & Literature, Entertainment, Geography, History, Languages, Science & Nature, Sports, Trivia.

```powershell
python tools/seed_all_categories.py
```
