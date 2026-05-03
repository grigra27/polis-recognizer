"""FranchiseParser — extract franchise (deductible) amount or absence.

Three classes of evidence, all yielded as Candidates:

1. Numeric value + currency (e.g. "Безусловная франшиза 30 000 руб.").
2. Numeric zero — reclassified to ``state="absent"`` because "0 руб."
   is functionally identical to "no franchise" in КАСКО context.
3. Textual absence ("франшиза - нет" / "не предусмотрена" /
   "не применяется" / "отсутствует") — multiline-tolerant.

The ranker picks ``found`` over ``absent`` so a real numeric value
always beats an absence statement. When only absence statements exist
they win over ``not_found``.
"""

from __future__ import annotations

import re
from typing import List

from ..candidates import Candidate, ConfidenceComponents
from ..numeric import normalize_currency, parse_numeric
from ..tables import find_kasko_polnoe_row_signals, parse_signal_value
from .base import ExtractionContext, FieldParser


# Currency suffix shared with the limit parser. Tolerates "руб" without
# a trailing dot (Tesseract drops it on noisy scans) and bare letter
# forms (RUB / EUR / USD) seen in some digital-PDF text-layers.
_CURRENCY_RE = r"(?:рублей|руб\.|руб\b|₽|RUB|RUR|USD|EUR|евро|долл\.?(?:\s+США)?)"


_NUMERIC_PATTERNS = [
    (
        "bezuslovnaya_numeric",
        re.compile(
            # Tolerant noun endings: безусловн[ая|ой|ую|ые] + франшиз[а|ы|ой|у|е].
            # Real samples include all four cases; a strict "безусловная
            # франшиза" pattern misses the inflected forms even though
            # they're routine in КАСКО prose.
            r"безусловн\w*\s+франшиз\w*\s*[:=\-—–]?\s*"
            r"(\d[\d ]*(?:[.,]\d{1,2})?)\s*"
            + r"(" + _CURRENCY_RE + r")",
            re.IGNORECASE,
        ),
        0.7, 0.3,
    ),
    (
        "uslovnaya_numeric",
        re.compile(
            r"условн\w*\s+франшиз\w*\s*[:=\-—–]?\s*"
            r"(\d[\d ]*(?:[.,]\d{1,2})?)\s*"
            + r"(" + _CURRENCY_RE + r")",
            re.IGNORECASE,
        ),
        0.65, 0.3,
    ),
    (
        "generic_franchise_numeric",
        re.compile(
            r"франшиз\w*\s*[:=\-—–]?\s*"
            r"(\d[\d ]*(?:[.,]\d{1,2})?)\s*"
            + r"(" + _CURRENCY_RE + r")",
            re.IGNORECASE,
        ),
        0.55, 0.25,
    ),
]


# АльфаСтрахование XLS-form layout: a single risk-row like
#   1. КАСКО ПОЛНОЕ (ПОВРЕЖДЕНИЕ, ХИЩЕНИЕ) 5 525 000,00 0,00 220 000,00
# whose columns are sum / franchise / premium. The franchise cell
# is one of:
#   - a positive number  (e.g. "30 000,00")
#   - a literal zero ("0,00" / "0.00") — meaning "no franchise"
#   - the placeholder "Не установлена" — same semantic as zero
# We anchor on "КАСКО полное" because that label's two-numbers-and-a-
# placeholder shape is unambiguous; other risk rows don't have a
# stable column ordering across insurers. Same-line constraint via
# [^\S\n] keeps the regex from absorbing values from the next row.
#
# All three numbers use atomic groups (`(?>...)`) so the regex engine
# can't backtrack and split a 3-digit-grouped number ("6 500 000.00")
# into shorter pieces to fit a different column layout. Without the
# atomic anchor the engine happily reinterprets the 2-column variant
# "sum  premium" as "sum-fragment  franchise  premium-fragment".
_INLINE_KASKO_POLNOE_PATTERN = re.compile(
    r"каско[^\S\n]+полное"
    r"(?:[^\S\n]*\([^)]*\))?"
    r"[^\S\n]+(?>\d[\d ]*(?:[.,]\d{1,2})?)"            # limit (skipped)
    r"[^\S\n]+"
    r"((?>\d[\d ]*(?:[.,]\d{1,2})?)|не\s+установлен[аоы]?|[—–\-])"  # franchise
    r"[^\S\n]+(?>\d[\d ]*(?:[.,]\d{1,2})?)",           # premium anchor
    re.IGNORECASE,
)


# Multiline-tolerant absence patterns. ``[\s\S]`` allows newlines so we
# match split layouts like "Безусловная франшиза по рискам ...\nв размере - нет".
_ABSENT_PATTERNS = [
    (
        "absent_v_razmere_net",
        re.compile(
            r"(?:безусловн(?:ая|ой|ые)|условн(?:ая|ой|ые))?\s*франшиза\b"
            r"[\s\S]{0,180}?в\s+размере\s*[-—–:]\s*нет\b",
            re.IGNORECASE,
        ),
        0.7, 0.3,
    ),
    (
        "absent_dash_net",
        re.compile(
            r"(?:безусловн(?:ая|ой|ые)|условн(?:ая|ой|ые))?\s*франшиза\b"
            r"[^.\n]{0,40}?\s*[-—–:=]\s*нет\b",
            re.IGNORECASE,
        ),
        0.6, 0.3,
    ),
    (
        "absent_ne_predusmotrena",
        re.compile(
            r"франшиза\b[\s\S]{0,80}?не\s+(?:предусмотрен|применяется)",
            re.IGNORECASE,
        ),
        0.65, 0.3,
    ),
    (
        "absent_otsutstvuet",
        re.compile(
            r"франшиза\b[\s\S]{0,40}?отсутствует",
            re.IGNORECASE,
        ),
        0.6, 0.3,
    ),
]


