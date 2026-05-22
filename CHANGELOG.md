# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.2] — 2026-05-22

Precision pass over the policyholder + contacts feature. The output
is meaningfully cleaner on real lizinging / corporate КАСКО polises
— the price is a drop in raw coverage numbers, because most of the
"coverage" we were claiming in 0.3.1 was actually broker / lizingodatel
data leaking into the policyholder slots.

### Fixed

- **Slash-combined anchor labels.** `СТРАХОВАТЕЛЬ / ЛИЗИНГОПОЛУЧАТЕЛЬ:`
  (and similar with `ВЫГОДОПРИОБРЕТАТЕЛЬ` / `ЗАЛОГОДАТЕЛЬ` /
  `ГРУЗОПОЛУЧАТЕЛЬ`) is now absorbed by the anchor regex as a single
  unit, so the block span starts AFTER the combined label rather
  than in the middle of " / ЛИЗИНГОПОЛУЧАТЕЛЬ:". Eliminates names
  like `"/ ЛИЗИНГОПОЛУЧА"`.
- **Bank-details name reject.** Captures starting with bank tokens
  (`р/с`, `к/с`, `БИК`, `БАНК`, `кор. счет`) or with a numbered
  contract clause (`10.2 …`, `1) …`) are no longer emitted as
  policyholder names. Catches cases where pdfplumber column
  flattening put the actual name on a different page and the line
  immediately after the anchor is bank details / a contract clause.
- **Row-grouped table parsing.** A new helper
  `policyholder_table_rows(table)` returns the rows of a pdfplumber
  table that start at the "Страхователь" label and end just before
  the next other-party label (Брокер / Страховщик /
  Выгодоприобретатель / Собственник / Лизингодатель /
  Залогодержатель / Контактное лицо / Представитель). Applied to
  the INN / OGRN / KPP / phones / emails / address / postal_code
  parsers. Stops broker contacts (`online@on-linebroker.ru`, the
  "г. Люберцы, Парковая, д.3" office address, etc.) from being
  attributed to the policyholder when both parties share the same
  table.

### Measured impact

Re-run on `digital_pdf/batch_1` (23 files), 0.3.1 → 0.3.2:

| Field | 0.3.1 | 0.3.2 | Note |
|---|---|---|---|
| `policyholder.name` | 95.7% | 82.6% | -3 files = removed garbage (`/ ЛИЗИНГОПОЛУЧА`, `10.2. Выплата…`) |
| `policyholder.type` | 78.3% | 73.9% | -1 file (type of a now-removed garbage name) |
| `contacts.phones` | 17.4% | 8.7% | -2 = broker phones removed |
| `contacts.emails` | 34.8% | 0.0% | every "email" in 0.3.1 was actually `online@on-linebroker.ru` |
| `contacts.address` | 95.7% | 73.9% | -5 = broker addresses removed |
| `contacts.postal_code` | 91.3% | 65.2% | -6 = broker-address indexes |

The headline-looking regressions are precision wins. Lizinging /
corporate КАСКО polises in batch_1 simply don't list policyholder
contact channels (those are on the corporate signature block, not
inside the policy table); 0.3.1 was incorrectly returning broker
contact data in those slots. 0.3.2 returns `None`, which is the
honest answer.

### Tests

10 new regression tests in
`tests/test_corpus_regressions_v032.py`. 222 tests pass total.

### Known limitations remaining

