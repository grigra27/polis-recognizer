"""PolicyholderPassportParser — extract passport series + number.

**PII-gated.** Returns nothing unless ``ctx.extract_pii`` is True (the
constructor flag on ``PolicyExtractor``). This is a defence in depth on
top of the composer-level gate in ``extractor._build_policyholder`` —
both must be on for the data to surface.

Russian passports are identified by a 4-digit series and a 6-digit
number, often written with a space inside the series:

    Паспорт 12 34 № 567890
    Паспорт серия 1234 номер 567890
    Паспорт гражданина РФ 12 34 567890

Returned value is a dict ``{"series": "1234", "number": "567890"}`` —
series normalised to 4 contiguous digits.
"""

from __future__ import annotations

import re
from typing import List

from ..candidates import Candidate, ConfidenceComponents
from ..policyholder_block import locate_policyholder_block
from .base import ExtractionContext, FieldParser


_PASSPORT_RE = re.compile(
    r"Паспорт"
    # Optional noise between label and digits: "гражданина РФ", "серия",
    # "серии", "РФ", colons, № marks, commas, dots, whitespace. Capped
    # at 40 chars so the regex can't merge a passport label with an
    # unrelated digit run on the next paragraph.
    r"[\sа-яёА-ЯЁ:№,.]{0,40}?"
    r"(\d{2}\s*\d{2})"               # 4-digit series, optional internal space
    r"[\sа-яёА-ЯЁ:№,.]{0,20}?"
    r"(\d{6})",                      # 6-digit number
    re.IGNORECASE,
)


class PolicyholderPassportParser(FieldParser):
    field_name = "policyholder_passport"

    def parse(self, ctx: ExtractionContext) -> List[Candidate]:
        if not ctx.extract_pii:
            return []
        block = locate_policyholder_block(ctx.normalized)
        if block is None:
            return [
                Candidate(
                    value=None,
                    state="not_found",
                    pattern_id="no_pattern_match",
                    source_fragment="",
                )
            ]
        text = ctx.normalized.text
        start, end = block
        block_text = text[start:end]

        match = _PASSPORT_RE.search(block_text)
        if match is None:
            return [
                Candidate(
                    value=None,
                    state="not_found",
                    pattern_id="no_pattern_match",
                    source_fragment="",
                )
            ]

        series_raw, number = match.group(1), match.group(2)
        series = re.sub(r"\s+", "", series_raw)
        span_abs = (start + match.start(), start + match.end())
        return [
            Candidate(
                value={"series": series, "number": number},
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
        ]
