"""Pick a single winning Candidate from a parser's output.

State priority is enforced first:
    found > absent > not_found

Within the same state the highest-confidence candidate wins. When the
runner-up is within ``CLOSE_GAP`` of the winner, the winner gets an
ambiguity penalty (its score drops by ``AMBIGUITY_PENALTY_FACTOR``)
because we genuinely cannot be sure which match is correct.

The ambiguity logic is symmetric: it doesn't care which pattern the
runner-up came from, only that another plausible candidate exists.
"""

from __future__ import annotations

from typing import List, Optional

from .candidates import Candidate


_STATE_PRIORITY = {"found": 0, "absent": 1, "not_found": 2}


class CandidateRanker:
    CLOSE_GAP = 0.15
    AMBIGUITY_PENALTY_FACTOR = 0.85

    def best(self, candidates: List[Candidate]) -> Optional[Candidate]:
        if not candidates:
            return None

        # Sort by (state priority asc, confidence desc).
        ranked = sorted(
            candidates,
            key=lambda c: (
                _STATE_PRIORITY.get(c.state, 99),
                -c.confidence,
            ),
        )
        winner = ranked[0]

        # If the next candidate is in the same state and very close in
        # confidence, the winner is genuinely ambiguous — apply a
        # multiplicative penalty so downstream confidence-gating sees
        # the uncertainty.
        if len(ranked) >= 2:
            runner = ranked[1]
            if runner.state == winner.state:
                gap = winner.confidence - runner.confidence
                if 0 <= gap < self.CLOSE_GAP:
                    winner.components.ambiguity_penalty = (
                        self.AMBIGUITY_PENALTY_FACTOR
                    )
                    winner.notes.append(f"close_runner_up:{runner.pattern_id}")

        return winner
