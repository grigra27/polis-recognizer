"""PolicyholderPhonesParser — extract policyholder phone numbers.

Russian phone formats on insurance documents are diverse:

    +7 (495) 123-45-67
    +74951234567
    8(495)1234567
    8-495-123-45-67
    (495) 123-45-67          (no country code)

The parser collects all phone-shaped runs inside the policyholder
block (or inside tables that anchor on "Страхователь"), normalises
them to E.164 (``+7XXXXXXXXXX``), dedupes, and emits one Candidate
whose ``value`` is the resulting ``list[str]``.

Prefix discipline: we accept only ``+7`` or ``8`` as the country
prefix, and ``(XXX)``-parenthesised numbers without prefix (assumed
``+7``). Bare 11-digit runs starting with ``7`` are deliberately NOT
accepted — too easy to collide with the leading 7 of an ИНН-12.

The parser only emits when at least one phone-shaped run was found
AND it normalised successfully — no partial-match weirdness.
"""

from __future__ import annotations

import re
from typing import List, Optional

from ..candidates import Candidate, ConfidenceComponents
from ..policyholder_block import (
    locate_policyholder_block,
    policyholder_table_rows,
)
from .base import ExtractionContext, FieldParser


# Path A: ``+7`` or ``8`` prefix, optional separators throughout.
# Total run after stripping non-digits will be exactly 11.
_PHONE_PREFIXED_RE = re.compile(
    r"(?:\+7|\b8)"
    r"[\s\-(]*\d{3}[\s\-)]*"
    r"\d{3}[\s\-]*"
    r"\d{2}[\s\-]*"
    r"\d{2}"
)

# Path B: parenthesised area code without country prefix. The negative
# look-behind blocks matches that already belong to a prefixed phone.
_PHONE_PARENED_RE = re.compile(
    r"(?<![\d+])"
    r"\(\d{3}\)\s*\d{3}[\s\-]?\d{2}[\s\-]?\d{2}"
)


def _is_placeholder_number(local: str) -> bool:
    """True iff a 10-digit local part is a template placeholder.

    Examples seen on real АльянсЛизинг / РЕСО form-mask polises:
    ``1111111111``, ``0000000000``, ``9999999999`` — those are
    obvious "default" / "empty" markers that downstream consumers
    should NOT treat as real phone numbers.
    """
    if len(local) != 10:
        return False
    # All-same digit run.
    if len(set(local)) == 1:
        return True
    return False


def _normalize_phone(raw: str) -> Optional[str]:
    """Strip to digits, return ``+7XXXXXXXXXX`` or ``None`` if not a fit.

    Rejects placeholder runs (all-same digit local part) — those
    appear as empty-field defaults on lizinging-contract templates
    and are useless to downstream callers.
    """
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits[0] in ("7", "8"):
        local = digits[1:]
        if _is_placeholder_number(local):
            return None
        return "+7" + local
    if len(digits) == 10:
        if _is_placeholder_number(digits):
            return None
        return "+7" + digits
    return None


def _scan(text: str) -> List[str]:
    out: List[str] = []
    for match in _PHONE_PREFIXED_RE.finditer(text):
        out.append(match.group(0))
    for match in _PHONE_PARENED_RE.finditer(text):
        out.append(match.group(0))
    return out


class PolicyholderPhonesParser(FieldParser):
    field_name = "policyholder_phones"

    def parse(self, ctx: ExtractionContext) -> List[Candidate]:
        raw_matches: List[str] = []
        text = ctx.normalized.text
        span = None

        block = locate_policyholder_block(ctx.normalized)
        if block is not None:
            start, end = block
            block_text = text[start:end]
            block_matches = _scan(block_text)
            if block_matches:
                span = (start, end)
                raw_matches.extend(block_matches)

        for page in ctx.tables or []:
            for table in page or []:
                rows = policyholder_table_rows(table)
                if not rows:
                    continue
                for row in rows:
                    for cell in row or []:
                        if not cell:
                            continue
                        raw_matches.extend(_scan(cell))

        # Normalize + dedup, preserve order of first appearance.
        seen = set()
        ordered: List[str] = []
        for raw in raw_matches:
            normalized = _normalize_phone(raw)
            if normalized is None or normalized in seen:
                continue
            seen.add(normalized)
            ordered.append(normalized)

        if not ordered:
            return [
                Candidate(
                    value=None,
                    state="not_found",
                    pattern_id="no_pattern_match",
                    source_fragment="",
                )
            ]

        fragment = ""
        if span is not None:
            fragment = self.take_fragment(text, span[0], min(span[0] + 60, span[1]))
        return [
            Candidate(
                value=ordered,
                state="found",
                pattern_id="block_scan",
                source_fragment=fragment or ", ".join(ordered)[:240],
                span=span,
                components=ConfidenceComponents(
                    pattern_strength=0.65,
                    context_strength=0.25,
                ),
            )
        ]
