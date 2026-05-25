# Roadmap: known limitations after 0.3.4

After the corpus-driven iteration 0.3.0 → 0.3.4 (eleven fixes from
inspecting all seven `digital_pdf` batches, 644 files), the
`policyholder` + `contacts` feature reliably extracts clean values on
the majority of mainstream insurer templates. Per-batch name coverage
ranges 75.6%–100.0%, with the worst cases concentrated in **five
identifiable failure classes** that need bigger work than regex tuning
can deliver.

This document catalogues each class, the risk it poses if left alone,
and one or more proposed approaches to fix it. The implementation
order at the bottom is a suggestion; each item is independently
ship-able.

This roadmap deliberately scopes only to digital_pdf failure modes
known as of 0.3.4. OCR-pipeline (`scanned_pdf/*`) issues are out of
scope — those have their own surface and need a separate corpus pass.

---

## L1 — ВСК «Классика» 2-col `ФИО гражданина / наименование ИП, юр. лица` layout

### Symptom

ВСК polises with policy numbers like `25200V…` and `19056-полис КАСКО
…` use a two-column form where the labels sit *above* and *below* the
values, rather than to the left:

```
ФИО гражданина/                          ИНН  7446038068    E-mail kons-trade@mail.ru
Общество с ограниченной ответственностью "Консерв-трейд"
наименование ИП, юр. лица                КПП  745601001     Телефон +79127726289
Адрес страхователя: 455022, Челябинская обл, Магнитогорск г, …
```

Under 0.3.4 these files extract either nothing (`policyholder.* = None`)
or partial garbage:
- `contacts.address = 'страхователя'` (the second word of the label),
- `contacts.postal_code = '455000'` (caught from somewhere else),
- `contacts.phones = ['+73519228153']` (a different phone from the page).

`policyholder.name`, `policyholder.inn`, `policyholder.kpp`,
`contacts.emails` — all `None`.

### Affected files

~20 / 644 (~3%), concentrated in batches 2 (6 files), 4 (11 files),
plus a handful in batch_5 / batch_6.

### Risk if unfixed

ВСК is a top-5 КАСКО insurer. Downstream integrators that filter on
`policyholder.name != None` will silently miss this entire insurer's
policies. For renewal / NPS / CRM flows the loss is invisible — the
user just doesn't get a record. **The data IS present in the PDF**,
the extractor just doesn't recognise the layout. That's worse than
a layout we can't read at all (no expectation set).

### Proposed approaches

**A. Layout-aware sub-extractor (recommended)**
Detect the ВСК «Классика» template by document fingerprint (e.g.
presence of `САО «ВСК»` + `Полис №\s*25\d{3}V`-style number + the
characteristic `ФИО гражданина/` / `наименование ИП, юр. лица` pair).
When matched, dispatch to a `vsk_klassika_extractor.py` that reads
the table cell positions directly from `ctx.tables` instead of
guessing via text.

Trade-off: introduces an insurer-dispatcher layer (`extraction/insurer_profiles/`),
which is the right place to also park future per-insurer parsers
(Альфа XLS form-mask handling is already a de-facto profile, just
inlined). Effort: ~1-2 days for the framework + ВСК «Классика»
profile + tests.

**B. Anchor-extension on the same parsers**
Add `ФИО гражданина/` and `наименование ИП, юр. лица` to the
form-mask label list. When the anchor regex sees these labels with
empty content, walk *up* in the document (not just down) to find
the actual name on the previous logical line.

Trade-off: simpler, but fragile — the "look upward" heuristic risks
false matches on documents where the label appears for the insurer
or beneficiary block. Effort: ~half a day.

**C. Defer / document only**
Add a known-limitation entry to README, document the ВСК «Классика»
fingerprint so downstream consumers can detect and skip these files
explicitly.

Trade-off: zero effort but no value delivered.

### Decision

**(A) is the right long-term call.** ВСК won't be the last insurer
that needs bespoke handling — at minimum АльянсЛизинг form-mask
(already ad-hoc), Согаз SGZA, Чулпан, Ингосстрах would all benefit
from a profile registry. The framework pays for itself by L2.

