# Dave — Quiz Suggest Assistant

Asisten lokal untuk sesi kuis (mis. boarding Jumat di Quiz.com). Bekerja seperti **Live Caption Chrome**: Dave **mengawasi layar otomatis**, **mendeteksi soal sendiri** (tanpa kalibrasi), **mencari jawaban dari banyak sumber**, lalu **menampilkan jawaban** di overlay kecil. **Anda tetap yang mengetik/mengklik jawaban.**

- **Mode LIVE:** Tanpa kalibrasi — Dave OCR seluruh layar & deteksi teks soal otomatis
- **Answer-first:** Menjawab isi soal (PG maupun isian bebas), tidak bergantung opsi A/B/C/D
- **Suggest-only:** Bukan auto-klik
- **Login:** Tidak perlu — Dave hanya membaca layar, tidak menyentuh akun/API Quiz.com

> Etis: Dave dipakai transparan sebagai alat bantu di sesi fun internal, bukan untuk curang di kompetisi/ujian.

---

## Cara Kerja (Mode LIVE)

```
Dave awasi layar (poll ringan ~250ms)
   -> layar berubah lalu diam (slide baru muncul)
   -> OCR seluruh layar, deteksi teks SOAL otomatis
   -> solve multi-source (LLM / Wikipedia / web / cache / bank)
   -> overlay tampilkan JAWABAN (+ petunjuk opsi jika PG)
   -> Anda ketik / klik sendiri  ->  log latency + sumber
```

Deteksi soal memakai **perubahan layar** (murah), OCR hanya jalan saat slide baru muncul & diam — jadi ringan, mirip live caption. Teks soal dikenali otomatis (baris yang diakhiri `?` atau teks font terbesar di bagian atas).

### Sumber jawaban (cepat -> lambat)

| # | Sumber | Kapan | Perkiraan |
|---|--------|-------|-----------|
| 1 | Cache | Soal pernah muncul/dikonfirmasi | ~ms |
| 2 | Bank lokal (SQLite) | Fuzzy match soal lama | ~ms |
| 3 | Wiki-options (inverse lookup) | Cek artikel Wikipedia tiap opsi vs kata kunci soal | ~0.5–2 dtk |
| 4 | Wikipedia (extracts) | Ringkasan artikel hasil pencarian | ~0.5–2 dtk |
| 5 | Web search (DuckDuckGo) | Fakta spesifik, tanpa API key | ~1–3 dtk |
| 6 | LLM (opsional) | Reasoning / tie-breaker soal sulit; butuh API key | ~1–2 dtk |

Sumber jaringan berjalan **paralel** dengan batas waktu total (`total_solve`, default 5 dtk) dan **early-exit**: begitu bukti sudah cukup yakin (>=75%), Dave langsung menampilkan suggestion tanpa menunggu sumber lambat. Setiap kandidat dicocokkan ke opsi A/B/C/D; opsi dengan skor tertinggi jadi suggestion.

> **Sumber andalan:** *wiki-options* mencari artikel Wikipedia untuk **tiap opsi**, lalu mengukur seberapa banyak kata kunci soal muncul di artikel itu — sangat akurat untuk soal entitas (planet, tokoh, negara). Untuk soal sulit/ambigu (skor mepet), aktifkan **LLM** sebagai tie-breaker.

---

## Instalasi (Windows, Python 3.11+)