class FranchiseParser(FieldParser):
    field_name = "franchise"

    def parse(self, ctx: ExtractionContext) -> List[Candidate]:
        text = ctx.normalized.text
        candidates: List[Candidate] = []

        # 0. pdfplumber-extracted tables, if available. The Альфа KASKO
        # ПОЛНОЕ row gives us a clean (sum, franchise, premium) tuple
        # without the regex gymnastics needed on flat text. Highest
        # pattern strength because the column-typed signal is much less
        # ambiguous than positional regex on de-tabulated text.
        if ctx.tables:
            signals = find_kasko_polnoe_row_signals(ctx.tables) or []
            if len(signals) >= 2:
                # signals[0] = sum, signals[1] = franchise (number or
                # absent placeholder). Anything past index 2 is premium /
                # period and ignored here.
                kind, raw = signals[1]
                if kind == "absent":
                    candidates.append(
                        Candidate(
                            value={"value": 0, "currency": "RUB", "absent": True},
                            state="absent",
                            pattern_id="alfa_kasko_polnoe_table:placeholder_as_absent",
                            source_fragment=f"table-row кАСКО ПОЛНОЕ: {raw}",
                            components=ConfidenceComponents(
                                pattern_strength=0.8, context_strength=0.3,
                            ),
                        )
                    )
                elif kind == "number":
                    value = parse_signal_value(raw)
                    if value is not None and value <= 0:
                        candidates.append(
                            Candidate(
                                value={"value": 0, "currency": "RUB", "absent": True},
                                state="absent",
                                pattern_id="alfa_kasko_polnoe_table:zero_as_absent",
                                source_fragment=f"table-row кАСКО ПОЛНОЕ: {raw}",
                                components=ConfidenceComponents(
                                    pattern_strength=0.8, context_strength=0.3,
                                ),
                            )
                        )
                    elif value is not None:
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

        # 1. Numeric matches.
        for pattern_id, pattern, p_str, c_str in _NUMERIC_PATTERNS:
            for match in pattern.finditer(text):
                value = parse_numeric(match.group(1))
                currency = normalize_currency(match.group(2))
                if value is None or currency is None:
                    continue
                fragment = self.take_fragment(text, match.start(), match.end())
                # Numeric zero is functionally absent.
                if value <= 0:
                    candidates.append(
                        Candidate(
                            value={"value": 0, "currency": currency, "absent": True},
                            state="absent",
                            pattern_id=f"{pattern_id}:zero_as_absent",
                            source_fragment=fragment,
                            span=(match.start(), match.end()),
                            components=ConfidenceComponents(
                                pattern_strength=p_str, context_strength=c_str,
                            ),
                        )
                    )
                else:
                    candidates.append(
                        Candidate(
                            value={"value": value, "currency": currency},
                            state="found",
                            pattern_id=pattern_id,
                            source_fragment=fragment,
                            span=(match.start(), match.end()),
                            components=ConfidenceComponents(
                                pattern_strength=p_str, context_strength=c_str,
                            ),
                        )
                    )

        # 1b. АльфаСтрахование КАСКО ПОЛНОЕ row — second number column
        # is franchise. Treat zero / placeholder text as absent.
        for match in _INLINE_KASKO_POLNOE_PATTERN.finditer(text):
            raw = match.group(1).strip()
            fragment = self.take_fragment(text, match.start(), match.end())
            # Placeholder / dash → absent.
            if raw and not raw[0].isdigit():
                candidates.append(
                    Candidate(
                        value={"value": 0, "currency": "RUB", "absent": True},
                        state="absent",
                        pattern_id="alfa_kasko_polnoe_inline:placeholder_as_absent",
                        source_fragment=fragment,
                        span=(match.start(), match.end()),
                        components=ConfidenceComponents(
                            pattern_strength=0.6, context_strength=0.25,
                        ),
                    )
                )
                continue
            value = parse_numeric(raw)
            if value is None:
                continue
            if value <= 0:
                candidates.append(
                    Candidate(
                        value={"value": 0, "currency": "RUB", "absent": True},
                        state="absent",
                        pattern_id="alfa_kasko_polnoe_inline:zero_as_absent",
                        source_fragment=fragment,
                        span=(match.start(), match.end()),
                        components=ConfidenceComponents(
                            pattern_strength=0.6, context_strength=0.25,
                        ),
                    )
                )
            else:
                candidates.append(
                    Candidate(
                        value={"value": value, "currency": "RUB"},
                        state="found",
                        pattern_id="alfa_kasko_polnoe_inline",
                        source_fragment=fragment,
                        span=(match.start(), match.end()),
                        components=ConfidenceComponents(
                            pattern_strength=0.6, context_strength=0.25,
                        ),
                    )
                )

        # 2. Textual absence.
        for pattern_id, pattern, p_str, c_str in _ABSENT_PATTERNS:
            match = pattern.search(text)
            if not match:
                continue
            fragment = self.take_fragment(text, match.start(), match.end())
            candidates.append(
                Candidate(
                    value={"value": 0, "currency": "RUB", "absent": True},
                    state="absent",
                    pattern_id=pattern_id,
                    source_fragment=fragment,
                    span=(match.start(), match.end()),
                    components=ConfidenceComponents(
                        pattern_strength=p_str, context_strength=c_str,
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
