"""RepairModeParser — classify the repair mode (dealer / service / cash).

Three modes:
- ``dealer`` — repair at the official dealer's STO
- ``service`` — repair at any contracted СТОА (service station)
- ``cash`` — monetary payout instead of repair

The challenge: КАСКО polises mention all three modes in some context
(carve-outs, exclusions, alternatives). The parser uses the
NegationContext detector to penalize candidates inside negative clauses
("за исключением ремонта на СТОА официального дилера"). Multiple
positive mentions reinforce a candidate (frequency boost) up to a cap.
"""

from __future__ import annotations

import re
from typing import Dict, List

from ..candidates import Candidate, ConfidenceComponents
from .base import ExtractionContext, FieldParser


_MODE_PATTERNS = [
    (
        "dealer",
        [
            (
                "dealer_explicit",
                # Tolerate up to 3 intervening tokens between "Ремонт"
                # and "осуществляется/производится" — real КАСКО texts
                # often write "Ремонт ТС осуществляется на СТОА ..."
                #
                # Two changes vs the original:
                # 1. ``ремонт\w*`` consumes the noun's inflection
                #    (ремонт/ремонта/ремонту/ремонтом). The bare ``ремонт``
                #    used to match the prefix only and then expect \s+
                #    on the next char — failing for "ремонта ТС ...".
                # 2. The verb-and-its-trailing-space are wrapped as one
                #    optional group; the previous form required \s+ on
                #    both sides of the optional verb, which fails when
                #    the verb is absent ("ремонта ТС на СТОА" has only
                #    one space between "ТС" and "на", not two).
                re.compile(
                    r"ремонт\w*(?:\s+\S+){0,3}?\s+"
                    r"(?:(?:осуществляется|производится)\s+)?"
                    r"(?:на|у)\s+(?:стоа\s+)?официальн(?:ого|ой)\s+дилер(?:а|е)",
                    re.IGNORECASE,
                ),
                0.7,
            ),
            (
                "dealer_naprav",
                re.compile(
                    r"направление\s+на\s+ремонт\s+(?:к|на)\s+(?:стоа\s+)?"
                    r"официальн(?:ого|ой)\s+дилер(?:а|е)",
                    re.IGNORECASE,
                ),
                0.65,
            ),
            (
                "dealer_keyword",
                re.compile(r"официальн(?:ый|ого)\s+дилер", re.IGNORECASE),
                0.45,
            ),
        ],
    ),
    (
        "cash",
        [
            (
                "cash_form",
                re.compile(
                    r"форма\s+возмещения\s*:?\s*денежн(?:ая|ой)\s+выплат(?:а|ы)",
                    re.IGNORECASE,
                ),
                0.7,
            ),
            (
                "cash_v_forme",
                re.compile(
                    r"возмещение\s+(?:в\s+форме\s+)?денежн(?:ой|ая)\s+выплат(?:ы|а)",
                    re.IGNORECASE,
                ),
                0.65,
            ),
            (
                "cash_keyword",
                re.compile(r"денежн(?:ая|ой)\s+выплат(?:а|ы)", re.IGNORECASE),
                0.45,
            ),
        ],
    ),
    (
        "service",
        [
            (
                "service_remont_stoa",
                # Same noun-inflection + verb-optional-with-trailing-space
                # treatment as in dealer_explicit. Common Альфа phrasing
                # "ремонта повреждённого ТС на СТОА" needed both fixes:
                # ``ремонт\w*`` to consume the case ending and the verb
                # group changed to ``(?:VERB\s+)?`` so we don't require
                # two spaces around the (absent) verb.
                re.compile(
                    r"ремонт\w*(?:\s+\S+){0,3}?\s+"
                    r"(?:(?:осуществляется|производится)\s+)?"
                    r"(?:на|у)\s+стоа\b",
                    re.IGNORECASE,
                ),
                0.6,
            ),
            (
                "service_naprav_stoa",
                re.compile(
                    r"направление\s+на\s+ремонт\s+(?:на|в)\s+стоа\b",
                    re.IGNORECASE,
                ),
                0.55,
            ),
            (
                "service_sto_full",
                re.compile(
                    r"станци(?:я|и)\s+технического\s+обслуживания",
                    re.IGNORECASE,
                ),
                0.4,
            ),
        ],
    ),
]


class RepairModeParser(FieldParser):
    field_name = "repair_mode"

    def parse(self, ctx: ExtractionContext) -> List[Candidate]:
        text = ctx.normalized.text
        # Aggregate mode evidence: each mode collects positive/negative
        # match counts and the strongest positive match for the source
        # fragment.
        agg: Dict[str, dict] = {}

        for mode, patterns in _MODE_PATTERNS:
            for pattern_id, pattern, p_str in patterns:
                for match in pattern.finditer(text):
                    penalty = ctx.negation.penalty(text, match.start())
                    bucket = agg.setdefault(
                        mode,
                        {
                            "positive_hits": 0,
                            "negative_hits": 0,
                            "best_p_str": 0.0,
                            "best_pattern_id": "",
                            "best_span": None,
                            "best_fragment": "",
                            "negation_reason": None,
                        },
                    )
                    if penalty < ctx.negation.NO_PENALTY - 0.01:
                        bucket["negative_hits"] += 1
                        bucket["negation_reason"] = ctx.negation.reason(penalty)
                    else:
                        bucket["positive_hits"] += 1
                        if p_str > bucket["best_p_str"]:
                            bucket["best_p_str"] = p_str
                            bucket["best_pattern_id"] = pattern_id
                            bucket["best_span"] = (match.start(), match.end())
                            bucket["best_fragment"] = self.take_fragment(
                                text, match.start(), match.end()
                            )

        candidates: List[Candidate] = []
        for mode, bucket in agg.items():
            positives = bucket["positive_hits"]
            negatives = bucket["negative_hits"]
            if positives == 0:
                continue
            # Frequency boost: 2+ positive mentions add a small context
            # bonus (capped). Each negation in the same mode subtracts.
            freq_bonus = min(0.2, 0.05 * (positives - 1))
            neg_penalty_components = max(0.5, 1.0 - 0.25 * negatives)
            components = ConfidenceComponents(
                pattern_strength=bucket["best_p_str"],
                context_strength=0.2 + freq_bonus,
                negation_penalty=neg_penalty_components,
            )
            notes = []
            if negatives:
                notes.append(f"negation_hits:{negatives}")
            if bucket["negation_reason"]:
                notes.append(bucket["negation_reason"])
            candidates.append(
                Candidate(
                    value=mode,
                    state="found",
                    pattern_id=bucket["best_pattern_id"],
                    source_fragment=bucket["best_fragment"],
                    span=bucket["best_span"],
                    components=components,
                    notes=notes,
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
