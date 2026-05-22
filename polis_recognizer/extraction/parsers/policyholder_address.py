"""PolicyholderAddressParser — extract policyholder's address as raw text.

Russian addresses on insurance documents are messy: КЛАДР/ФИАС-grade
component parsing is a separate problem with its own reference data
and is intentionally out of scope here. The parser captures the raw
string after an address anchor and returns it as-is (whitespace
collapsed, trailing punctuation trimmed). Downstream consumers that
need structure plug in КЛАДР/ФИАС themselves.

Strategy:

1. **Table strategy** — anchored on "Страхователь" in the same table;
   row labelled "Адрес" / "Место жительства" / "Зарегистр…".
2. **Anchored text strategy** — find an address anchor inside the
   policyholder block, capture to the next labeled subfield, double
   newline, or a 250-char cap.

The cap exists because a missing stopper can let the capture run on
into unrelated content. 250 chars comfortably covers any realistic
Russian address (zip + region + city + street + building + apartment).
"""

from __future__ import annotations

import re
from typing import List

from ..candidates import Candidate, ConfidenceComponents
from ..policyholder_block import (
    locate_policyholder_block,
    policyholder_table_rows,
)
from .base import ExtractionContext, FieldParser


_ADDRESS_ANCHORS_RE = re.compile(
    r"(?:"
    r"Юр\.?\s*адрес"
    r"|Фактический\s+адрес"
    r"|Почтовый\s+адрес"
    r"|Адрес\s+регистрации"
    r"|Зарегистрирован[\w\s]{0,20}?\s+по\s+адресу"
    r"|Место\s+жительства"
    r"|Место\s+нахождения"
    r"|Адрес"
    r")\s*[:\-—–]?\s*",
    re.IGNORECASE,
)

# Labels that close the address capture. Includes both prose labels
# (Тел, E-mail, Дата рождения) and the abbreviated form-field labels
# seen in XLS-form-mask polises after pdfplumber column flattening
# (ДАТА РОЖД., ПОЛ, ТЕЛ, РЕЗИДЕНТ — printed as inline pseudo-labels
# next to the address).
_ADDRESS_STOP_RE = re.compile(
    r"\s*(?:"
    r"ИНН\b|КПП\b|ОГРН(?:ИП)?\b|"
    r"Тел(?:ефон)?\b|ТЕЛ\b|"
    r"E-?mail|Эл\.?\s*почта|Почта\s*:|"
    r"Паспорт\b|"
    r"Дата\s+рождения|ДАТА\s+РОЖД|г\.р\.|"
    r"ПОЛ\b|"
    r"РЕЗИДЕНТ\b|"
    r"Контактн|"
    r"Страховщик|СТРАХОВЩИК|"
    r"Выгодоприобретатель"
    r")",
    re.IGNORECASE,
)

_ADDRESS_TABLE_LABEL_RE = re.compile(
    r"^\s*(?:"
    r"Адрес"
    r"|Место\s+жительства"
    r"|Место\s+нахождения"
    r"|Зарегистр"
    r"|Юр\.?\s*адрес"
    r"|Фактический\s+адрес"
    r"|Почтовый\s+адрес"
    r")",
    re.IGNORECASE,
)

_MAX_ADDRESS_CHARS = 250


def _normalize_address(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"\s+", " ", s).strip()
    return s.rstrip(" ,;.:—–-")


def _looks_like_address(s: str) -> bool:
    """A real address contains letters and a comma or postal-like digits.

    Bare punctuation, just digits, or single-word placeholders shouldn't
    pass through. Keeps the bar very low — addresses vary wildly.
    """
    if not s or len(s) < 5:
        return False
    return any(c.isalpha() for c in s)


class PolicyholderAddressParser(FieldParser):
    field_name = "policyholder_address"

    def parse(self, ctx: ExtractionContext) -> List[Candidate]:
        candidates: List[Candidate] = []
        candidates.extend(self._from_tables(ctx))
        candidates.extend(self._from_text(ctx))
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

    def _from_tables(self, ctx: ExtractionContext) -> List[Candidate]:
        out: List[Candidate] = []
        for page in ctx.tables or []:
            for table in page or []:
                rows = policyholder_table_rows(table)
                if not rows:
                    continue
                for row in rows:
                    if not row or len(row) < 2:
                        continue
                    if not _ADDRESS_TABLE_LABEL_RE.match(row[0] or ""):
                        continue
                    value_raw = " ".join(
                        (c or "").strip()
                        for c in row[1:]
                        if (c or "").strip()
                    )
                    # Same idea as the name parser's table-cell stop
                    # (0.3.1): XLS form-mask polises join adjacent
                    # form fields into a single value string. Without
                    # this truncation the address slot ends up with
                    # "…, д. 2 ДАТА РОЖД. 21.02.1966 ПОЛ М ТЕЛ".
                    stop_match = _ADDRESS_STOP_RE.search(value_raw)
                    if stop_match is not None:
                        value_raw = value_raw[: stop_match.start()]
                    value = _normalize_address(value_raw)
                    if not _looks_like_address(value):
                        continue
                    out.append(
                        Candidate(
                            value=value,
                            state="found",
                            pattern_id="table_cell",
                            source_fragment=f"Адрес | {value}"[:240],
                            components=ConfidenceComponents(
                                pattern_strength=0.7,
                                context_strength=0.25,
                            ),
                        )
                    )
        return out

    def _from_text(self, ctx: ExtractionContext) -> List[Candidate]:
        block = locate_policyholder_block(ctx.normalized)
        if block is None:
            return []
        text = ctx.normalized.text
        start, end = block
        block_text = text[start:end]
        out: List[Candidate] = []

        for anchor_match in _ADDRESS_ANCHORS_RE.finditer(block_text):
            capture_start = anchor_match.end()
            stop_match = _ADDRESS_STOP_RE.search(block_text, capture_start)
            para_end = block_text.find("\n\n", capture_start)
            bounds = [
                len(block_text),
                capture_start + _MAX_ADDRESS_CHARS,
            ]
            if stop_match is not None:
                bounds.append(stop_match.start())
            if para_end != -1:
                bounds.append(para_end)
            capture_end = min(bounds)

            value = _normalize_address(block_text[capture_start:capture_end])
            if not _looks_like_address(value):
                continue
            span_abs = (start + capture_start, start + capture_end)
            out.append(
                Candidate(
                    value=value,
                    state="found",
                    pattern_id="anchored_text",
                    source_fragment=self.take_fragment(
                        text, span_abs[0], span_abs[1]
                    ),
                    span=span_abs,
                    components=ConfidenceComponents(
                        pattern_strength=0.55,
                        context_strength=0.25,
                    ),
                )
            )
        return out