```powershell
cd dave
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

OCR memakai **RapidOCR (ONNX)** — tidak perlu install Tesseract terpisah.

---

## Setup & Menjalankan

### 1. Jalankan Dave (tanpa kalibrasi)

```powershell
python run_dave.py
```

Atau **double-klik ikon Dave** di desktop/taskbar. Overlay "Dave" muncul dengan status `Live — mengawasi layar...`. Buka `quiz.com`, join PIN + nama Anda, dan biarkan Dave mengawasi. Saat soal muncul, Dave otomatis membaca & menampilkan jawaban.

> **Mode `region` (opsional, legacy):** kalau mau area terbatas hasil kalibrasi manual, set `"mode": "region"` di `config/settings.json` lalu jalankan `python calibrate.py`. Default sekarang **`auto`** (live, tanpa kalibrasi).

### 2. (Opsional) Setelan

```powershell
copy config\settings.example.json config\settings.json
```

#### Aktifkan LLM (tie-breaker soal sulit — sudah ON di `settings.json`)

`sources.llm` sudah `true`. Tinggal isi API key (salah satu cara):

**Cara 1 — file key (paling mudah, cocok untuk ikon desktop):**

```powershell
copy config\llm_key.txt.example config\llm_key.txt
# lalu buka config\llm_key.txt, tempel key Anda (hapus contoh)
```

**Cara 2 — environment variable:**

```powershell
$env:DAVE_LLM_KEY = "gsk_...."
```

Dapatkan key **Groq gratis**: https://console.groq.com/keys (key diawali `gsk_`). Untuk OpenAI/provider lain, ubah `llm.base_url` & `llm.model` di `config/settings.json`.

> Tanpa key pun Dave tetap jalan — sumber lain (wiki-options, wikipedia, websearch) tetap dipakai; hanya tie-breaker LLM yang nonaktif.

### 3. (Opsional) Impor bank soal lama

```powershell
python tools\import_bank.py path\to\soal.csv    # kolom: question,answer
python tools\import_bank.py path\to\soal.json   # [{"question":"...","answer":"..."}]
```

### 4. Jalankan Dave

```powershell
python run_dave.py
```

Overlay "Dave" muncul (always-on-top, bisa di-drag). Buka `quiz.com` -> join PIN + nama Anda -> mulai sesi. Saat host menampilkan soal, Dave otomatis membaca dan menampilkan suggestion.

Tutup jendela overlay (atau Ctrl+C di terminal) untuk berhenti — ringkasan sesi otomatis dicetak.

---

## Verifikasi Cepat (tanpa layar/jaringan)

```powershell
python selftest.py
```

Menguji logika bank, cache, dan scoring secara offline.

---

## Output Benchmark

Tiap sesi menghasilkan `data/sessions/<timestamp>.jsonl` (satu baris per soal) dan ringkasan di terminal:

```
====================================================
  Dave — Ringkasan Sesi
====================================================
  Soal terjawab   : 20
  Rata-rata total : 1840 ms  (median 1620 ms)
  Tercepat        : 210 ms
  Terlambat       : 4300 ms
  <= 3 detik      : 18/20
  Rata OCR/parse  : 780 ms
  Rata solve      : 990 ms
  Sumber dipakai  : {'wikipedia': 9, 'bank': 6, 'cache': 3, 'websearch': 2}
====================================================
```

---

## Struktur

```
dave/
├── calibrate.py          # wizard kalibrasi region layar
├── run_dave.py           # entrypoint (overlay + pipeline)
├── selftest.py           # tes offline solver/scoring
├── requirements.txt
├── config/
│   ├── regions.example.json
│   └── settings.example.json
├── tools/
│   └── import_bank.py     # impor CSV/JSON ke bank
├── src/
│   ├── capture.py         # screenshot region (mss)
│   ├── parse.py           # OCR -> pertanyaan + opsi (RapidOCR)
│   ├── solve.py           # solver multi-source + scoring
│   ├── cache.py           # cache jawaban (JSON)
│   ├── bank.py            # bank Q&A (SQLite + fuzzy)
│   ├── overlay.py         # overlay suggest (tkinter)
│   ├── session_logger.py  # log + ringkasan
│   ├── pipeline.py        # orchestrator + deteksi soal baru
│   └── config.py          # loader konfigurasi
└── data/                  # cache, bank, log sesi (gitignored)
```

---

## Tuning Kecepatan

- **Region ketat** saat kalibrasi (jangan full-screen) → OCR lebih cepat & akurat.
- **Impor bank** soal-soal lama → banyak yang jadi hit instan.
- Kecilkan `timeouts_ms.total_solve` bila timer kuis ketat (mis. 2500).
- Nyalakan **LLM** hanya jika koneksi cepat; ia fallback paling lambat.
- Jika suggestion telat/ketinggalan, naikkan `poll_interval_ms` deteksi atau perkecil region.
```
