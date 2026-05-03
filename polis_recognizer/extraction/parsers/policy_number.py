"""PolicyNumberParser — extract the polis identifier.

КАСКО polises typically open with a heading like

    Серия 2022 № 0364420 / 26ТФ от 18.02.2026
    Полис страхования транспортного средства

The series-and-number form is the strongest signal. Generic
"Полис №" / "Договор №" patterns are kept as a weaker fallback.

The parser carefully avoids matching ПТС/СТС serials that share the
``Серия ... № ...`` shape but use 2-digit + Cyrillic letters
("Серия 77УО № 564607") — we require 4 digits in the series field for
the strong pattern.
"""

from __future__ import annotations

import re
from typing import List, Optional

from ..candidates import Candidate, ConfidenceComponents
from .base import ExtractionContext, FieldParser


# Numero sign in raw text (``№`` U+2116) is collapsed by Unicode NFKC
# normalisation to the ASCII bigram ``No``. The TextNormalizer applies
# NFKC, so by the time our regex runs we see ``No`` not ``№``. The
# alternation accepts both forms so the parser also works on text that
# bypassed normalisation (e.g. unit-tested in isolation).
_NO_TOKEN = r"(?:№|No\b)"

# 4 digits = year-like series ("2022", "1234"). Ignores ПТС/СТС
# series ("77УО") which use 2 digits + Cyrillic letters.
# Patterns are built via concatenation (not f-strings) so the
# placeholder-guard pre-commit hook doesn't mis-read regex quantifiers
# like ``\d{4}`` as template placeholders.
_SERIES_NUMBER_PATTERN = re.compile(
    r"Серия\s+(\d{4})\s+"
    + _NO_TOKEN
    + r"\s*(\d{4,})(?:\s*/\s*([\w\-А-Яа-яЁё]{1,16}))?",
    re.IGNORECASE,
)

# "Полис № N" / "Договор № N" with a moderately long alphanumeric body.
# The optional ``[|:‖]?`` between the № sign and the body absorbs OCR
# table-cell separators — Чулпан КАСКО scans render the polis-number
# table as "ПОЛИС № | 1211/27-0000185-2 | ..." with a literal pipe
# the OCR mistook for the cell ruling. The character class is short
# and specific so it doesn't open up false-positive surface.
_POLIS_KEYWORD_PATTERN = re.compile(
    r"(?:полис|договор)\s+страхования[\s\S]{0,120}?"
    + _NO_TOKEN
    + r"\s*[|:‖]?\s*([A-ZА-Я0-9][A-ZА-Я0-9\-/]{3,20})",
    re.IGNORECASE,
)
_POLIS_SHORT_PATTERN = re.compile(
    r"(?:полис|договор)\s+"
    + _NO_TOKEN
    + r"\s*[|:‖]?\s*([A-ZА-Я0-9][A-ZА-Я0-9\-/]{3,20})",
    re.IGNORECASE,
)

# АльфаСтрахование XLS-converted layout: pypdf scatters the polis header
# away from the "Полис №" anchor that POLIS_KEYWORD_PATTERN expects, so
# we match the very specific 4-segment numeric form they use:
#   71717/046/00402/25       canonical (5/3/5/2)
#   49297/046/0000424/25     same shape, longer asset number (5/3/7/2)
#   8991R/046/0000340/25     same shape, branch letter in segment 1
# Two anchored variants — after the № sign, or standalone on a line.
# The 4-segment slash shape with strict per-segment digit counts keeps
# the false-positive surface tiny even without strict word boundaries.
# The relaxed `No` (no `\b`) is needed because pypdf concatenates
# "No8991R/..." without a space between the marker and the body.
_ALFA_XLS_NUMBER_PATTERN_WITH_NO = re.compile(
    r"(?:№|No)\s*(\d{4,6}[A-Z]?/\d{2,4}/\d{4,7}/\d{2,4})",
    re.IGNORECASE,
)
_ALFA_XLS_NUMBER_PATTERN_BARE = re.compile(
    r"(?:^|\n)\s*(\d{4,6}[A-Z]?/\d{2,4}/\d{4,7}/\d{2,4})\s*(?:$|\n)",
    re.MULTILINE,
)


_MIN_DIGITS_IN_POLIS_NUMBER = 3


