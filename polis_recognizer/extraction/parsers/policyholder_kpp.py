"""PolicyholderKPPParser — extract policyholder's КПП.

КПП is exactly 9 digits and applies only to legal entities (not to
individuals or ИП). It has no published checksum, so the parser
relies entirely on context: the label "КПП" must be present.

Strategy:

1. **Table strategy** — table containing a "Страхователь" anchor AND
   a "КПП" label cell with 9-digit adjacent value.
2. **Anchored text strategy** — within the policyholder block, find
   "КПП" labels followed by exactly 9 digits.

No bare 9-digit fallback: random 9-digit runs are too common (phone
numbers, account fragments, transit codes) to accept without an
explicit label.
"""

from __future__ import annotations

import re
from typing import List

from ..candidates import Candidate, ConfidenceComponents
from ..policyholder_block import (
    locate_policyholder_block,
    table_has_policyholder_anchor,
)
from .base import ExtractionContext, FieldParser


_KPP_LABEL_AND_DIGITS_RE = re.compile(
    r"КПП\b[\s:№#\-]*(\d{9})(?!\d)", re.IGNORECASE
)
_KPP_DIGITS_RE = re.compile(r"(?<!\d)(\d{9})(?!\d)")
_KPP_TABLE_LABEL_RE = re.compile(r"^\s*КПП\b", re.IGNORECASE)


class PolicyholderKPPParser(FieldParser):
    field_name = "policyholder_kpp"

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
                    if not _KPP_TABLE_LABEL_RE.match(row[0] or ""):
                        continue
                    for cell in row[1:]:
                        if cell is None:
                            continue
                        digits_match = _KPP_DIGITS_RE.search(cell)
                        if digits_match is None:
                            continue
                        digits = digits_match.group(1)
                        out.append(
                            Candidate(
                                value=digits,
                                state="found",
                                pattern_id="table_cell",
                                source_fragment=f"КПП | {digits}",
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
        for match in _KPP_LABEL_AND_DIGITS_RE.finditer(block_text):
            digits = match.group(1)
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
                        pattern_strength=0.6,
                        context_strength=0.25,
                    ),
                )
            )
        return out
