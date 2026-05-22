# Project notes for Claude Code

`polis-recognizer` is a deterministic field extractor for Russian
КАСКО (and, later, ОСАГО) insurance policy PDFs. Library-only, no
LLM, no cloud. See `README.md` for the user-facing description and
`docs/roadmap-policyholder.md` for the active design + implementation
plan.

## Architecture pointers

- Top-level facade: `polis_recognizer/extractor.py` — `PolicyExtractor`
  and the `ExtractedPolicy` dataclass.
- Legacy → v2 mapper: `polis_recognizer/contract_field_extractor.py`.
- v2 pipeline: `polis_recognizer/extraction/` — `pipeline.run_extraction`
  is the single entry point; per-field parsers in
  `extraction/parsers/`; one parser = one `Candidate` winner per field.
- Registration order in `extraction/parsers/__init__.py` matters —
  `LEGACY_PARSERS` shapes the public dict, `ADDITIONAL_PARSERS` lands
  in `additional_fields`. New parsers go into the additional tuple.

## Test corpus (outside the repo)

A real-policy corpus lives at `../test_policies/` (sibling directory
of this repo). Layout:

```
test_policies/
  digital_pdf/batch_1 .. batch_7/   ~644 PDFs with usable text layer
  scanned_pdf/batch_1 .. batch_7/   ~255 PDFs that require OCR
  photos/                           staging — photos of paper polises
  in_unsorted/                      staging — unclassified intake
  out/<run_id>/                     historical evaluation runs
                                    (report.html, report.json,
                                     summary.csv, per_file/)
```

The owner may drop new batches into `batch_8`, `batch_9`, … or into
`in_unsorted/`. If you're about to run an evaluation, re-list the
corpus first — what you saw last session may be stale.

### Corpus handling rules

These are **non-negotiable**:

- **Never commit any file from `test_policies/` into this repo.** PDFs,
  extracted text, report.json — all of it contains real client PII
  (ФИО, паспорт, телефон, ИНН, адрес). The corpus path is gitignored
  by virtue of being outside the repo; do not work around that.
- **Never upload corpus files to third-party services.** No pastebins,
  no LLM tools, no online diff viewers, no diagram renderers, no
  share-link generators. Anything that leaves this machine is a leak.
- Synthetic fixtures for unit tests are built via `reportlab` (in
  `[test]` deps). Real corpus is for regression runs only, not for
  test fixtures.
- When pasting corpus content into the conversation for debugging,
  redact ФИО / passport / phone / address first — or work from the
  shape of the data, not the data itself.

## Build, test, conventions

- Python 3.11+. Test command: `pytest -v` from repo root.
- Lint: `ruff check .` (config in `pyproject.toml`).
- System deps: Tesseract (`tesseract-ocr` + `tesseract-ocr-rus`) and
  Poppler — needed for OCR fallback. CI installs them; locally on
  macOS use `brew install tesseract tesseract-lang poppler`.
- Docs and PR descriptions are in English to match repository
  conventions. Code identifiers stay in original form (Russian regex
  literals are fine where they reflect domain text).
- Versioning: `pyproject.toml` + `polis_recognizer/__init__.py` must
  stay in sync. Tagged releases (`vX.Y.Z`) trigger PyPI publish via
  `.github/workflows/publish.yml`; branch pushes only run tests.
