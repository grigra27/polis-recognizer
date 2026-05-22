# Roadmap: policyholder + contacts

Target release: **0.3.0**.

Adds two new fields to `PolicyExtractor` output: `policyholder` (the
contracting party) and `policyholder_contacts` (channels for reaching
them). Downstream integrators (CRM, underwriting, claims) consistently
need both, and today they re-OCR the same PDFs we already parse to get
them.

## Output shape

```python
class Policyholder(TypedDict):
    type: Literal["individual", "legal_entity"]
    name: str                     # ФИО or organization name, as written
    inn: str | None               # 12 digits (individual) or 10 (legal)
    ogrn: str | None              # legal only — 13 digits, 15 for ИП
    kpp: str | None               # legal only — 9 digits
    birth_date: date | None       # individual only, PII-gated
    passport: PassportRef | None  # individual only, PII-gated

class PassportRef(TypedDict):
    series: str  # 4 digits
    number: str  # 6 digits

class PolicyholderContacts(TypedDict):
    phones: list[str]        # E.164, +7XXXXXXXXXX, deduped
    emails: list[str]        # lowercased, deduped
    address: str | None      # raw string, no component parsing
    postal_code: str | None  # 6 digits, when present in or near address
```

Both fields follow the existing `Candidate` + `Ranker` pattern — each
sub-field can produce multiple candidates with confidence scores, the
ranker picks per sub-field independently.

## Which contacts and why these four

| Contact | Why included | Notes |
|---|---|---|
| Phone | Appears on ~100% of policies; the primary channel for claims and renewals. | Multi-value: mobile + work are both common. |
| Email | ~60–80% of policies; the canonical channel for e-signed documents and electronic policies. | Multi-value possible but rare. |
| Address | Required by law on the policy; tied to territory/risk; needed for postal correspondence. | Returned **raw**, see "Address" below. |
| Postal code | Cheap byproduct of address parsing (6 digits, stable shape); useful for territory bucketing without full address parsing. | — |

**Out of scope for 0.3.0:** messenger handles (Telegram/WhatsApp —
practically never on Russian insurance PDFs), fax (effectively dead),
workplace/contact-person fields (mixes the страхователь with HR data
and confuses the schema).

## Extraction strategy

### Name + type

- Anchor on `Страхователь:` / `СТРАХОВАТЕЛЬ` (and the left-column
  table-cell variants — `pdfplumber` surfaces these as label cells).
- Detect organizational prefixes — `ООО`, `ОАО`, `ПАО`, `ЗАО`, `АО`,
  `ИП`, `НКО`, `АНО`, `ТСЖ` — and set `type="legal_entity"`. Otherwise
  attempt to match three Cyrillic words (фамилия + имя + отчество)
  for `type="individual"`. Failure to match either gives a
  low-confidence individual candidate (the ranker can be overridden by
  INN length, see below).
- Name is returned **as written** (preserve case and order). Splitting
  into ФИО components or normalizing to Title Case is the caller's
  job — the source-of-truth form varies by integration.

### INN

- Regex `\b\d{10}\b` (legal) or `\b\d{12}\b` (individual) near an
  `ИНН` token.
- **Validate via the official weighted-checksum algorithm** before
  accepting. This kills almost all false positives — random 10/12-digit
  runs (account numbers, OCR artefacts) virtually never pass the
  checksum.
- INN length disambiguates `type` when the name parser is uncertain:
  a valid 10-digit INN forces `legal_entity`, a valid 12-digit INN
  forces `individual`.

### ОГРН / ОГРНИП / КПП

- Anchor-driven (`ОГРН`, `КПП` tokens), digit-count gated:
  - ОГРН — 13 digits (legal), 15 digits (ИП); has a checksum, validate.
  - КПП — exactly 9 digits, no checksum.
- Only emitted when `type == "legal_entity"`. (For ИП, ОГРНИП goes in
  `ogrn` and `kpp` stays `None`.)

