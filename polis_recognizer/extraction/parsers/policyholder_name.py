"""PolicyholderNameParser — extract the contracting party's name.

КАСКО polises name the policyholder via a labeled block, either inline
or as a table cell:

    Страхователь: ООО "Ромашка"
    ИНН 7707012345 ...

    Страхователь: Иванов Иван Иванович
    Дата рождения: ...

The parser returns the literal text as written. No Title-Case
normalization, no ФИО splitting — those are caller decisions; the
authoritative source-of-truth form depends on the integration.

Strategy, in descending strength:

1. **Table-cell match** via ``ctx.tables`` — left-cell starts with
   ``Страхователь``, right cell is the name. Strongest path:
   pdfplumber gives us the label cell directly, no boundary heuristics.
2. **Anchored text match** — ``Страхователь:`` prefix; capture from
   the anchor to the next labeled subfield (``ИНН``, ``Адрес`` …),
   end-of-line, or end-of-block (see ``policyholder_block.py``).

No bare-ФИО fallback in PR #2 — too noisy without an anchor; the
type-classifier handles bare-ФИО as a hint, not as identity.
"""

from __future__ import annotations

import re
from typing import List

from ..candidates import Candidate, ConfidenceComponents
from ..policyholder_block import locate_policyholder_block
from .base import ExtractionContext, FieldParser


_NAME_LABEL_RE = re.compile(
    r"^\s*(?:Страхователь|СТРАХОВАТЕЛЬ)\s*[:\-—–]?\s*$",
)

# Labels that mark the END of the name capture within the block.
# Keep deliberately short — false positives here truncate the name.
_NAME_STOP_RE = re.compile(
    r"\s*(?:"
    r"ИНН|КПП|ОГРН(?:ИП)?|"
    r"Адрес|Место\s+жительства|Место\s+нахождения|Зарегистр|"
    r"Тел(?:ефон)?|E-?mail|Эл\.?\s*почта|Почта\s*:|"
    r"Паспорт|Дата\s+рождения|г\.р\.|"
    r"Контактн"
    r")",
    re.IGNORECASE,
)

# Three Cyrillic words / two-word Surname-First / surname + initials.
# Used only to BOOST confidence on captured anchor text; never as a
# capture pattern itself.
_FIO_SHAPE_RE = re.compile(
    r"\b[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+)?\b"
)
_FIO_INITIALS_SHAPE_RE = re.compile(
    r"\b[А-ЯЁ][а-яё]+\s+[А-ЯЁ]\.\s*[А-ЯЁ]\."
)

# Strong "this is a legal entity" hint inside the captured name.
_ORG_PREFIX_RE = re.compile(
    r"\b(?:ООО|ОАО|АО|ПАО|ЗАО|ИП|НКО|АНО|ТСЖ|ТСН|МУП|ГУП|ФГУП|"
    r"Общество\s+с\s+ограниченной)\b",
    re.IGNORECASE,
)


def _looks_like_name(s: str) -> bool:
    """A real name has letters; bare digit / punctuation strings don't."""
    return bool(s) and any(c.isalpha() for c in s)


def _strip_trailing_punctuation(s: str) -> str:
    return s.rstrip(" \t,;.:—–-")


class PolicyholderNameParser(FieldParser):
    field_name = "policyholder_name"

    def parse(self, ctx: ExtractionContext) -> List[Candidate]:
        candidates: List[Candidate] = []
        candidates.extend(self._from_tables(ctx))
        candidates.extend(self._from_anchor(ctx))

        if not candidates:
            candidates.append(
                Candidate(
                    value=None,
                    state="not_found",
                    pattern_id="no_pattern_match",
                    source_fragment="",
                )
            )
        return candidates

    # ----- strategies -----

    def _from_tables(self, ctx: ExtractionContext) -> List[Candidate]:
        out: List[Candidate] = []
        for page in ctx.tables or []:
            for table in page or []:
                for row in table or []:
                    if not row or len(row) < 2:
                        continue
                    label = (row[0] or "").strip()
                    if not _NAME_LABEL_RE.match(label):
                        continue
                    value_raw = " ".join(
                        (c or "").strip() for c in row[1:]
                        if (c or "").strip()
                    )
                    value = _strip_trailing_punctuation(value_raw.strip())
                    if not _looks_like_name(value):
                        continue
                    out.append(
                        Candidate(
                            value=value,
                            state="found",
                            pattern_id="table_cell",
                            source_fragment=f"{label} | {value}"[:240],
                            components=ConfidenceComponents(
                                pattern_strength=0.7,
                                context_strength=0.3,
                            ),
                        )
                    )
        return out

    def _from_anchor(self, ctx: ExtractionContext) -> List[Candidate]:
        block = locate_policyholder_block(ctx.normalized)
        if block is None:
            return []
        text = ctx.normalized.text
        start, end = block
        block_text = text[start:end]

        # Strip the anchor's trailing punctuation/whitespace (colon,
        # dash, leading spaces left by the start-anchor's lookahead).
        head = re.match(r"^[\s:\-—–]*", block_text)
        capture_start = head.end() if head else 0

        # Stop at the first of: known subfield label, newline, end.
        stop_match = _NAME_STOP_RE.search(block_text, capture_start)
        eol_pos = block_text.find("\n", capture_start)
        bounds = [len(block_text)]
        if stop_match is not None:
            bounds.append(stop_match.start())
        if eol_pos != -1:
            bounds.append(eol_pos)
        capture_end = min(bounds)

        value = _strip_trailing_punctuation(
            block_text[capture_start:capture_end].strip()
        )
        if not _looks_like_name(value):
            return []

        is_org = bool(_ORG_PREFIX_RE.search(value))
        is_fio = bool(
            _FIO_SHAPE_RE.search(value)
            or _FIO_INITIALS_SHAPE_RE.search(value)
        )
        pattern_strength = 0.55
        context_strength = 0.25 if (is_org or is_fio) else 0.10

        span_abs = (start + capture_start, start + capture_end)
        fragment = self.take_fragment(text, span_abs[0], span_abs[1])
        return [
            Candidate(
                value=value,
                state="found",
                pattern_id="anchor_text",
                source_fragment=fragment,
                span=span_abs,
                components=ConfidenceComponents(
                    pattern_strength=pattern_strength,
                    context_strength=context_strength,
                ),
            )
        ]
