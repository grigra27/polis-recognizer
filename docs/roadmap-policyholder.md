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
