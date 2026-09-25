"""Cross-field plausibility pass over policyholder ИНН / ОГРН / КПП.

Each identifier parser picks its winner in isolation, so nothing stops
the pipeline from pairing the policyholder's ИНН with the leasing
company's КПП (roadmap L2). The three identifiers carry redundant
structure that lets us notice — and often fix — such mismatches:

* **Kind.** 10-digit ИНН ↔ 13-digit ОГРН ↔ КПП (legal entity);
  12-digit ИНН ↔ 15-digit ОГРНИП, never a КПП (ИП / individual).
  A kind clash is a *hard* conflict: one of the values is wrong.
* **Region.** ИНН[0:2], ОГРН[3:5] and КПП[0:2] all encode the subject
  of the federation where the entity registered. Agreement is strong
  evidence the values belong together; disagreement is only a *soft*
  conflict — relocations, branches (КПП reason 43) and the largest
  taxpayers (КПП 99xx…) legitimately break it.

Swaps are deliberately conservative — a *majority* rule. A field's
winner is replaced only when the other two identifiers agree with each
other (same kind, same region) and both contradict it, and a runner-up
candidate agrees with both. With only two identifiers present we can't
tell which one is wrong, so we just annotate: a coherent-but-wrong pair
would be worse than an incoherent pair that carries a warning.

Conflicts that survive are recorded in the winners' ``notes``; hard
conflicts additionally lower their confidence.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .candidates import Candidate
from .validators import inn_region, kpp_region, ogrn_region

INN, OGRN, KPP = "policyholder_inn", "policyholder_ogrn", "policyholder_kpp"
FIELDS = (INN, OGRN, KPP)

HARD_CONFLICT_PENALTY = 0.7


def _is_legal(field: str, value: str) -> bool:
    """True for legal-entity identifiers, False for ИП / individual ones."""
    if field == INN:
        return len(value) == 10
    if field == OGRN:
        return len(value) == 13
    return True  # only legal entities have a КПП


def _region(field: str, value: str) -> Optional[str]:
    if field == INN:
        return inn_region(value)
    if field == OGRN:
        return ogrn_region(value)
    return kpp_region(value)


def pair_conflicts(
    field_a: str, value_a: str, field_b: str, value_b: str
) -> Tuple[bool, Optional[bool]]:
    """Compare two identifiers.

    Returns ``(hard_conflict, region_match)`` where ``region_match`` is
    ``None`` when a region can't be read from either value.
    """
    hard = _is_legal(field_a, value_a) != _is_legal(field_b, value_b)
    ra, rb = _region(field_a, value_a), _region(field_b, value_b)
    region_match = None if ra is None or rb is None else ra == rb
    return hard, region_match


def _agree(field_a: str, cand_a: Candidate, field_b: str, cand_b: Candidate) -> bool:
    """Positive evidence the two identifiers belong to the same entity."""
    hard, region_match = pair_conflicts(field_a, cand_a.value, field_b, cand_b.value)
    return not hard and region_match is True


def _conflict(field_a: str, cand_a: Candidate, field_b: str, cand_b: Candidate) -> bool:
    hard, region_match = pair_conflicts(field_a, cand_a.value, field_b, cand_b.value)
    return hard or region_match is False


def _found_values(candidates: List[Candidate]) -> List[Candidate]:
    """Distinct found values, strongest candidate per value, best first."""
    best: Dict[str, Candidate] = {}
    for c in candidates:
        if c.state != "found" or not isinstance(c.value, str):
            continue
        if c.value not in best or c.confidence > best[c.value].confidence:
            best[c.value] = c
    return sorted(best.values(), key=lambda c: -c.confidence)


def reconcile_identifiers(
    winners: Dict[str, Optional[Candidate]],
    candidates: Dict[str, List[Candidate]],
) -> Dict[str, Optional[Candidate]]:
    """Return updated winners for ИНН / ОГРН / КПП.

    ``winners`` / ``candidates`` are keyed by field name; fields other
    than the three identifiers are ignored. The returned dict only has
    entries for identifier fields whose winner changed or was annotated.
    A field is never emptied or newly filled — a found winner is only
    ever replaced by another found candidate of the same field.
    """
    current: Dict[str, Candidate] = {
        f: w
        for f in FIELDS
        if (w := winners.get(f)) is not None and w.state == "found" and isinstance(w.value, str)
    }
    if len(current) < 2:
        return {}
    updated: Dict[str, Optional[Candidate]] = {}

    if len(current) == 3:
        for field in FIELDS:
            fa, fb = (f for f in FIELDS if f != field)
            a, b, inc = current[fa], current[fb], current[field]
            if not _agree(fa, a, fb, b):
                continue
            if not (_conflict(field, inc, fa, a) and _conflict(field, inc, fb, b)):
                continue
            for alt in _found_values(candidates.get(field, [])):
                if alt.value == inc.value:
                    continue
                if _agree(field, alt, fa, a) and _agree(field, alt, fb, b):
                    alt.notes.append(f"consistency_reranked_over:{inc.value}")
                    current[field] = updated[field] = alt
                    break
            break  # at most one swap: a second would undo the majority

    present = [(f, current[f]) for f in FIELDS if f in current]
    for i, (fa, ca) in enumerate(present):
        for fb, cb in present[i + 1:]:
            hard, region_match = pair_conflicts(fa, ca.value, fb, cb.value)
            short = f"{fa.removeprefix('policyholder_')}/{fb.removeprefix('policyholder_')}"
            if hard:
                for f, c in ((fa, ca), (fb, cb)):
                    c.notes.append(f"identifier_kind_conflict:{short}")
                    c.components.consistency_penalty = HARD_CONFLICT_PENALTY
                    updated[f] = c
            elif region_match is False:
                for f, c in ((fa, ca), (fb, cb)):
                    c.notes.append(f"identifier_region_mismatch:{short}")
                    updated[f] = c
    return updated
