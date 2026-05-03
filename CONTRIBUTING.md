# Contributing

Thanks for your interest in `polis-recognizer`. The project is
maintained as a side-project, so reviews aren't instant — but PRs are
welcome.

## What contributions are most useful

The single most valuable contribution is a **parser pattern for an
insurer format we don't recognize yet**. The recognizer ships with
patterns for the major Russian KASKO insurers (АльфаСтрахование, СОГАЗ,
Чулпан, Ингосстрах, ВСК, АбсолютСтрахование, Росгосстрах). Other
insurers, or unusual templates from these, often don't extract well.

## How to report a new format

**Don't paste real policy PDFs in the issue tracker** — they're personal
data. Instead:

1. Open an issue titled `New format: <Insurer name>`.
2. Paste the **plain-text** extraction of the relevant rows
   (you can run it yourself with
   `from polis_recognizer import PolicyExtractor; print(PolicyExtractor().extract_from_pdf("polis.pdf"))`
   and copy the relevant text from the diagnostics, OR run
   `pdftotext` on the file).
3. List which fields are missing or wrong, with the expected values.
4. Anonymize anything that identifies a real client (insured name,
   address, VIN, policy number — replace with placeholders).

That's enough to write a regex/table pattern. We don't need the
original PDF.

## How to add a parser pattern

Each field has its own parser at
`polis_recognizer/extraction/parsers/<field>.py`. The structure is a
list of `(pattern_id, regex, pattern_strength, context_strength)`
tuples. Add yours, run the test suite, and open a PR.

Patterns should be **specific** — a pattern that matches "anything that
looks like a policy number" will produce false positives across the
corpus. Anchor on insurer-specific labels or layout artifacts when
possible.

## Testing

```bash
# Install dev dependencies
pip install -e ".[test]"

# Run tests
pytest
```

The test suite uses synthetic PDFs generated via `reportlab` — no real
policies are committed to the repo.

## Code style

```bash
ruff check polis_recognizer tests
ruff format polis_recognizer tests
```

## License

By contributing, you agree your contribution is licensed under the
project's [MIT License](LICENSE).