### Passport (PII-gated)

- Anchors: `Паспорт`, `серия`, `№`, `выдан`.
- Pattern: 4 digits + 6 digits, often separated by a space, `№`, or
  newline.
- Returned only when `extract_pii=True` is passed to the constructor.
  Default is `False`. See "PII handling" below.

### Birth date (PII-gated)

- Anchors: `Дата рождения`, `г.р.`, `род.`.
- Reuses the date parser already used for `policy_period`.
- PII-gated same as passport.

### Phone

- One normalizing regex covering the common formats on Russian
  policies:
  - `+7XXXXXXXXXX`, `8XXXXXXXXXX`, `7XXXXXXXXXX`
  - `+7 (XXX) XXX-XX-XX` and all dash/space/parenthesis variants
  - `(XXX) XXX-XX-XX` — assumed `+7` if exactly 10 digits after
    stripping
- Normalize to E.164 (`+7XXXXXXXXXX`) before storing — strip
  non-digits, replace a leading `8` with `+7`, validate total length
  is 11.
- Anchors raise confidence: `тел.`, `телефон`, `контактный`, `моб.`,
  `сотовый`. Phones without anchors are accepted at lower confidence
  only when they fall inside the страхователь block (proximity to the
  name anchor from "Name + type").
- Multi-value: dedupe by normalized form, preserve discovery order.

### Email

- RFC-lite regex: `[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}` —
  good enough for what appears in the wild. Cyrillic local parts and
  `.рф` domains are technically legal but vanishingly rare on
  insurance PDFs; defer to a later release if a real corpus shows
  them.
- Lowercase before storing.
- Anchors: `e-mail`, `email`, `эл. почта`, `почта:`.
- Multi-value: dedupe case-insensitively.

### Address

- Anchors: `Адрес`, `Место жительства`, `Место нахождения`,
  `Зарегистрирован по адресу`, `Юр. адрес`, `Почтовый адрес`.
- Capture from the anchor until the next labeled field, the end of
  the paragraph, or `\n\n`.
- Strip embedded newlines → single space; collapse runs of whitespace.
- **Do not parse into components.** КЛАДР / ФИАС-grade address
  normalization is a separate problem with its own corpus and
  reference data; lumping it into this library would balloon scope.

### Postal code

- A 6-digit run within or adjacent to the captured address string.
- When found, returned as a separate `postal_code` field — and left
  in the raw `address` string too, so the caller can choose either
  representation.

## PII handling

Phone, email, and address are operational contact data — any caller
processing a policy has a legal basis to handle them (152-ФЗ, ст. 6
ч. 1 п. 5: исполнение договора). These are extracted **by default**.

Passport and birth date are sensitive identifiers and aren't strictly
needed by most integrations (claims/CRM can key off ФИО + phone or
INN). Default to **off**, opt in explicitly:

```python
extractor = PolicyExtractor(extract_pii=True)
```

When `extract_pii=False` (the default), `policyholder.passport` and
`policyholder.birth_date` are always `None` even if those values are
present in the source text. This keeps the default output safe to
log, cache, and persist without extra redaction work.

## Evaluation

This work depends on the eval-harness roadmap item: without a labelled
corpus the recall/precision claims below can't be checked. Once that's
in place, the targets on the test corpus are:

| Field | Target precision | Target recall |
|---|---|---|
| `policyholder.name` | ≥ 0.95 | ≥ 0.85 |
| `policyholder.type` | ≥ 0.98 | ≥ 0.95 |
| `policyholder.inn` | ≥ 0.99 (checksum) | ≥ 0.80 |
| `phones` | ≥ 0.95 | ≥ 0.90 |
| `emails` | ≥ 0.98 | ≥ 0.70 |
| `address` | ≥ 0.85 | ≥ 0.80 |

Precision is prioritized over recall — a missing field is easier to
handle downstream than a wrong one, especially when the wrong value
is something operational like a phone number.

