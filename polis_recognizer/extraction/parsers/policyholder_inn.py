"""PolicyholderINNParser — extract policyholder's ИНН.

ИНН is the most frequently needed structural identifier for downstream
integration (CRM, accounting, КЛАДР lookups). We extract it with a
strict checksum gate — random 10/12-digit runs in OCR output are
common, and an unvalidated match would produce confident-but-wrong
values.

Strategy:

1. **Table strategy** — when a table contains a "Страхователь" label
   cell anywhere AND an "ИНН" label cell with adjacent digits,
   the digits are the policyholder's ИНН. Strongest path: pdfplumber
   gives row-level grouping for free.
2. **Anchored text strategy** — within the policyholder block (see
   ``policyholder_block.py``), find ИНН labels and validate the
   digits that follow.

A candidate is emitted ONLY when the checksum validates. This is
critical for precision; an invalid ИНН is worse than no value at all
for an integrator who will use it as a foreign key.
"""

from __future__ import annotations

import re
from typing import List

from ..candidates import Candidate, ConfidenceComponents
from ..policyholder_block import (
    locate_policyholder_block,
    table_has_policyholder_anchor,
)
from ..validators import validate_inn_10, validate_inn_12
from .base import ExtractionContext, FieldParser


_INN_DIGITS_RE = re.compile(r"(?<!\d)(\d{10}|\d{12})(?!\d)")
_INN_LABEL_AND_DIGITS_RE = re.compile(
    r"ИНН\b[\s:№#\-]*(\d{10}|\d{12})(?!\d)", re.IGNORECASE
)

_INN_TABLE_LABEL_RE = re.compile(r"^\s*ИНН\b", re.IGNORECASE)


def _validate_any_length(s: str) -> bool:
    if len(s) == 10:
        return validate_inn_10(s)
    if len(s) == 12:
        return validate_inn_12(s)
    return False


class PolicyholderINNParser(FieldParser):
    field_name = "policyholder_inn"

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
                if not table_has_policyholder_anchor(table):
                    continue
                for row in table:
                    if not row or len(row) < 2:
                        continue
                    if not _INN_TABLE_LABEL_RE.match(row[0] or ""):
                        continue
                    for cell in row[1:]:
                        if cell is None:
                            continue
                        digits_match = _INN_DIGITS_RE.search(cell)
                        if digits_match is None:
                            continue
                        digits = digits_match.group(1)
                        if not _validate_any_length(digits):
                            continue
                        out.append(
                            Candidate(
                                value=digits,
                                state="found",
                                pattern_id="table_cell",
                                source_fragment=f"ИНН | {digits}",
                                components=ConfidenceComponents(
                                    pattern_strength=0.75,
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
        for match in _INN_LABEL_AND_DIGITS_RE.finditer(block_text):
            digits = match.group(1)
            if not _validate_any_length(digits):
                continue
            span_abs = (start + match.start(), start + match.end())
            out.append(
                Candidate(
                    value=digits,
                    state="found",
                    pattern_id="anchored_text",
                    source_fragment=self.take_fragment(
                        text, span_abs[0], span_abs[1]
                    ),
                    span=span_abs,
                    components=ConfidenceComponents(
                        pattern_strength=0.65,
                        context_strength=0.25,
                    ),
                )
            )
        return out
