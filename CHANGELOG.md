# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] — 2026-05-22

Adds policyholder + contacts extraction. No breaking changes to the
existing seven legacy fields.

### Added

- **`ExtractedPolicy.policyholder`** — `{type, name, inn, ogrn, kpp,
  passport, birth_date}`. `type` is `"individual"` or
  `"legal_entity"`. Identifiers (`inn`, `ogrn`) are validated via
  the published ФНС checksum algorithms; invalid digit runs (common
  in OCR output) are rejected rather than emitted as
  confident-but-wrong values.
- **`ExtractedPolicy.policyholder_contacts`** — `{phones, emails,
  address, postal_code}`. Phones normalised to E.164
  (`+7XXXXXXXXXX`), emails lowercased and deduped, address returned
  as a raw string (КЛАДР/ФИАС normalisation intentionally out of
  scope), postal code first-digit-gated to 1–6.
- **`PolicyExtractor(extract_pii=True)`** — opt-in flag for
  passport and birth-date extraction. Off by default: with default
  settings `policyholder.passport` and `policyholder.birth_date`
  are always `None` even when the source text contains them, so
  the default output is safe to log / cache / persist without
  extra redaction work.
- New parser modules in `polis_recognizer/extraction/parsers/`:
  `policyholder_name`, `policyholder_type`, `policyholder_inn`,
  `policyholder_ogrn`, `policyholder_kpp`, `policyholder_phones`,
  `policyholder_emails`, `policyholder_address`,
  `policyholder_postal_code`, `policyholder_passport`,
  `policyholder_birth_date`.
- Shared helpers in `extraction/`: `policyholder_block.py`
  (block locator + table-anchor detector), `validators.py`
  (ИНН-10/12 + ОГРН-13/15 checksums), `dates.py`
  (Russian-aware date parser extracted from `policy_period.py`
  for reuse).

### Changed

- `is_complete` still gates on the seven legacy fields only. The
  two new fields are non-mandatory and don't affect it.
- `PolicyPeriodParser` now imports its date parser from
  `extraction/dates.py`. Behaviour unchanged.

### Tests

- 131 new tests across the 11 new parsers and the supporting
  modules. Total 190; legacy 59 unchanged.

## [0.2.0] — 2026-05-05

Security maintenance release. No API changes.

### Changed

- Bumped `pypdf` upper bound from `<6` to `<7` and the floor to
  `>=6.10.2`. This unblocks downstream consumers from the 5.x CVE
  backlog (CVE-2025-55197, CVE-2025-62707/-62708, CVE-2025-66019,
  CVE-2026-22690/-22691/-24688/-27024/-27025/-27026/-27628/-27888/
  -28351/-28804/-31826/-33123/-33699/-40260, plus four GHSA
  advisories). Public surface used here — `PdfReader`,
  `page.extract_text()` — is unchanged across 5.x → 6.x.
- Bumped `Pillow` upper bound from `<12` to `<13` and the floor to
  `>=12.2.0`. Closes 4 CVEs in the 11.x line (CVE-2026-25990/-40192/
  -42308/-42309). Only `PIL.Image` basics are used here, which were
  not affected by the 12.x removals.

### Why

`polis-recognizer 0.1.0` transitively pinned both packages below the
versions that ship the CVE fixes, which forced downstream projects
(notably `polishelper`) to suppress 26 advisories in their pip-audit
configs. This release lifts that constraint.

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

[0.3.0]: https://github.com/grigra27/polis-recognizer/releases/tag/v0.3.0
[0.2.0]: https://github.com/grigra27/polis-recognizer/releases/tag/v0.2.0
[0.1.0]: https://github.com/grigra27/polis-recognizer/releases/tag/v0.1.0