## Implementation plan

### Integration points

The v2 pipeline already has all the seams we need. Per file:

| File | Change |
|---|---|
| `extraction/parsers/<new>.py` | One parser per sub-field (see decomposition below). |
| `extraction/parsers/__init__.py` | Register new parsers in `ADDITIONAL_PARSERS`. Legacy tuple untouched. |
| `extraction/parsers/base.py` | Add `extract_pii: bool = False` to `ExtractionContext`. |
| `extraction/pipeline.py` | `run_extraction(..., extract_pii=False)` propagates to context. |
| `contract_field_extractor.py` | `extract_contract_fields(..., extract_pii=False)` propagates to `run_extraction`. |
| `extractor.py` `PolicyExtractor.__init__` | `extract_pii: bool = False` (constructor-level, see "PII gating" below). |
| `extractor.py` `ExtractedPolicy` | Two new optional dict fields. `is_complete` stays at 7 (new fields are non-mandatory). |
| `extractor.py` `_build_extracted_policy` | Compose `policyholder` and `policyholder_contacts` from `additional_fields`. |
| `extraction/validators.py` (new) | ИНН-10/12 + ОГРН-13/15 checksum validators. |
| `extraction/policyholder_block.py` (new) | Shared `locate_policyholder_block(ctx) → (start, end)` used by name/contacts parsers. |
| `extraction/dates.py` | Extract the `policy_period` date parser into a reusable helper. |

### Parser decomposition

One parser per sub-field — keeps independent ranking and diagnostics.
All land in `additional_fields`:

```
policyholder_name        → str
policyholder_type        → "individual" | "legal_entity"
policyholder_inn         → str (checksum-validated)
policyholder_ogrn        → str (checksum-validated, legal only)
policyholder_kpp         → str (legal only)
policyholder_phones      → list[str] (one Candidate, multi-value)
policyholder_emails      → list[str] (one Candidate, multi-value)
policyholder_address     → str (raw, no component parsing)
policyholder_postal_code → str (6 digits)
policyholder_passport    → {"series": str, "number": str}   [PII-gated]
policyholder_birth_date  → date                              [PII-gated]
```

11 parsers. The facade composes them into two dicts on the public API.

### Per sub-field extraction approach

**`policyholder_name`** — anchors `Страхователь:`, `СТРАХОВАТЕЛЬ`,
plus table-cell match via `ctx.tables` (label cell == "Страхователь").
Capture from anchor to next labeled field or end of line. Detect org
prefixes (`ООО`, `АО`, `ПАО`, `ЗАО`, `ОАО`, `ИП`, `НКО`, `АНО`, `ТСЖ`)
to mark legal-entity candidates. Bare 3-Cyrillic-word ФИО pattern as
fallback (low confidence).

**`policyholder_type`** — uses the same `locate_policyholder_block`
helper. Decision order:
1. Org prefix present in block → `legal_entity`, conf 0.9.
2. Valid ИНН-10 in block → `legal_entity`, conf 0.85.
3. Valid ИНН-12 in block → `individual`, conf 0.85.
4. Three-word ФИО in block → `individual`, conf 0.75.

The ИНН dependency duplicates a tiny ИНН regex + checksum call here —
parsers in v2 don't see each other's results, so dependent inference
goes through the shared validator module, not through parser ordering.

**`policyholder_inn`** — regex `(?<!\d)\d{10}(?!\d)|(?<!\d)\d{12}(?!\d)`
near an `ИНН` token (high conf) or anywhere (low conf). **No candidate
emitted unless the checksum passes** — this is critical for precision;
random 10/12-digit runs in OCR output are common.

**`policyholder_ogrn` / `policyholder_kpp`** — anchor-driven, length
gated (ОГРН 13/15, КПП 9). ОГРН checksum-validated. Only emit when an
org prefix or valid 10-digit ИНН lives within ±200 chars — without
this guard ОГРН of the **insurer** (in the signature block) leaks in.