- **Address form-mask tail.** XLS form-mask polises join address with
  adjacent form fields ("422774, РТ, …, ул. Новая, д. 2 ДАТА РОЖД.
  21.02.1966 ПОЛ М ТЕЛ"). The address parser captures the whole
  joined string. Fix planned for 0.3.3 — apply `_ADDRESS_STOP_RE` to
  table cell values, same idea as the name table-cell stop in 0.3.1.

## [0.3.1] — 2026-05-22

Precision and coverage fixes for the policyholder + contacts feature,
derived from running 0.3.0 against the real-corpus
`digital_pdf/batch_1` (23 lizinging / corporate КАСКО polises).
No API changes; same shape, more accurate values.

### Fixed

- **Block-end stoppers** now include `Собственник` / `Лизингодатель` /
  `ОБРЕМЕНЕНИЕ` / `Залогодержатель`. Previously, the policyholder
  block in a lizinging contract could over-run into the lizingodatel
  section, picking up its ИНН/ОГРН/КПП and surfacing them as the
  policyholder's.
- **Bank-line guard** for `policyholder_ogrn` and `policyholder_kpp`:
  matches on a line that also contains banking markers (`р/с`, `к/с`,
  `БИК`, `кор. счет`) are rejected. Real corpus example: a line like
  `"р/с 40702… БИК 044030704, ОГРН 1074705005484, КПП 470501001"`
  carries the **lizingodatel's** bank details, not the policyholder's;
  before the guard, that ОГРН/КПП leaked into `policyholder.*`.
- **Strict anchor with prose detection.** `locate_policyholder_block`
  now scores each `Страхователь` occurrence and prefers labeled
  positions (start of line, after `1.` / `2. `) over prose
  continuations like *"Страхователь подтверждает, что Правила
  страхования получил…"*. A lowercase Cyrillic letter immediately
  following the anchor strongly downweights the match — that's the
  classic Russian verb signal of prose. The fallback path still
  returns the highest-scored anchor even when every match is prose,
  so coverage doesn't regress on prose-only documents.
- **Name table-cell truncation.** When pdfplumber surfaces a labeled
  cell containing form-mask join debris like
  `"ИП Саакян Самвел Аршакович ИНН 163400896388 РЕЗИДЕНТ РФ ДА НЕТ"`,
  the captured name now truncates at the first known subfield label
  (`ИНН` / `КПП` / `ОГРН` / `Адрес` / `Паспорт` …). The same
  stop-regex that already terminated in-text captures is reused.
- **Postal code from anchored tables.** `PolicyholderPostalCodeParser`
  falls back to scanning tables anchored on "Страхователь" when the
  text-block scan finds nothing. In XLS form-mask polises the address
  often lives only in the table layer, never in the text layer,
  which previously produced `policyholder_contacts.postal_code = None`
  despite the address containing a clear 6-digit index.

### Measured impact

Re-run on `digital_pdf/batch_1` (23 files):

| Field | 0.3.0 | 0.3.1 |
|---|---|---|
| `policyholder.type` | 60.9% | 78.3% |
| `policyholder.inn` | 47.8% | 65.2% |
| `policyholder.kpp` | 21.7% | 26.1% |
| `contacts.postal_code` | 47.8% | 91.3% |
| `policyholder.ogrn` | 17.4% | 13.0% * |

`*` `ogrn` count went down by one because the bank-line guard now
rejects the lizingodatel's ОГРН it used to (wrongly) emit on lizinging
contracts. The remaining ОГРН values are higher-precision.

### Tests

14 new regression tests (`tests/test_corpus_regressions_v031.py`)
covering each fix against synthetic versions of the corpus failure
modes. 204 tests pass total.

### Known limitations remaining

- **Broker email/address bleed** — when an "Страхователь" anchor
  exists in a table that also contains an insurance broker's contacts,
  the contact parsers can extract the broker's email
  (`online@on-linebroker.ru`) and office address instead of the
  policyholder's. Fix planned for 0.3.2 via table row-grouping.
- **Slash-combined labels** — `"СТРАХОВАТЕЛЬ / ЛИЗИНГОПОЛУЧАТЕЛЬ:"`
  on a single line with the content on a different page after
  pdfplumber column flattening produces names like `"/ ЛИЗИНГОПОЛУЧА"`.
  Anchor regex extension planned for 0.3.2.

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

[0.3.2]: https://github.com/grigra27/polis-recognizer/releases/tag/v0.3.2
[0.3.1]: https://github.com/grigra27/polis-recognizer/releases/tag/v0.3.1
[0.3.0]: https://github.com/grigra27/polis-recognizer/releases/tag/v0.3.0
[0.2.0]: https://github.com/grigra27/polis-recognizer/releases/tag/v0.2.0
[0.1.0]: https://github.com/grigra27/polis-recognizer/releases/tag/v0.1.0
