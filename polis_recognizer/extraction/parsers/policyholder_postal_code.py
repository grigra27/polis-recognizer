"""PolicyholderPostalCodeParser — extract 6-digit Russian postal code.

The Russian postal index is exactly 6 digits, and its first digit
encodes a federal district — values 1–6 only (district codes 7+ don't
exist). That single-digit gate is enough to filter most non-index
6-digit runs (ОКВЭД codes, OCR digit sequences, etc.).

Strategy: scan the policyholder block for ``[1-6]\\d{5}`` flanked by
non-digits; first match wins. Postal codes typically open the address
string ("101000, г. Москва"), so the first hit inside the block is
nearly always the right one.

Out of scope: cross-checking against the captured address from
PolicyholderAddressParser — v2 parsers run independently. If the
postal code needs to come from inside the address specifically, the
caller can do that check post-extraction.
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


_POSTAL_RE = re.compile(r"(?<!\d)([1-6]\d{5})(?!\d)")


class PolicyholderPostalCodeParser(FieldParser):
    field_name = "policyholder_postal_code"

    def parse(self, ctx: ExtractionContext) -> List[Candidate]:
        # Text-block scan first.
        candidates = self._from_block(ctx)
        # Fall back to anchored tables when block has no postal code —
        # in XLS form-mask polises the address often lives only in the
        # table layer, never in the text layer.
        if not candidates:
            candidates = self._from_tables(ctx)
        if not candidates:
            return self._not_found()
        return candidates

    def _from_block(self, ctx: ExtractionContext) -> List[Candidate]:
        block = locate_policyholder_block(ctx.normalized)
        if block is None:
            return []
        text = ctx.normalized.text
        start, end = block
        block_text = text[start:end]

        match = _POSTAL_RE.search(block_text)
        if match is None:
            return []
        digits = match.group(1)
        span_abs = (start + match.start(), start + match.end())
        return [
            Candidate(
                value=digits,
                state="found",
                pattern_id="block_scan",
                source_fragment=self.take_fragment(
                    text, span_abs[0], span_abs[1]
                ),
                span=span_abs,
                components=ConfidenceComponents(
                    pattern_strength=0.55,
                    context_strength=0.2,
                ),
            )
        ]

    def _from_tables(self, ctx: ExtractionContext) -> List[Candidate]:
        out: List[Candidate] = []
        for page in ctx.tables or []:
            for table in page or []:
                rows = policyholder_table_rows(table)
                if not rows:
                    continue
                for row in rows:
                    for cell in row or []:
                        if not cell:
                            continue
                        match = _POSTAL_RE.search(cell)
                        if match is None:
                            continue
                        digits = match.group(1)
                        out.append(
                            Candidate(
                                value=digits,
                                state="found",
                                pattern_id="table_cell",
                                source_fragment=f"…{digits}…",
                                components=ConfidenceComponents(
                                    pattern_strength=0.55,
                                    context_strength=0.2,
                                ),
                            )
                        )
                        # First hit per table is enough.
                        if out:
                            return out
        return out

    def _not_found(self) -> List[Candidate]:
        return [
            Candidate(
                value=None,
                state="not_found",
                pattern_id="no_pattern_match",
                source_fragment="",
            )
        ]
