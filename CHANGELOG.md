# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] — 2026-05-03

Initial public release.

### Added

- `PolicyExtractor` facade for end-to-end PDF → 7 structured fields.
- `extract_from_pdf` / `extract_from_bytes` / `extract_from_text` entry points.
- 7 deterministic field parsers:
  - `policy_period`
  - `franchise`
  - `limit`
  - `repair_mode`
  - `premium`
  - `sum_type`
  - `policy_number`
- Three PDF extractor backends: `pypdf`, `pdfplumber`, `hybrid` (default).
  - Hybrid mode reuses pypdf text and pdfplumber tables in one pass for the
    best recall/latency trade-off on KASKO templates.
- Tesseract OCR fallback for scanned PDFs.
- OpenCV-based image preprocessing (`fallback` / `always` / `never` modes).
- Pre-built parser patterns for major Russian KASKO insurers:
  АльфаСтрахование XLS forms (5/3/5/2 and 5/3/7/2 numbers, branch-letter
  variants), СОГАЗ-АВТО (`SGZA…` policy numbers), Чулпан (OCR pipe
  tolerance in policy_number), Ингосстрах (legacy `RUR` currency code,
  prose-spaced premium label), ВСК (two-row КАСКО layout),
  АбсолютСтрахование (glued text-layer detection), Diadoc/Kontur EDI
  envelope detection.
- Lower-level `run_extraction(text, *, tables=None)` for use without a PDF.

### Notes

- API is pre-stable. Public dataclass shapes
  (`ExtractedPolicy`, `MonetaryField`, etc.) may change before 1.0.
- KASKO-only for now; ОСАГО support is on the roadmap.

[0.1.0]: https://github.com/grigra27/polis-recognizer/releases/tag/v0.1.0
