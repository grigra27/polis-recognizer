"""PolicyholderBirthDateParser — extract policyholder's date of birth.

**PII-gated.** Same as ``PolicyholderPassportParser``: returns nothing
unless ``ctx.extract_pii`` is True.

Supports both label-before-date and date-before-label phrasings, with
both numeric (``DD.MM.YYYY``) and textual (``DD <month-word> YYYY``)
date forms:

    Дата рождения: 01.01.1980
    01 января 1980 г.р.
    Год рождения 1980        (year-only — emitted as YYYY-01-01)

Returned value is an ISO date string (``YYYY-MM-DD``) inside the
pipeline; the public-API composer converts it to ``datetime.date`` at
the boundary.
"""

from __future__ import annotations

import re
from typing import List, Optional

from ..candidates import Candidate, ConfidenceComponents
from ..dates import parse_russian_date
from ..policyholder_block import locate_policyholder_block
from .base import ExtractionContext, FieldParser


_DATE_NUMERIC_RE = r"\d{1,2}\.\d{1,2}\.\d{4}"
# Textual: day + Russian month word + year. The word boundary on
# `\b\d{4}\b` keeps us from extending the match into longer digit runs.
_DATE_TEXTUAL_RE = r"\d{1,2}\s+[А-Яа-яёЁ]+\s+\d{4}"

_LABEL_THEN_DATE_RE = re.compile(
    r"(?:Дата\s+рождения|год\s+рождения|г(?:од)?\s*\.?\s*р(?:ождения)?\s*\.?)"
    r"[\s.:]+"
    rf"({_DATE_NUMERIC_RE}|{_DATE_TEXTUAL_RE})",
    re.IGNORECASE,
)

_DATE_THEN_LABEL_RE = re.compile(
    rf"({_DATE_NUMERIC_RE}|{_DATE_TEXTUAL_RE})"
    r"\s+г\.?\s*р\.?",
    re.IGNORECASE,
)


def _emit_not_found() -> List[Candidate]:
    return [
        Candidate(
            value=None,
            state="not_found",
            pattern_id="no_pattern_match",
            source_fragment="",
        )
    ]


class PolicyholderBirthDateParser(FieldParser):
    field_name = "policyholder_birth_date"

    def parse(self, ctx: ExtractionContext) -> List[Candidate]:
        if not ctx.extract_pii:
            return []
        block = locate_policyholder_block(ctx.normalized)
        if block is None:
            return _emit_not_found()
        text = ctx.normalized.text
        start, end = block
        block_text = text[start:end]
        candidates: List[Candidate] = []

        for pattern_id, pattern in (
            ("label_then_date", _LABEL_THEN_DATE_RE),
            ("date_then_label", _DATE_THEN_LABEL_RE),
        ):
            for match in pattern.finditer(block_text):
                iso = parse_russian_date(match.group(1))
                if iso is None:
                    continue
                span_abs = (start + match.start(), start + match.end())
                candidates.append(
                    Candidate(
                        value=iso,
                        state="found",
                        pattern_id=pattern_id,
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

        if not candidates:
            return _emit_not_found()
        return candidates