**`policyholder_phones`** — three regex variants (`+7...`, `8...`,
bare 10-digit), normalize to E.164 (`+7XXXXXXXXXX`), validate length.
Anchors `тел`, `моб`, `сотовый` raise confidence; without anchor —
accept only inside the policyholder block. Multi-value: one Candidate
with `value=list[str]`, deduped, discovery order preserved.

**`policyholder_emails`** — `[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}`,
lowercased, deduped. Anchors `e-mail`, `email`, `эл. почта`. Without
anchor — only inside policyholder block. One Candidate, `value=list[str]`.

**`policyholder_address`** — anchors `Адрес`, `Место жительства`,
`Место нахождения`, `Зарегистрирован`, `Юр. адрес`, `Почтовый адрес`.
Capture until next labeled field (shared `_NEXT_LABEL_RE`), `\n\n`, or
250-char hard cap. Collapse whitespace; trim punctuation. **No
component parsing** (КЛАДР/ФИАС is a separate problem).

**`policyholder_postal_code`** — `\b\d{6}\b` inside captured address
or within ±60 chars. Filter leading `0` (Russian indices start 1–6).

**`policyholder_passport`** (PII-gated) — guard `if not ctx.extract_pii:
return []` first thing. Anchors `Паспорт`, `серия`. Pattern: 4 digits
(series, possibly `12 34`) + 6 digits (number).

**`policyholder_birth_date`** (PII-gated) — same guard. Anchors `Дата
рождения`, `г.р.`, `род.`. Date parser reused from `extraction/dates.py`.

### PII gating mechanism

Flag rides on `ExtractionContext`. End-to-end:

```python
# extraction/parsers/base.py
@dataclass
class ExtractionContext:
    raw: str
    normalized: NormalizedText
    layout: LayoutAnalyzer
    negation: NegationContext
    tables: List[...] = field(default_factory=list)
    extract_pii: bool = False   # NEW

# extraction/pipeline.py
def run_extraction(raw_text, *, correlation_id=None, tables=None,
                   extract_pii=False):
    ctx = ExtractionContext(..., extract_pii=extract_pii)

# contract_field_extractor.py
def extract_contract_fields(self, text, correlation_id=None, *,
                            tables=None, extract_pii=False):
    v2_result = run_extraction(text, ..., extract_pii=extract_pii)

# extractor.py
class PolicyExtractor:
    def __init__(self, *, extract_pii=False, ...):
        self._extract_pii = extract_pii
```

PII-gated parsers exit early:

```python
def parse(self, ctx):
    if not ctx.extract_pii:
        return []
    # ...
```

Invariant: with the default `extract_pii=False`, `policyholder.passport`
and `policyholder.birth_date` are always `None` even when present in
the source text. The flag lives on the constructor, not per-call:
configure once → safe everywhere.

### `ExtractedPolicy` integration

Two new dataclass fields between `repair_mode` and `extraction_method`:

```python
@dataclass
class ExtractedPolicy:
    # ... existing 7 fields ...
    policyholder: Optional[dict] = None
    policyholder_contacts: Optional[dict] = None
    # ... existing meta fields ...
```

Composer (module-level helpers in `extractor.py`):

