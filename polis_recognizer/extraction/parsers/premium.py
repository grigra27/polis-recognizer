"""PremiumParser — extract the insurance premium amount.

Two paths:

1. Keyword: "Страховая премия (по Полису): 41 245,50 руб." Premium total
   summed up at the bottom of the table (e.g. ИТОГО) is also accepted
   when no per-risk row was found.

2. Risk-row table: in КАСКО tables the second numeric column is the
   premium. The parser leans on LayoutAnalyzer + column header
   classification (``"premium"``). Without a header the second column
   is taken positionally.

Only Автокаско / Ущерб+Угон rows are considered for the per-risk path
to avoid mixing ГО premium into the headline number.
"""

from __future__ import annotations

import re
from typing import List, Optional

from ..candidates import Candidate, ConfidenceComponents
from ..numeric import normalize_currency, parse_numeric
from ..tables import find_kasko_polnoe_row_signals, parse_signal_value
from .base import ExtractionContext, FieldParser


_KEYWORD_PATTERNS = [
    (
        "premium_explicit",
        re.compile(
            # ``[\s\S]{0,40}?`` between label and number absorbs prose
            # like "по настоящему полису составляет" that Ингосстрах
            # KASKO templates put in front of the value. Currency tokens
            # widened to match the same set as numeric.CURRENCY_TOKEN_RE,
            # including ``RUR`` (legacy code Ингосстрах still prints).
            r"страховая\s+премия(?:\s+по\s+полису)?[\s\S]{0,40}?\s*[:=\-—–]?\s*"
            r"(\d[\d ]*(?:[.,]\d{1,2})?)\s*"
            r"(руб\.?|рублей|₽|RUB|RUR|USD|EUR)",
            re.IGNORECASE,
        ),
        0.7, 0.3,
    ),
    (
        "itogo",
        re.compile(
            r"итого\s*[:=\-—–]?\s*"
            r"(\d[\d ]*(?:[.,]\d{1,2})?)\s*"
            r"(руб\.?|рублей|₽|RUB|RUR|USD|EUR)?",
            re.IGNORECASE,
        ),
        0.55, 0.2,
    ),
]


# АльфаСтрахование XLS-form КАСКО ПОЛНОЕ row, third numeric column
# is premium (sum / franchise-or-placeholder / premium). Mirrors the
# franchise parser's inline anchor: same atomic-group + same-line +
# 3-column requirement keep the engine from re-splitting digit-grouped
# numbers ("5 525 000,00") to fit a different layout.
#
# We don't reuse the franchise parser's regex directly because the
# capture group has to be the THIRD column here, not the second.
_INLINE_KASKO_POLNOE_PREMIUM_PATTERN = re.compile(
    r"каско[^\S\n]+полное"
    r"(?:[^\S\n]*\([^)]*\))?"
    r"[^\S\n]+(?>\d[\d ]*(?:[.,]\d{1,2})?)"            # limit (skipped)
    r"[^\S\n]+"
    r"(?:(?>\d[\d ]*(?:[.,]\d{1,2})?)|не\s+установлен[аоы]?|[—–\-])"  # franchise/placeholder (skipped)
    r"[^\S\n]+"
    r"((?>\d[\d ]*(?:[.,]\d{1,2})?))",                 # premium (captured)
    re.IGNORECASE,
)

_PREMIUM_RISK_LABELS = [
    re.compile(r"(?:авто)?каско", re.IGNORECASE),
    re.compile(r"ущерб\s*(?:и|\+)\s*угон", re.IGNORECASE),
]

_CURRENCY_HINT_RE = re.compile(
    r"валюта\s+полиса\s*:?\s*рубл|руб\.|рублей|₽|RUB", re.IGNORECASE
)


def _detect_default_currency(text: str) -> Optional[str]:
    if _CURRENCY_HINT_RE.search(text):
        return "RUB"
    return None