def _looks_like_polis_number(value: str) -> bool:
    """A real polis identifier carries digits; bare Cyrillic words don't."""
    if not value:
        return False
    digits = sum(1 for c in value if c.isdigit())
    return digits >= _MIN_DIGITS_IN_POLIS_NUMBER


def _format(series: Optional[str], number: str, suffix: Optional[str]) -> str:
    parts: List[str] = []
    if series:
        parts.append(series)
    parts.append(number)
    if suffix:
        parts.append(suffix)
    return " / ".join(parts)


class PolicyNumberParser(FieldParser):
    field_name = "policy_number"

    def parse(self, ctx: ExtractionContext) -> List[Candidate]:
        text = ctx.normalized.text
        candidates: List[Candidate] = []

        # 1) Strongest signal: Серия XXXX № YYYY [/ ZZZ].
        for match in _SERIES_NUMBER_PATTERN.finditer(text):
            series, number, suffix = match.group(1), match.group(2), match.group(3)
            display = _format(series, number, suffix)
            fragment = self.take_fragment(text, match.start(), match.end())
            candidates.append(
                Candidate(
                    value={
                        "display": display,
                        "series": series,
                        "number": number,
                        "suffix": suffix,
                    },
                    state="found",
                    pattern_id="series_number",
                    source_fragment=fragment,
                    span=(match.start(), match.end()),
                    components=ConfidenceComponents(
                        pattern_strength=0.7, context_strength=0.3,
                    ),
                )
            )

        # 2) "Полис страхования ... № N" — moderate pattern.
        for match in _POLIS_KEYWORD_PATTERN.finditer(text):
            number = match.group(1).strip()
            if not _looks_like_polis_number(number):
                # Without the digit requirement re.IGNORECASE turns
                # the [A-ZА-Я0-9]+ class into something that happily
                # eats Cyrillic words ("страхования"). Real polis
                # identifiers always carry digits — require at least
                # three to filter false-positives.
                continue
            fragment = self.take_fragment(text, match.start(), match.end())
            candidates.append(
                Candidate(
                    value={"display": number, "number": number},
                    state="found",
                    pattern_id="polis_keyword",
                    source_fragment=fragment,
                    span=(match.start(), match.end()),
                    components=ConfidenceComponents(
                        pattern_strength=0.55, context_strength=0.25,
                    ),
                )
            )

        # 3) Generic short keyword pattern.
        for match in _POLIS_SHORT_PATTERN.finditer(text):
            number = match.group(1).strip()
            if not _looks_like_polis_number(number):
                continue
            fragment = self.take_fragment(text, match.start(), match.end())
            candidates.append(
                Candidate(
                    value={"display": number, "number": number},
                    state="found",
                    pattern_id="polis_short",
                    source_fragment=fragment,
                    span=(match.start(), match.end()),
                    components=ConfidenceComponents(
                        pattern_strength=0.45, context_strength=0.2,
                    ),
                )
            )

        # 4) АльфаСтрахование XLS form-mask: 4-segment number with №.
        for match in _ALFA_XLS_NUMBER_PATTERN_WITH_NO.finditer(text):
            number = match.group(1).strip()
            fragment = self.take_fragment(text, match.start(), match.end())
            candidates.append(
                Candidate(
                    value={"display": number, "number": number},
                    state="found",
                    pattern_id="alfa_xls_number_with_no",
                    source_fragment=fragment,
                    span=(match.start(), match.end()),
                    components=ConfidenceComponents(
                        pattern_strength=0.6, context_strength=0.25,
                    ),
                )
            )

        # 5) Same form on a bare line (no № prefix). Strict 5/3/5/2 arity
        #    keeps the false-positive surface tiny.
        for match in _ALFA_XLS_NUMBER_PATTERN_BARE.finditer(text):
            number = match.group(1).strip()
            fragment = self.take_fragment(text, match.start(), match.end())
            candidates.append(
                Candidate(
                    value={"display": number, "number": number},
                    state="found",
                    pattern_id="alfa_xls_number_bare",
                    source_fragment=fragment,
                    span=(match.start(), match.end()),
                    components=ConfidenceComponents(
                        pattern_strength=0.55, context_strength=0.2,
                    ),
                )
            )

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