---

## L2 — Lizingodatel identifier substitution

### Symptom

Lizinging contracts often render the policyholder block as:

```
СТРАХОВАТЕЛЬ ООО "Экология-Норд" ИНН
ЗАО «Альянс-Лизинг» ИНН 7825496985 РЕЗИДЕНТ РФ ДА НЕТ
```

The Страхователь's ИНН field is empty (the form was never filled in
for the lessee). The next line carries the *lessor's* (ЗАО «Альянс-
Лизинг») ИНН. The current parser picks the first ИНН it finds after
the strahovatel anchor, which is the lessor's.

Same pattern surfaces for ОГРН, КПП, and postal_code on these files.
0.3.4's bank-line guard catches the most blatant cases (where the
lessor's ИНН appears inside an `р/с …, БИК … ИНН 78… КПП 78…` line),
but the no-bank-line variant still leaks.

### Affected files

~21 / 644 (~3.3%), concentrated in batches 5/6 (АльянсЛизинг family).

### Risk if unfixed

This is **the most dangerous** of the five classes: we don't return
`None`, we return *wrong* identifiers. A downstream lookup of
`7825496985` as a counterparty in EGRUL returns "ЗАО Альянс-Лизинг" —
plausibly a customer name on a CRM screen, just not the *right*
customer. Foreign-key reconciliation breaks silently.

### Proposed approaches

**A. Name-proximity boundary (recommended for 0.4.0)**
The Страхователь block locator already knows where the name was
captured. Extend the INN / OGRN / KPP parsers to associate a digit
run only with the name that *immediately* precedes it, with no
intervening "ЗАО" / "ООО" / "АО" / "ПАО" / "ИП" prefix. If an
intervening org prefix appears, the digit run belongs to that other
entity.

Specifically: after `policyholder.name` is extracted, INN/OGRN/KPP
parsers should narrow their search window to `[name.span.end, next
org-prefix or block end]`.

Trade-off: requires inter-parser communication — currently parsers
are independent. Cleanest implementation: add a "primary candidate"
pass that publishes the name span into `ExtractionContext`, then
the identifier parsers read it. Some redesign of the pipeline.

Effort: ~1 day.

**B. Empty-field detector**
Detect the specific pattern `ИНН\s*$` / `ИНН\s*\n` (label without a
value) on the same line as the name, and refuse to attribute any
later ИНН to the policyholder if this pattern is present.

Trade-off: catches the common case but misses the variant where the
ИНН field is filled but with a placeholder like `--` or `_____`.
Easier to implement, lower coverage of the failure mode. Effort: a
few hours.

**C. Cross-check ИНН against name via EGRUL API**
Look up the extracted ИНН in EGRUL and verify it matches the
extracted name. If mismatch, set `policyholder.inn = None` and emit
a diagnostic.

Trade-off: introduces a network dependency (the library has been
fully offline since 0.1.0). Available as an opt-in `verify_inn=True`
flag, defaulting to off. Highest precision, real cost.

### Decision

**(A)** for 0.4.0. (B) as a fallback for cases where (A) doesn't
identify a clear org boundary. (C) as a separate opt-in feature for
integrations that have an EGRUL provider already.

---

## L3 — АльянсЛизинг "См. Особые условия" template

### Symptom

A subset of АльянсЛизинг АС-prefixed polices uses a template where
the Страхователь block contains only the name plus literal "См.
Особые условия" rows. The actual ИНН / ОГРН / КПП / phones / emails
are printed on a **different page** that pdfplumber column-flattens
into an unrelated text region.

```
СТРАХОВАТЕЛЬ: ООО "Алтын Яр"
Адрес: См. Особые условия
ИНН: См. Особые условия
…
[page 2 or further]
ИНН 7702543210  КПП 770201001  ОГРН 1147746234567  …
```

### Affected files

~25 / 644 (~3.9%) — mostly batch_6.

### Risk if unfixed

Same flavour as L1: we get the name, but everything else is `None`.
Downstream sees a "half-extracted" record — easier to detect than L2
(no wrong values), but coverage hit is real.

