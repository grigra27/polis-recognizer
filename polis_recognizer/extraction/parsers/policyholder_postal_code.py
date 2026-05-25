"""PolicyholderPostalCodeParser — extract 6-digit Russian postal code.

The Russian postal index is exactly 6 digits, first digit 1–6 (federal
district code; values 7+ don't exist).

**Strategy after 0.3.4**: extract ONLY from the leading 6 digits of an
address anchor's content — never via a free-form scan of the block /
table. The free-form scan that earlier versions used kept catching
VIN, ПТС and ПСМ tails (e.g. `VIN Z94C241BBSR270155` would surface
`270155` as a postal code on documents whose actual address had no
index). On batch_2 that misfire affected ~15/31 files.

The parser leans on the same address anchors / table label patterns
that `PolicyholderAddressParser` uses, then checks whether the captured
address content starts with `[1-6]\\d{5}` followed by a comma, space,
or end-of-string.
"""

from __future__ import annotations

import re
from typing import List, Optional

from ..candidates import Candidate, ConfidenceComponents
from ..policyholder_block import (
    locate_policyholder_block,
    policyholder_table_rows,
)
from .base import ExtractionContext, FieldParser


# Postal code MUST be at the very start of the address content
# (optionally preceded by whitespace), MUST be exactly 6 digits with
# the first one 1–6, AND MUST be followed by a non-digit (so a 7+
# digit run starting with [1-6] doesn't match the prefix).
_POSTAL_AT_START_RE = re.compile(r"^\s*([1-6]\d{5})(?!\d)")

# Same address anchors as the address parser — locally inlined to
# keep the dependency tree flat. Kept in sync with
# parsers/policyholder_address.py:_ADDRESS_ANCHORS_RE.
_ADDRESS_ANCHORS_RE = re.compile(
    r"(?:"
    r"Юр\.?\s*адрес"
    r"|Фактический\s+адрес"
    r"|Почтовый\s+адрес"
    r"|Адрес\s+регистрации"
    r"|Адрес\s+страхователя"
    r"|Зарегистрирован[\w\s]{0,20}?\s+по\s+адресу"
    r"|Место\s+жительства"
    r"|Место\s+нахождения"
    r"|Адрес"
    r")\s*[:\-—–]?\s*",
    re.IGNORECASE,
)

# Same table labels as address parser.
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


def _postal_from_address_value(value: Optional[str]) -> Optional[str]:
    """Return the 6-digit postal prefix of ``value`` or None."""
    if not value:
        return None
    match = _POSTAL_AT_START_RE.match(value)
    if match is None:
        return None
    return match.group(1)


class PolicyholderPostalCodeParser(FieldParser):
    field_name = "policyholder_postal_code"

    def parse(self, ctx: ExtractionContext) -> List[Candidate]:
        candidates = self._from_text(ctx)
        if not candidates:
            candidates = self._from_tables(ctx)
        if not candidates:
            return self._not_found()
        return candidates

    def _from_text(self, ctx: ExtractionContext) -> List[Candidate]:
        block = locate_policyholder_block(ctx.normalized)
        if block is None:
            return []
        text = ctx.normalized.text
        start, end = block
        block_text = text[start:end]

        for anchor in _ADDRESS_ANCHORS_RE.finditer(block_text):
            tail = block_text[anchor.end() :]
            digits = _postal_from_address_value(tail)
            if digits is None:
                continue
            offset = anchor.end() + (len(tail) - len(tail.lstrip()))
            span_abs = (start + offset, start + offset + 6)
            return [
                Candidate(
                    value=digits,
                    state="found",
                    pattern_id="address_anchor",
                    source_fragment=self.take_fragment(
                        text, span_abs[0], span_abs[1]
                    ),
                    span=span_abs,
                    components=ConfidenceComponents(
                        pattern_strength=0.6,
                        context_strength=0.25,
                    ),
                )
            ]
        return []

    def _from_tables(self, ctx: ExtractionContext) -> List[Candidate]:
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
                    for cell in row[1:]:
                        digits = _postal_from_address_value(cell)
                        if digits is None:
                            continue
                        return [
                            Candidate(
                                value=digits,
                                state="found",
                                pattern_id="table_cell",
                                source_fragment=f"…{digits}…",
                                components=ConfidenceComponents(
                                    pattern_strength=0.6,
                                    context_strength=0.25,
                                ),
                            )
                        ]
        return []

    def _not_found(self) -> List[Candidate]:
        return [
            Candidate(
                value=None,
                state="not_found",
                pattern_id="no_pattern_match",
                source_fragment="",
            )
        ]
