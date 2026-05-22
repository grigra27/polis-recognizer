"""PolicyPeriodParser — extract the start/end dates of the insurance period.

Patterns are ordered by specificity. Each one carries an explicit
``pattern_strength`` so the ranker can compare candidates from the same
parser meaningfully (a label-anchored pattern beats a bare "с DATE по
DATE" if both match different ranges).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import List, Optional, Tuple

from ..candidates import Candidate, ConfidenceComponents
from ..dates import parse_russian_date
from .base import ExtractionContext, FieldParser


_DATE_LITERAL = r"\d{1,2}\.\d{1,2}\.\d{4}"
# Optional Russian "year" suffix: "27.02.2026 г." or "27.02.2026г". 67% of
# real-world policies in our corpus put дата followed by " г." — without
# this tail the date pattern collapses against the next token.
_DATE_TAIL = r"(?:\s*г\.?)?"
_TIME_PREFIX = r"(?:\d{1,2}:\d{2}\s+)?"

# "3 апреля 2026" / "17 апреля 2026" — VSK-style policies print dates in
# Russian textual form. Stems cover all genitive variants (апреля/мая/июля).
_MONTH_STEM = (
    r"(?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)"
    r"\w*"
)
_DATE_TEXTUAL = rf"\d{{1,2}}\s+{_MONTH_STEM}\s+\d{{4}}"  # placeholder-guard: ignore
# Same shape but
#  - allows «-quotes» around the day number (Чулпан scans);
#  - tolerates underscores / dashes inserted by Tesseract between tokens
#    on noisy scans, e.g. "« 25» марта _ 2025 года". `[\s_\-—–]+` matches
#    a run of whitespace, underscores, hyphens, en/em-dashes — none of
#    which can plausibly appear inside a real date component, so the
#    looseness can't merge unrelated tokens.
_DATE_QUOTED_SEP = r"[\s_\-—–]+"
_DATE_TEXTUAL_QUOTED = rf"«?\s*\d{{1,2}}\s*»?{_DATE_QUOTED_SEP}{_MONTH_STEM}{_DATE_QUOTED_SEP}\d{{4}}"  # placeholder-guard: ignore
# "00 час. 00 мин." — also VSK; optional and tolerant of dot/no-dot.
_TIME_TEXTUAL_PREFIX = r"(?:\d{1,2}\s+час\.?\s+\d{1,2}\s+мин\.?\s+)?"

_PATTERNS = [
    # 1. Strong label + optional time prefix on each bound.
    (
        "label_anchored",
        re.compile(
            rf"срок\s+действия\s+(?:полиса|договора|страхования)\s*:?\s*"
            rf"с\s+{_TIME_PREFIX}({_DATE_LITERAL}){_DATE_TAIL}\s+"
            rf"по\s+{_TIME_PREFIX}({_DATE_LITERAL}){_DATE_TAIL}",
            re.IGNORECASE,
        ),
        0.7,  # pattern_strength
        0.3,  # context_strength
    ),
    # 2. "период страхования: DATE - DATE"
    (
        "period_label",
        re.compile(
            rf"период\s+страхования\s*:?\s*({_DATE_LITERAL}){_DATE_TAIL}\s*[-–—]\s*({_DATE_LITERAL}){_DATE_TAIL}",
            re.IGNORECASE,
        ),
        0.6,
        0.3,
    ),
    # 3. "действует с DATE по DATE"
    (
        "deystvuet_s",
        re.compile(
            rf"действует\s+с\s+({_DATE_LITERAL}){_DATE_TAIL}\s+по\s+({_DATE_LITERAL}){_DATE_TAIL}",
            re.IGNORECASE,
        ),
        0.55,
        0.25,
    ),
    # 4. Generic "с [HH:MM] DATE по [HH:MM] DATE" — weakest because it
    #    can match any "from-to" date prose, not just the policy period.
    (
        "generic_s_po",
        re.compile(
            rf"с\s+{_TIME_PREFIX}({_DATE_LITERAL}){_DATE_TAIL}\s+по\s+{_TIME_PREFIX}({_DATE_LITERAL}){_DATE_TAIL}",
            re.IGNORECASE,
        ),
        0.4,
        0.15,
    ),
    # 5. Label-anchored textual dates: "Срок действия ... с 00 час. 00 мин.
    #    3 апреля 2026 до 23 час. 59 мин. 2 апреля 2027". VSK template.
    #    Tolerates "по" or "до" as the upper bound separator.
    (
        "label_anchored_textual",
        re.compile(
            rf"срок\s+действия\s+(?:полиса|договора|страхования)[\s\S]{{0,40}}?"
            rf"с\s+{_TIME_TEXTUAL_PREFIX}({_DATE_TEXTUAL})\s+"
            rf"(?:по|до)\s+{_TIME_TEXTUAL_PREFIX}({_DATE_TEXTUAL})",
            re.IGNORECASE,
        ),
        0.7,
        0.3,
    ),
    # 6. Чулпан-style scans where OCR mangles the "годапо" connector
    #    between two textual dates into nonsense ("romano", "rodapo" etc.).
    #    Real example seen in the corpus:
    #
    #      "Срок действия договоре « 20 » ноября 2025 romano « 19 » ноября 2026 гола"
    #
    #    The bridge between dates can be 0-30 chars of arbitrary noise; we
    #    only require that BOTH ends are textual dates (numeric day +
    #    Russian month word + 4-digit year). Two textual dates appearing
    #    that close together right after "Срок действия" are extremely
    #    likely the policy period — the ambiguity penalty is acceptable.
    #    Day may be wrapped in «», noun ending tolerant (договора /
    #    договоре / договору, OCR commonly drops the trailing letter).
    (
        "label_anchored_textual_quoted",
        re.compile(
            rf"срок\s+действия\s+(?:полис[аеу]?|договор[аеу]?|страхования)"
            rf"[\s\S]{{0,40}}?"
            rf"({_DATE_TEXTUAL_QUOTED})"
            rf"[\s\S]{{0,30}}?"
            rf"({_DATE_TEXTUAL_QUOTED})",
            re.IGNORECASE,
        ),
        0.7,
        0.3,
    ),
]


# Form-mask templates from АльфаСтрахование. pypdf cannot reconstruct
# row order from per-cell text frames so dates show up as a flat
# sequence right after the table header "РУБЛЬ ЭКВ. ДОЛЛАРА США ЭКВ.
# ЕВРО". The first two dates form the headline coverage period (start
# of risk → end of risk for the first year). 2-digit years are accepted
# (e.g. "11 03 26" → 2026) — that's how Альфа prints the abbreviated
# form on multi-year policies.
_POSITIONAL_ALFA_PATTERN = re.compile(
    r"РУБЛЬ\s+ЭКВ\.?\s+ДОЛЛАРА\s+США\s+ЭКВ\.?\s+ЕВРО\s+"
    r"(\d{1,2})\s+(\d{1,2})\s+(\d{2,4})\s+"
    r"(\d{1,2})\s+(\d{1,2})\s+(\d{2,4})",
    re.IGNORECASE,
)
_POSITIONAL_ALFA_STRENGTH = 0.65
_POSITIONAL_ALFA_CONTEXT = 0.3


# Alias kept for in-module readability; semantics identical to
# extraction.dates.parse_russian_date.
_parse_iso = parse_russian_date


class PolicyPeriodParser(FieldParser):
    field_name = "policy_period"

    def parse(self, ctx: ExtractionContext) -> List[Candidate]:
        text = ctx.normalized.text
        seen: set = set()
        candidates: List[Candidate] = []

        for pattern_id, pattern, p_strength, c_strength in _PATTERNS:
            for match in pattern.finditer(text):
                start_iso = _parse_iso(match.group(1))
                end_iso = _parse_iso(match.group(2))
                if not start_iso or not end_iso:
                    continue
                # Deduplicate identical date ranges across patterns (keep
                # the strongest one because patterns iterate strongest-
                # first).
                key = (start_iso, end_iso)
                if key in seen:
                    continue
                seen.add(key)

                fragment = self.take_fragment(text, match.start(), match.end())
                candidates.append(
                    Candidate(
                        value={"start": start_iso, "end": end_iso},
                        state="found",
                        pattern_id=pattern_id,
                        source_fragment=fragment,
                        span=(match.start(), match.end()),
                        components=ConfidenceComponents(
                            pattern_strength=p_strength,
                            context_strength=c_strength,
                        ),
                    )
                )

        # АльфаСтрахование form-mask: dates after table header.
        for match in _POSITIONAL_ALFA_PATTERN.finditer(text):
            d1, m1, y1, d2, m2, y2 = match.groups()
            start_iso = _parse_iso(f"{d1}.{m1}.{y1}")
            end_iso = _parse_iso(f"{d2}.{m2}.{y2}")
            if not start_iso or not end_iso:
                continue
            key = (start_iso, end_iso)
            if key in seen:
                continue
            seen.add(key)
            fragment = self.take_fragment(text, match.start(), match.end())
            candidates.append(
                Candidate(
                    value={"start": start_iso, "end": end_iso},
                    state="found",
                    pattern_id="positional_alfa_form",
                    source_fragment=fragment,
                    span=(match.start(), match.end()),
                    components=ConfidenceComponents(
                        pattern_strength=_POSITIONAL_ALFA_STRENGTH,
                        context_strength=_POSITIONAL_ALFA_CONTEXT,
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
                    components=ConfidenceComponents(),
                )
            )
        return candidates
