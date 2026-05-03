"""Candidate model with transparent confidence breakdown.

Each parser yields zero or more `Candidate` objects. A Candidate carries
not just a value but also the reasoning behind its confidence score,
which lets the ranker make informed picks and lets the admin diagnostic
view show *why* a particular field landed where it did.

The score formula is intentionally simple:

    base   = min(1.0, pattern_strength + context_strength)
    score  = base * negation_penalty * ambiguity_penalty

Both penalty multipliers default to 1.0 (no penalty); the ranker may
lower them when a competing candidate is close behind.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Literal, Optional, Tuple


State = Literal["found", "absent", "not_found"]


@dataclass
class ConfidenceComponents:
    """Transparent breakdown of a candidate's confidence score."""

    pattern_strength: float = 0.0
    context_strength: float = 0.0
    negation_penalty: float = 1.0
    ambiguity_penalty: float = 1.0

    def score(self) -> float:
        base = min(1.0, self.pattern_strength + self.context_strength)
        return round(base * self.negation_penalty * self.ambiguity_penalty, 3)

    def to_dict(self) -> dict:
        return {
            "pattern_strength": round(self.pattern_strength, 3),
            "context_strength": round(self.context_strength, 3),
            "negation_penalty": round(self.negation_penalty, 3),
            "ambiguity_penalty": round(self.ambiguity_penalty, 3),
            "score": self.score(),
        }


@dataclass
class Candidate:
    """A single piece of evidence produced by a parser.

    Attributes:
        value: The extracted value, shape parser-specific.
        state: ``found`` / ``absent`` / ``not_found``. Drives ranker
            tie-breaks: a found candidate beats an absent one, even if
            their numeric scores are close.
        pattern_id: Stable identifier of the rule that produced this
            candidate. Surfaces in diagnostics for debugging.
        source_fragment: Short slice of normalized text around the match
            (capped at 240 chars).
        span: ``(start, end)`` char span in the normalized text.
        components: Confidence breakdown. ``confidence`` is derived.
        notes: Free-form trace breadcrumbs (e.g. ``"runner_up: pid_x"``).
    """

    value: Any
    state: State
    pattern_id: str
    source_fragment: str
    span: Optional[Tuple[int, int]] = None
    components: ConfidenceComponents = field(default_factory=ConfidenceComponents)
    notes: List[str] = field(default_factory=list)

    @property
    def confidence(self) -> float:
        return self.components.score()

    def to_dict(self) -> dict:
        return {
            "value": self.value,
            "state": self.state,
            "pattern_id": self.pattern_id,
            "confidence": self.confidence,
            "components": self.components.to_dict(),
            "source_fragment": self.source_fragment,
            "span": list(self.span) if self.span else None,
            "notes": list(self.notes),
        }
