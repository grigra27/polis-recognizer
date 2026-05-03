"""LimitParser — extract the sum insured (страховая сумма / лимит).

Three independent paths feed into the candidate pool:

1. Keyword pattern: "страховая сумма: 2 000 000 руб." / "лимит
   ответственности 500000 USD". Highest pattern strength.

2. Risk-row table parsing via the LayoutAnalyzer. КАСКО polises usually
   give the value in a multi-column row:

       Автокаско (Ущерб и Угон)  2000000,00  41245,50

   The LayoutAnalyzer returns columns; if a column header was detected
   as ``sum_insured`` we use that index, otherwise we fall back to the
   FIRST numeric column (the conventional ordering: sum, then premium).

3. Risk-priority: rows for "Автокаско" / "Ущерб + Угон" outrank rows
   for "Гражданская ответственность" (ГО). Pattern strength reflects
   that ordering.
"""

from __future__ import annotations

import re
from typing import List, Optional

from ..candidates import Candidate, ConfidenceComponents
from ..numeric import normalize_currency, parse_numeric
from ..tables import find_kasko_polnoe_row_signals, parse_signal_value
from .base import ExtractionContext, FieldParser


# Currency suffix accepted in keyword patterns. Same set as
# numeric.CURRENCY_TOKEN_RE; duplicated here so the captured group
# stays compatible with `normalize_currency`. Tolerates "руб" without a
# trailing dot (Tesseract sometimes drops it on noisy scans) and the
# bare "RUB" / "EUR" / "USD" letter forms occasionally seen in
# digital-PDF text-layers.
_CURRENCY_RE = r"(?:рублей|руб\.|руб\b|₽|RUB|RUR|USD|EUR|евро|долл\.?(?:\s+США)?)"

_KEYWORD_PATTERNS = [
    (
        "strahovaya_summa",
        re.compile(
            # "страхов\w+\s+сум\w*" instead of strict "страховая сумма" —
            # case forms (страховой/страховую сумму) plus OCR-mangled
            # endings ("страховас сумму") are common in Russian scans.
            r"страхов\w*\s+сум\w*\s*[:=\-—–]?\s*"
            r"(\d[\d ]*(?:[.,]\d{1,2})?)\s*"
            + r"(" + _CURRENCY_RE + r")",
            re.IGNORECASE,
        ),
        0.7, 0.3,
    ),
    (
        "limit_otvetstvennosti",
        re.compile(
            r"лимит\s+ответствен\w*\s*[:=\-—–]?\s*"
            r"(\d[\d ]*(?:[.,]\d{1,2})?)\s*"
            + r"(" + _CURRENCY_RE + r")",
            re.IGNORECASE,
        ),
        0.7, 0.3,
    ),
]

_RISK_LABELS = [
    # (label_pattern, risk_kind, pattern_strength, context_strength)
    (
        re.compile(r"(?:авто)?каско", re.IGNORECASE),
        "autocasco", 0.55, 0.25,
    ),
    (
        re.compile(r"ущерб\s*(?:и|\+)\s*угон", re.IGNORECASE),
        "usherb_ugon", 0.5, 0.2,
    ),
    (
        re.compile(r"гражданская\s+ответственность|^го\b", re.IGNORECASE),
        "go", 0.35, 0.15,
    ),
]


# Inline KEYWORD patterns for АльфаСтрахование/ВСК "two-row КАСКО" layouts:
#   9.2 «Повреждение или гибель ТС» 11000000.00 200000.00 - -
#   9.1 «Хищение ТС» 11000000.00 75000.00 - -
# LayoutAnalyzer.find_rows() doesn't reliably detect these rows because of
# the numbered prefix and «-quotes», so we match label + first number on
# the same line directly. Currency is resolved through
# `_detect_default_currency` because the inline match doesn't carry one.
_INLINE_RISK_PATTERNS = [
    (
        "povrezhdenie_ili_gibel_inline",
        re.compile(
            r"«?повреждение\s+или\s+гибель(?:\s+тс)?»?\s+"
            r"(\d[\d ]*(?:[.,]\d{1,2})?)",
            re.IGNORECASE,
        ),
        0.55, 0.25,
    ),
    (
        "khishchenie_ts_inline",
        re.compile(
            r"«?хищение\s+тс»?\s+"
            r"(\d[\d ]*(?:[.,]\d{1,2})?)",
            re.IGNORECASE,
        ),
        0.5, 0.2,
    ),
    # АльфаСтрахование XLS-converted layouts:
    #   1. КАСКО ПОЛНОЕ (ПОВРЕЖДЕНИЕ, ХИЩЕНИЕ) 12 000 000,00 0,00 322 800,00
    # First number is the sum insured, second is franchise (often 0,00 →
    # "no franchise"), third is premium. Numbered "1." prefix and the
    # parenthesized risk list throw off LayoutAnalyzer; matching on
    # the label keyword and pulling the first numeric works reliably.
    (
        "kasko_polnoe_inline",
        re.compile(
            r"каско\s+полное"            # "КАСКО ПОЛНОЕ"
            r"(?:\s*\([^)]*\))?"          # optional "(ПОВРЕЖДЕНИЕ, ХИЩЕНИЕ)"
            r"\s+"
            r"(\d[\d ]*(?:[.,]\d{1,2})?)",
            re.IGNORECASE,
        ),
        0.6, 0.25,
    ),
]