### Proposed approaches

**A. Multi-page block expansion (recommended)**
When the block locator's content ends with placeholder phrases
("См. Особые условия", "Уточняется в Особых условиях", "См. п. N",
"См. приложение"), expand the search globally — look for an ИНН /
ОГРН / КПП labeled value anywhere in the document that matches the
extracted name via shared org-prefix tokens.

Trade-off: relies on name uniqueness within the doc. The same
name's identifiers are unlikely to appear in the lessor / insurer
sections, so risk of cross-contamination is low. Effort: ~1 day.

**B. Section-keyed lookup**
Locate "Особые условия" section header, parse THAT section for the
policyholder's identifiers via the same anchor logic.

Trade-off: works only when the special-conditions section is in the
text layer (which it usually is on АльянсЛизинг АС forms). Won't
help when the data is in a different page's table layer. Effort:
half a day, but lower coverage of the failure mode than (A).

### Decision

**(A)** as the primary fix; (B) can be added later as a refinement
if (A) produces false positives.

---

## L4 — ВСК signature-only anchor

### Symptom

A subset of ВСК polices puts the Страхователь anchor only in the
signature block at the bottom of the document, alongside a disclaimer
("Страхователь подтверждает, что Правила страхования получил…"). The
actual policyholder data lives in a "Сведения о Страхователе" table
earlier in the document, which has no "Страхователь" label cell —
the column headers say things like "Полное наименование", "ИНН",
"Адрес юридический", and the rows contain the values.

The current block locator finds only the signature anchor; 0.3.4's
disclaimer-reject then correctly returns `None` rather than capturing
the disclaimer prose, but the actual data is never extracted.

### Affected files

~11 / 644 (~1.7%) in batch_4 + a smaller count in batch_3.

### Risk if unfixed

Coverage hit (~2% of corpus). No wrong-value risk — we honestly
return `None`. Less urgent than L1/L2 but still a notable miss for
ВСК coverage.

### Proposed approaches

**A. Secondary table-driven locator (recommended)**
When the text-anchor block locator finds only a low-quality anchor
(prose suffix / empty content / inside a disclaimer paragraph), fall
back to scanning `ctx.tables` for tables whose header row matches a
`"Сведения о Страхователе"` / `"Данные страхователя"` / `"Информация
о страхователе"` pattern. Treat that table as the policyholder block.

Trade-off: needs careful header-detection regex; risk of false
matches on insurer / beneficiary tables that share similar headers.
Effort: ~1 day with iteration on real ВСК tables.

**B. Multi-anchor fallback**
When the strict-anchor scorer returns only anchors with score ≤ 0
(prose / signature), do a relaxed pass that accepts low-confidence
anchors but emits only a single `Candidate` with explicit
`low_confidence` notes.

Trade-off: doesn't actually solve the case where there's no anchor —
ВСК signature-only files HAVE an anchor (the signature one), it's
just the wrong one. (B) doesn't help.

### Decision

**(A)** is the only realistic fix. Tied to the L1 sub-extractor
framework — both are ВСК-specific table-layout problems, and the
fix mechanism is the same.

---

## L5 — pdfplumber column flattening on 2-column "АДРЕСА, РЕКВИЗИТЫ И ПОДПИСИ СТОРОН"

### Symptom

Some lizinging / corporate polices end with a two-column "АДРЕСА,
РЕКВИЗИТЫ И ПОДПИСИ СТОРОН" section where the policyholder's
requisites are in one column and the insurer's in the other. pdfplumber
flattens both columns into a single text stream, interleaving lines:

```
Страховщик:          Страхователь:
ООО СОГАЗ-АВТО       ООО СЕТЬ СКАЗОЧНЫХ ОТЕЛЕЙ "ДОМБАЙ-АРХЫЗ"
Юридический          369000, Респ. Карачаево-Черкесская
Российская Федерация,
107078, г. Москва,
проспект Академика Сахарова, 10
ОГРН 1027739820921   ОГРН 1180917001935
```