```python
def _candidate_value(addl_entry):
    """Unpack the .value of a winning candidate dict, or None."""
    if not addl_entry or not isinstance(addl_entry, dict):
        return None
    return addl_entry.get("value")


def _build_policyholder(addl: dict, *, extract_pii: bool) -> Optional[dict]:
    name = _candidate_value(addl.get("policyholder_name"))
    inn  = _candidate_value(addl.get("policyholder_inn"))
    if name is None and inn is None:
        return None
    return {
        "type":       _candidate_value(addl.get("policyholder_type")),
        "name":       name,
        "inn":        inn,
        "ogrn":       _candidate_value(addl.get("policyholder_ogrn")),
        "kpp":        _candidate_value(addl.get("policyholder_kpp")),
        "passport":   _candidate_value(addl.get("policyholder_passport"))
                          if extract_pii else None,
        "birth_date": _candidate_value(addl.get("policyholder_birth_date"))
                          if extract_pii else None,
    }


def _build_contacts(addl: dict) -> Optional[dict]:
    phones  = _candidate_value(addl.get("policyholder_phones"))  or []
    emails  = _candidate_value(addl.get("policyholder_emails"))  or []
    address = _candidate_value(addl.get("policyholder_address"))
    postal  = _candidate_value(addl.get("policyholder_postal_code"))
    if not phones and not emails and address is None:
        return None
    return {
        "phones":      list(phones),
        "emails":      list(emails),
        "address":     address,
        "postal_code": postal,
    }
```

`is_complete` is left alone — it gates on the legacy seven fields,
the two new fields are non-mandatory.

### Tests

One file per parser, in the existing `tests/test_*.py` convention.
Entry point `run_extraction(text)` — matches `test_policy_number.py`.
Synthetic PDFs via `reportlab` (already in `[test]` deps).

```
tests/test_policyholder_name.py
tests/test_policyholder_type.py
tests/test_policyholder_inn.py            # incl. checksum-reject cases
tests/test_policyholder_ogrn.py
tests/test_policyholder_kpp.py
tests/test_policyholder_phones.py         # all formats → E.164 normalize
tests/test_policyholder_emails.py
tests/test_policyholder_address.py
tests/test_policyholder_postal_code.py
tests/test_policyholder_pii.py            # PII flag invariants
tests/test_pipeline.py                    # extended with policyholder smoke
```

A regression run on the real corpus (see "Corpus" below) is in
addition to unit tests, not a replacement for them.

### PR phasing

Sequential, each ~200–400 LOC + tests, each leaves the project
green and shippable:

| PR | Scope | Done when |
|---|---|---|
| **#1 Skeleton** | `extract_pii` plumbed end-to-end; new `ExtractedPolicy` fields default to `None`; empty composer helpers; signature tests. | Public API extended, behaviour unchanged. |
| **#2 Name + Type** | `PolicyholderNameParser`, `PolicyholderTypeParser`, `policyholder_block` helper; minimal validators module (ИНН length check for type disambiguation only). | `.name` and `.type` populated on synthetic + corpus fixtures. |
| **#3 ИНН / ОГРН / КПП** | Full `validators.py` with checksums; three parsers. | All three structural IDs extracted, checksum-validated. |
| **#4 Phones + Emails** | Two parsers, E.164 normaliser, multi-value handling. | `phones` / `emails` extracted and deduped. |
| **#5 Address + Postal code** | Two parsers, shared `_NEXT_LABEL_RE` stopper. | Raw address + postal code in output. |
| **#6 PII (Passport + Birth date)** | Two PII-gated parsers; `extract_pii` flag wired through `PolicyExtractor.__init__`; invariant tests. | Release 0.3.0 ships. |

### Corpus

The training/regression corpus lives **outside** the repo at
`../test_policies/` (sibling of `polis-recognizer/`). Layout:

- `digital_pdf/batch_1..batch_7/` — ~644 PDFs with usable text layer.
- `scanned_pdf/batch_1..batch_7/` — ~255 PDFs requiring OCR.
- `photos/`, `in_unsorted/` — staging areas.
- `out/<run_id>/` — historical evaluation runs (`report.html`,
  `report.json`, `summary.csv`, `per_file/`); useful as a baseline
  for the eval harness we'll need eventually but is not part of this
  roadmap.

Real-corpus rules — see `CLAUDE.md` at the repo root. The short
version: **never commit corpus files into the repo** (they contain
PII), **never upload them to third-party services** (LLM tools,
pastebins, share links). Use them locally for regression runs only.
