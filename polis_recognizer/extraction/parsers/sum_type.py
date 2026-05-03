"""SumTypeParser — classify the sum-insured as aggregate vs non-aggregate.

КАСКО polises declare whether the sum insured is *aggregate*
("агрегатная" — exhausts as claims are paid) or *non-aggregate*
("неагрегатная" — restored after each claim, full sum stays).

Common phrasings:
    "Страховая сумма по рискам «Автокаско», «Ущерб» — агрегатная — нет"
    "агрегатная: да"
    "неагрегатная страховая сумма"

State semantics:
- "агрегатная — нет"  → non_aggregate (logical inversion of the label)
- "агрегатная — да"  → aggregate
- "неагрегатная" alone (without negation) → non_aggregate
"""

from __future__ import annotations

import re
from typing import List

from ..candidates import Candidate, ConfidenceComponents
from .base import ExtractionContext, FieldParser


_AGG_LABEL_NEGATED = re.compile(
    r"агрегатн(?:ая|ой|ые)\b[^.\n]{0,40}?[-—–:=]\s*нет\b", re.IGNORECASE
)
_AGG_LABEL_AFFIRMED = re.compile(
    r"агрегатн(?:ая|ой|ые)\b[^.\n]{0,40}?[-—–:=]\s*да\b", re.IGNORECASE
)
_NEAGG_BARE = re.compile(r"\bнеагрегатн(?:ая|ой|ые)\b", re.IGNORECASE)
_AGG_BARE = re.compile(r"(?<!не)\bагрегатн(?:ая|ой|ые)\b", re.IGNORECASE)

# АльфаСтрахование form vocabulary: instead of "агрегатная/неагрегатная"
# they spell out the semantics — "С УМЕНЬШЕНИЕМ НА РАЗМЕР ВЫПЛАЧЕННОГО
# СТРАХОВОГО ВОЗМЕЩЕНИЯ" (sum decreases as claims pay out — aggregate)
# vs "БЕЗ УМЕНЬШЕНИЯ ..." (sum stays full — non-aggregate). Both clauses
# can appear in the same document because Альфа differentiates per risk
# point (e.g. "по пп. 1, 3, 5 БЕЗ УМЕНЬШЕНИЯ" / "по пп. 2, 4 С УМЕНЬШЕНИЕМ").
# When that happens we prefer non-aggregate (КАСКО полное standard) by
# giving it a slightly higher pattern strength.
_NEAGG_BEZ_UMENSHENIYA = re.compile(
    r"без\s+уменьшения\s+(?:на\s+размер\s+)?выплаченного\s+страхового\s+возмещения",
    re.IGNORECASE,
)
_AGG_S_UMENSHENIEM = re.compile(
    r"с\s+уменьшением\s+(?:на\s+размер\s+)?выплаченного\s+страхового\s+возмещения",
    re.IGNORECASE,
)


class SumTypeParser(FieldParser):
    field_name = "sum_type"

    def parse(self, ctx: ExtractionContext) -> List[Candidate]:
        text = ctx.normalized.text
        candidates: List[Candidate] = []

        # Strongest signal: "агрегатная - нет" (logical inversion).
        for match in _AGG_LABEL_NEGATED.finditer(text):
            candidates.append(
                Candidate(
                    value="non_aggregate",
                    state="found",
                    pattern_id="agg_label_negated",
                    source_fragment=self.take_fragment(text, match.start(), match.end()),
                    span=(match.start(), match.end()),
                    components=ConfidenceComponents(
                        pattern_strength=0.7, context_strength=0.3,
                    ),
                )
            )
        # Affirmed: "агрегатная - да".
        for match in _AGG_LABEL_AFFIRMED.finditer(text):
            candidates.append(
                Candidate(
                    value="aggregate",
                    state="found",
                    pattern_id="agg_label_affirmed",
                    source_fragment=self.take_fragment(text, match.start(), match.end()),
                    span=(match.start(), match.end()),
                    components=ConfidenceComponents(
                        pattern_strength=0.7, context_strength=0.3,
                    ),
                )
            )

        # АльфаСтрахование "БЕЗ УМЕНЬШЕНИЯ" wording (non-aggregate). Strong
        # signal — the phrase is unambiguous. Slightly higher than the
        # corresponding "С УМЕНЬШЕНИЕМ" so non-aggregate wins when both
        # are present in the same document (mixed per-point disclosure).
        if match := _NEAGG_BEZ_UMENSHENIYA.search(text):
            candidates.append(
                Candidate(
                    value="non_aggregate",
                    state="found",
                    pattern_id="alfa_bez_umensheniya",
                    source_fragment=self.take_fragment(text, match.start(), match.end()),
                    span=(match.start(), match.end()),
                    components=ConfidenceComponents(
                        pattern_strength=0.7, context_strength=0.3,
                    ),
                )
            )

        # АльфаСтрахование "С УМЕНЬШЕНИЕМ" wording (aggregate).
        if match := _AGG_S_UMENSHENIEM.search(text):
            candidates.append(
                Candidate(
                    value="aggregate",
                    state="found",
                    pattern_id="alfa_s_umensheniem",
                    source_fragment=self.take_fragment(text, match.start(), match.end()),
                    span=(match.start(), match.end()),
                    components=ConfidenceComponents(
                        pattern_strength=0.65, context_strength=0.3,
                    ),
                )
            )

        # Bare "неагрегатная" — moderate signal (label stand-alone).
        if _NEAGG_BARE.search(text):
            match = _NEAGG_BARE.search(text)
            candidates.append(
                Candidate(
                    value="non_aggregate",
                    state="found",
                    pattern_id="bare_neagregat",
                    source_fragment=self.take_fragment(text, match.start(), match.end()),
                    span=(match.start(), match.end()),
                    components=ConfidenceComponents(
                        pattern_strength=0.55, context_strength=0.2,
                    ),
                )
            )

        # Bare "агрегатная" with no neighbouring "нет/да" — weak signal,
        # often shows up inside a label that hasn't been answered yet.
        # We ONLY keep it if no negated/affirmed candidate already
        # resolved the field — otherwise it's ambiguous noise.
        if not candidates:
            for match in _AGG_BARE.finditer(text):
                candidates.append(
                    Candidate(
                        value="aggregate",
                        state="found",
                        pattern_id="bare_agregat",
                        source_fragment=self.take_fragment(text, match.start(), match.end()),
                        span=(match.start(), match.end()),
                        components=ConfidenceComponents(
                            pattern_strength=0.4, context_strength=0.1,
                        ),
                    )
                )
                break  # one bare match is enough

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
