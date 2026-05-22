# polis-recognizer

A deterministic field extractor for Russian KASKO insurance policy PDFs.
Pulls 7 structured fields without LLMs — text-layer extraction
(`pypdf`) plus optional table-aware reading (`pdfplumber`), with
Tesseract OCR fallback for scanned policies.

> **Status: pre-stable (0.x).** API may change before 1.0.

## What it extracts

| Field | Type | Example |
|---|---|---|
| `policy_number` | `str` | `"AC524160804"` |
| `policy_period` | `{start, end}` (`date`) | `{"start": date(2025, 2, 27), "end": date(2026, 2, 26)}` |
| `franchise` | `{value, currency, absent}` | `{"value": 30000, "currency": "RUB", "absent": False}` |
| `limit` | `{value, currency}` | `{"value": 5525000, "currency": "RUB"}` |
| `premium` | `{value, currency}` | `{"value": 220000, "currency": "RUB"}` |
| `sum_type` | `"aggregate"` / `"non_aggregate"` | `"non_aggregate"` |
| `repair_mode` | `"dealer"` / `"service"` / `"cash"` | `"dealer"` |

## Quick start

```python
from polis_recognizer import PolicyExtractor

extractor = PolicyExtractor()
result = extractor.extract_from_pdf("/path/to/polis.pdf")

print(result.policy_number)
# → "AC524160804"
print(result.policy_period)
# → {"start": date(2025, 2, 27), "end": date(2026, 2, 26)}
print(result.franchise)
# → {"value": 30000.0, "currency": "RUB", "absent": False}
```

Input methods:

```python
extractor.extract_from_pdf("polis.pdf")
extractor.extract_from_bytes(pdf_bytes, filename="polis.pdf")
extractor.extract_from_text("сырой текст полиса")  # bypass PDF/OCR
```

## Installation

```bash
pip install polis-recognizer
```

### System dependencies

`polis-recognizer` shells out to Tesseract for OCR and to `poppler`
for PDF→image conversion. These are NOT pip-installable; install them
through your OS package manager.

**Linux (Debian/Ubuntu):**

```bash
sudo apt-get install -y tesseract-ocr tesseract-ocr-rus poppler-utils libgl1
```

**macOS (Homebrew):**

```bash
brew install tesseract tesseract-lang poppler
```

**Windows:** best-effort. Install
[Tesseract for Windows](https://github.com/UB-Mannheim/tesseract/wiki)
and add it to PATH; install Poppler binaries for PDF support. We don't
test on Windows in CI.

The Russian language pack (`tesseract-ocr-rus` / `tesseract-lang`) is
required — without it, OCR silently falls back to English and Cyrillic
documents come back as garbage. The library logs a CRITICAL warning at
import time if the pack is missing.

## How it works

The extractor runs three stages:

1. **PDF ingestion** — `PdfExtractionRouter` tries text-layer extraction
   first (pypdf for text, pdfplumber for tables on the same page). If
   the result is too short (fewer than 100 chars by default) or detected
   as glued/EDI-envelope text, it falls back to Tesseract OCR.
2. **Text normalization** — Unicode NFKC, NBSP stripping, hyphenated
   line-break healing, runs of multiple spaces collapsed.
3. **Field extraction** — 7 deterministic parsers (one per field) run
   regex + table-aware patterns and emit ``Candidate``s with confidence
   scores. A ranker picks the winner per field.

There's no LLM and no cloud dependency. Everything runs locally.

## PDF extractor choice

The default is ``"hybrid"`` — pypdf text plus pdfplumber tables in one
pass. Two alternatives:

| Option | When to use |
|---|---|
| `"hybrid"` (default) | Best for KASKO. pypdf preserves date/period text quality, pdfplumber's tables fix the column layout for limit/franchise/premium. |
| `"pypdf"` | Faster, no tables. Use when document quality is uniform and tables aren't needed. |
| `"pdfplumber"` | Fully layout-aware. Slower; on KASKO it slightly regresses date parsing. Use for table-heavy non-KASKO formats. |

```python
extractor = PolicyExtractor(pdf_extractor="pypdf")
```

## Supported insurer formats

The parser ships with patterns for these Russian insurers' KASKO
templates: АльфаСтрахование (XLS form-mask), СОГАЗ-АВТО, Чулпан,
Ингосстрах, ВСК, АбсолютСтрахование, Росгосстрах, СОГАЗ Diadoc-wrapped
PDFs. Recall on real-world KASKO corpora is ~50-65% per field; pulling
above that requires per-format parser additions.

If you have a policy from an insurer not on this list — see
[CONTRIBUTING.md](CONTRIBUTING.md) for how to add a parser pattern.

## Configuration

Constructor arguments:

```python
extractor = PolicyExtractor(
    ocr_language="rus+eng",       # Tesseract language string
    ocr_timeout_seconds=300,
    ocr_page_limit=50,
    ocr_max_text_size=500_000,
    pdf_extractor="hybrid",       # "pypdf" | "pdfplumber" | "hybrid"
    image_preprocessing="fallback", # "never" | "fallback" | "always"
    psm=None,                     # Tesseract --psm (None = auto)
    oem=None,                     # Tesseract --oem (None = auto)
    max_image_size_bytes=None,    # reject images larger than this
)
```

## License

[MIT](LICENSE) © Grigorii Grachev. Free for any use, including
commercial.

## Roadmap

**Next up (target 0.3.0): policyholder + contacts.** Adds two new
fields to the extractor output:

- `policyholder` — contracting party: name, individual/legal-entity
  type, INN/ОГРН/КПП, and (behind a PII opt-in flag) passport
  reference and birth date.
- `policyholder_contacts` — reachable channels: phone numbers
  (normalized to E.164 `+7XXXXXXXXXX`), email addresses, raw postal
  address, and postal code when present.

Full design — anchors, regex strategy, INN/ОГРН checksum validation,
PII gating, target precision/recall — in
[docs/roadmap-policyholder.md](docs/roadmap-policyholder.md).

**Further out:** ОСАГО support — the underlying field model already
accommodates it; only parser patterns need adding.

See [CHANGELOG.md](CHANGELOG.md) for release history.