The block locator finds "Страхователь:" at the start of the
right-column section; the captured block then includes both columns'
content interleaved. Result: name partially correct, but ОГРН and
address are the *insurer's*, not the policyholder's.

### Affected files

1-3 / 644 (rare). Not a coverage problem; precision concern.

### Risk if unfixed

Wrong-value problem like L2 but at much smaller frequency.
Acceptable to defer until L1/L2 are addressed.

### Proposed approaches

**A. Column-aware extraction via pdfplumber `table_settings`**
Configure pdfplumber to extract these regions as a 2-column table
instead of flattening. Requires per-document or per-template tuning
of `table_settings` (e.g. `vertical_strategy="lines"`,
`explicit_vertical_lines=[…]`).

Trade-off: pdfplumber's `table_settings` is finicky and per-doc;
hard to generalise without per-template knowledge. The insurer
profile framework from L1 would be the right home for this — each
profile can supply its own `table_settings`.

Effort: ~1 day per template.

**B. Layout-aware text re-construction with PyMuPDF (`fitz`)**
PyMuPDF preserves text position info more reliably than pdfplumber.
Could be used as an alternative text source for column-heavy
templates: read text with positions, group into columns by X
coordinate, then run the existing parsers on the right-column-only
text.

Trade-off: adds a new dependency (PyMuPDF is AGPL — licensing
question for downstream commercial consumers). Bigger architectural
change. Effort: ~2-3 days.

**C. Defer**
3 files isn't enough to justify the work. Document the limitation
and skip.

### Decision

Lowest priority. **(A)** is the natural fit when the L1 profile
framework lands.

---

## Cross-cutting opportunities

### CC1 — Insurer profile framework

A common thread through L1, L3, L4, L5 is "this insurer / template
needs bespoke handling". The current architecture lumps everything
into single parsers with cumulative regex variants. Refactoring into
a `extraction/insurer_profiles/` directory with one module per
template + a top-level dispatcher would:

- Make each template's fixes self-contained.
- Let us add new templates without touching the shared regex bank.
- Surface insurer detection as a first-class step
  (`policyholder.insurer_template = "vsk_klassika"`) — useful
  diagnostic data for downstream consumers.

**This is the highest-leverage piece of work in the entire roadmap.**
Recommend doing it FIRST as the foundation for 0.4.x.

### CC2 — Eval harness

The 0.3.0→0.3.4 iteration relied on per-batch inspector output,
hand-grepped for failure patterns. That worked for one pass but it
won't scale:

- No ground truth → we measured coverage, not precision. The "name
  100% on batch_1" headline is misleading without verification that
  the names are *correct*.
- No diff between releases → we can't tell which fix moved which
  number.
- No regression alarm → a future change that breaks batch_5 won't be
  visible until the next manual run.

Should build an `eval/` framework that:

1. Maintains a versioned `golden.jsonl` with the expected output for
   each labeled file in `test_policies/`.
2. Runs the extractor on the corpus, diffs against `golden.jsonl`.
3. Emits per-field precision/recall and a list of regressed files.
4. Runs in CI on a subset of the corpus that's been redacted /
   synthesised (the real corpus is gitignored).

Effort: ~3-4 days for labelling the corpus + framework. **This is
the second-highest-leverage piece.**

### CC3 — Multi-page document scanning

Several limitations (L2, L3) share a root cause: the current block
locator works within a single text stream and gives up if the data
isn't there. A document-wide retry pass — "I have a name but no INN
in the block; let me sweep the rest of the document for an ИНН label
near a matching org name" — would help L2/L3 simultaneously.

Effort: ~1 day on top of CC1.

### CC4 — Output schema additions

Two fields would help downstream consumers handle the remaining
limitations gracefully:

- `policyholder.insurer_template` — the detected template name or
  `"unknown"`. Lets integrators do template-specific fallback.
- `policyholder.confidence` / `policyholder._diagnostics` — a single
  per-field confidence score and a list of pattern_ids that fired.
  Lets integrators filter out low-confidence values explicitly
  instead of relying on `None`.

Effort: ~half a day.

---

## Suggested release sequence