class PremiumParser(FieldParser):
    field_name = "premium"

    def parse(self, ctx: ExtractionContext) -> List[Candidate]:
        text = ctx.normalized.text
        candidates: List[Candidate] = []
        default_currency = _detect_default_currency(text)

        # 0. pdfplumber-extracted tables. The third numeric cell on
        # the КАСКО ПОЛНОЕ row is the premium (sum / franchise / premium).
        if ctx.tables:
            signals = find_kasko_polnoe_row_signals(ctx.tables) or []
            # Walk the row picking out numeric cells; the THIRD numeric
            # is premium. Absent placeholders count as a positional
            # slot but don't yield a number.
            numeric_count = 0
            premium_cell: Optional[str] = None
            for kind, raw in signals:
                if kind == "number":
                    numeric_count += 1
                    if numeric_count == 3:
                        premium_cell = raw
                        break
                elif kind == "absent":
                    # Placeholder consumes the franchise slot — keep counting.
                    numeric_count += 1
            if premium_cell:
                value = parse_signal_value(premium_cell)
                if value is not None and value > 0:
                    candidates.append(
                        Candidate(
                            value={"value": value, "currency": default_currency or "RUB"},
                            state="found",
                            pattern_id="alfa_kasko_polnoe_table",
                            source_fragment=f"table-row кАСКО ПОЛНОЕ: {premium_cell}",
                            components=ConfidenceComponents(
                                pattern_strength=0.8, context_strength=0.3,
                            ),
                        )
                    )

        # 1. Keyword.
        for pattern_id, pattern, p_str, c_str in _KEYWORD_PATTERNS:
            for match in pattern.finditer(text):
                value = parse_numeric(match.group(1))
                currency = (
                    normalize_currency(match.group(2)) if match.lastindex and match.lastindex >= 2
                    else default_currency
                ) or default_currency
                if value is None or value <= 0:
                    continue
                candidates.append(
                    Candidate(
                        value={"value": value, "currency": currency},
                        state="found",
                        pattern_id=pattern_id,
                        source_fragment=self.take_fragment(text, match.start(), match.end()),
                        span=(match.start(), match.end()),
                        components=ConfidenceComponents(
                            pattern_strength=p_str,
                            context_strength=c_str,
                        ),
                    )
                )

        # 1b. АльфаСтрахование КАСКО ПОЛНОЕ inline row — third number.
        for match in _INLINE_KASKO_POLNOE_PREMIUM_PATTERN.finditer(text):
            value = parse_numeric(match.group(1))
            if value is None or value <= 0:
                continue
            candidates.append(
                Candidate(
                    value={"value": value, "currency": default_currency or "RUB"},
                    state="found",
                    pattern_id="alfa_kasko_polnoe_inline",
                    source_fragment=self.take_fragment(text, match.start(), match.end()),
                    span=(match.start(), match.end()),
                    components=ConfidenceComponents(
                        pattern_strength=0.6, context_strength=0.25,
                    ),
                )
            )

        # 2. Per-risk row premium column.
        for label_pattern in _PREMIUM_RISK_LABELS:
            rows = ctx.layout.find_rows(ctx.normalized, label_pattern)
            for row in rows:
                if len(row.columns) < 2:
                    continue
                header = ctx.layout.find_header(ctx.normalized, row.line_no)
                col_idx = 1  # positional fallback: the SECOND column
                if header is not None and "premium" in header.column_kinds:
                    col_idx = header.column_kinds.index("premium")
                if col_idx >= len(row.columns):
                    continue
                value, _ = row.columns[col_idx]
                if value <= 0:
                    continue
                ctx_str = 0.25 + (0.15 if header else 0.0)
                candidates.append(
                    Candidate(
                        value={"value": value, "currency": default_currency},
                        state="found",
                        pattern_id="row_premium"
                        + (":header_classified" if header else ":positional"),
                        source_fragment=row.line_text,
                        components=ConfidenceComponents(
                            pattern_strength=0.5,
                            context_strength=ctx_str,
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