_CURRENCY_HINT_RE = re.compile(
    r"валюта\s+полиса\s*:?\s*рубл|руб\.|рублей|₽|RUB", re.IGNORECASE
)


def _detect_default_currency(text: str) -> Optional[str]:
    if _CURRENCY_HINT_RE.search(text):
        return "RUB"
    return None


class LimitParser(FieldParser):
    field_name = "limit"

    def parse(self, ctx: ExtractionContext) -> List[Candidate]:
        text = ctx.normalized.text
        candidates: List[Candidate] = []

        # 0. pdfplumber-extracted tables, if available. The first
        # numeric cell on the КАСКО ПОЛНОЕ row is the sum insured.
        # See `services/extraction/tables.py` for the row layout.
        if ctx.tables:
            signals = find_kasko_polnoe_row_signals(ctx.tables) or []
            for kind, raw in signals:
                if kind != "number":
                    continue
                value = parse_signal_value(raw)
                if value is None or value <= 0:
                    break
                candidates.append(
                    Candidate(
                        value={"value": value, "currency": "RUB"},
                        state="found",
                        pattern_id="alfa_kasko_polnoe_table",
                        source_fragment=f"table-row кАСКО ПОЛНОЕ: {raw}",
                        components=ConfidenceComponents(
                            pattern_strength=0.8, context_strength=0.3,
                        ),
                    )
                )
                break  # only the FIRST numeric cell is the sum insured

        # 1. Keyword patterns.
        for pattern_id, pattern, p_str, c_str in _KEYWORD_PATTERNS:
            for match in pattern.finditer(text):
                value = parse_numeric(match.group(1))
                currency = normalize_currency(match.group(2))
                if value is None or currency is None or value <= 0:
                    continue
                candidates.append(
                    Candidate(
                        value={"value": value, "currency": currency},
                        state="found",
                        pattern_id=pattern_id,
                        source_fragment=self.take_fragment(text, match.start(), match.end()),
                        span=(match.start(), match.end()),
                        components=ConfidenceComponents(
                            pattern_strength=p_str, context_strength=c_str,
                        ),
                    )
                )

        default_currency = _detect_default_currency(text)

        # 1b. Inline risk-keyword patterns (АльфаСтрахование/ВСК two-row КАСКО).
        for pattern_id, pattern, p_str, c_str in _INLINE_RISK_PATTERNS:
            for match in pattern.finditer(text):
                value = parse_numeric(match.group(1))
                if value is None or value <= 0:
                    continue
                candidates.append(
                    Candidate(
                        value={"value": value, "currency": default_currency},
                        state="found",
                        pattern_id=pattern_id,
                        source_fragment=self.take_fragment(text, match.start(), match.end()),
                        span=(match.start(), match.end()),
                        components=ConfidenceComponents(
                            pattern_strength=p_str, context_strength=c_str,
                        ),
                    )
                )

        # 2. Risk-row table parsing.
        for label_pattern, risk_kind, p_str, c_str in _RISK_LABELS:
            rows = ctx.layout.find_rows(ctx.normalized, label_pattern)
            for row in rows:
                if not row.columns:
                    continue
                # Pick the column. If a column header was detected, use
                # the ``sum_insured`` index; otherwise fall back to the
                # first numeric column (the conventional layout).
                header = ctx.layout.find_header(ctx.normalized, row.line_no)
                col_idx = 0
                if header is not None and "sum_insured" in header.column_kinds:
                    col_idx = header.column_kinds.index("sum_insured")
                if col_idx >= len(row.columns):
                    col_idx = 0
                value, raw = row.columns[col_idx]
                if value <= 0:
                    continue
                # Boost context_strength when we positively identified
                # the column via a header — that's stronger evidence.
                ctx_str = c_str + (0.15 if header is not None else 0.0)
                candidates.append(
                    Candidate(
                        value={"value": value, "currency": default_currency},
                        state="found",
                        pattern_id=f"row_{risk_kind}"
                        + (":header_classified" if header else ":positional"),
                        source_fragment=row.line_text,
                        components=ConfidenceComponents(
                            pattern_strength=p_str,
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
