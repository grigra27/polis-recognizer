"""Generic negation-context detector.

Insurance text loves expressions like

    "Ремонт ТС осуществляется на СТОА официального дилера, за исключением
     ремонта стекол"

If a parser sees "официального дилера" it must know the surrounding
clause is a CARVE-OUT, not the policy's primary repair mode. Likewise
for "не на СТОА", "кроме случаев".

The detector returns a multiplier in (0, 1] applied to the candidate's
confidence so a parser doesn't have to drop the match entirely — it can
keep the candidate but lose to a competing positive match.

This used to live inside a single field's parser. v2 makes it generic
because the same problem applies to every field that competes between
positive and negative mentions (repair_mode, sum_type, risks_covered).
"""

from __future__ import annotations

import re
from typing import Optional


_NEGATION_BEFORE_PATTERN = re.compile(
    r"(?:за\s+исключением|исключая|кроме|за\s+минусом|вместо|"
    r"не\s+на|не\s+у|не\s+в|не\s+для)",
    re.IGNORECASE,
)

# Single bare "не " immediately preceding the matched span (within 6 chars).
_BARE_NEGATION_TAIL = re.compile(r"\bне\b\s*\S{0,6}$", re.IGNORECASE)


class NegationContext:
    """Decide whether a span sits inside a negated clause.

    Two penalty levels:
    - 0.2 (strong) when an explicit "за исключением" / "кроме" is found
      in the lookback window.
    - 0.55 (weak) when only a bare "не " precedes the span.
    - 1.0 (no penalty) otherwise.
    """

    STRONG_PENALTY = 0.2
    WEAK_PENALTY = 0.55
    NO_PENALTY = 1.0

    # Russian КАСКО clauses are wordy: "за исключением" / "кроме" often
    # opens a paragraph and the negated span follows after several lines.
    # 80 chars cut off mid-sentence on those — bumped to 200 so a typical
    # multi-line carve-out still gets the strong penalty.
    DEFAULT_LOOKBACK = 200

    def penalty(
        self,
        text: str,
        span_start: int,
        *,
        lookback_chars: int = DEFAULT_LOOKBACK,
    ) -> float:
        if span_start <= 0:
            return self.NO_PENALTY
        window = text[max(0, span_start - lookback_chars):span_start]
        if _NEGATION_BEFORE_PATTERN.search(window):
            return self.STRONG_PENALTY
        if _BARE_NEGATION_TAIL.search(window):
            return self.WEAK_PENALTY
        return self.NO_PENALTY

    def reason(self, penalty: float) -> Optional[str]:
        if penalty <= self.STRONG_PENALTY + 0.01:
            return "negation_strong"
        if penalty <= self.WEAK_PENALTY + 0.01:
            return "negation_weak"
        return None