| Version | Scope | Effort | Risk |
|---|---|---|---|
| **0.4.0** | CC1 insurer profile framework + L1 ВСК «Классика» as the first profile + L4 secondary table-driven locator (shares ВСК surface) | 3-4 days | Medium — architectural refactor; needs careful test coverage |
| **0.4.1** | L2 name-proximity boundary for ИНН/ОГРН/КПП + CC3 multi-page sweep for L3 | 2 days | Low — additive, no API changes |
| **0.4.2** | L3 АльянсЛизинг "См. Особые условия" expansion + L5 column-aware extraction via `table_settings` for the 2-col "АДРЕСА…" templates | 2 days | Low |
| **0.5.0** | CC2 eval harness + corpus labelling. Schema additions (CC4). Breaking changes (the `_diagnostics` field, possible `policyholder.subtype = "individual_entrepreneur"` for ИП) | 4-5 days | High — first minor-version bump, schema-public |

The 0.4.x series wraps up policyholder + contacts. The 0.5.0 release
is when we admit the public API needs to evolve (`subtype`,
`_diagnostics`) and bundle the corpus eval harness that protects
future iterations.

After 0.5.x, the natural next moves are:

- **0.6.0** — ОСАГО support (carry-over from the original 0.1.0
  roadmap; the policyholder/contacts work is reusable, only parser
  patterns differ).
- **0.7.0** — `scanned_pdf` OCR corpus pass (current 255-file
  scanned corpus is completely untested for policyholder/contacts).

---

## Risk register

| ID | Risk | Mitigation |
|---|---|---|
| R1 | L2 wrong-value reports already in production. Downstream CRM may have wrong-ИНН records seeded from 0.3.0-0.3.4 outputs. | One-time re-extraction recommended after 0.4.1 ships. Document the substitution risk in README "Known limitations". |
| R2 | Insurer profile framework introduces a dispatcher that could regress non-profiled documents (the 80% case). | Default profile = current behaviour. Profile activation is opt-in via fingerprint match. CI test that runs the full corpus before/after. |
| R3 | EGRUL API verification (L2 option C) introduces a network dependency. | Off by default. Gated by an explicit `verify_inn=True` flag. Document the trade-off. |
| R4 | PyMuPDF (L5 option B) is AGPL. Downstream commercial users would need to swap it out or buy a commercial license. | Don't introduce as a hard dependency. If needed, expose as an optional extras_require. Prefer pdfplumber tuning (option A). |
| R5 | Corpus is private; eval harness can't run in public CI. | Build a synthetic / redacted fixture subset that goes into the repo. Real-corpus eval stays on the owner's machine. |

---

## What we'd need but don't have

- **Ground-truth labels** for the corpus. Currently every "X / 644
  files extracted" number is a *coverage* claim, not a *precision*
  one. We don't know how many of the 525 extracted names in 0.3.4
  are actually correct vs. correctly-shaped-but-wrong (some L2
  files have correctly-shaped name strings but the wrong ИНН).
  Labelling effort: ~2-3 days for a single human to go through
  the full 644 digital_pdf corpus.
- **Per-insurer template fingerprinting heuristic** (a precursor to
  CC1). Need a `detect_template(text, tables) -> template_id`
  function with high precision. Sketch: detect by combination of
  insurer name + characteristic header strings (e.g.
  `"САО \"ВСК\""` + `"ФИО гражданина/"` → `vsk_klassika`).
- **A separate corpus pass on `scanned_pdf/*`** to know what L1-L5
  look like through OCR. Currently 255 files are entirely untested
  in our digital-pdf roadmap.

---

## How to use this document

When picking up policyholder/contacts work in future sessions:

1. Re-read this doc to see what's still open (`git log` against this
   file shows what's already been delivered).
2. Pick a limitation. Check the proposed approach decision (some are
   marked "recommended", others are alternatives to consider).
3. Build a regression test from the failure example first, then
   implement, then re-run the corpus to verify the fix.
4. Update this doc as work lands — strike through delivered items
   rather than deleting them (preserves the rationale for archive).
