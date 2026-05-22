"""PolicyholderOGRNParser — extract policyholder's ОГРН / ОГРНИП.

ОГРН (13 digits, legal entity) and ОГРНИП (15 digits, individual
entrepreneur) are emitted into the same ``policyholder_ogrn`` field;
the schema doesn't distinguish them at the surface — the length is
the discriminator if downstream cares (and ``policyholder.type``
already says legal_entity for both).

Both have published checksums; validation is mandatory.

Strategy mirrors the ИНН parser:

1. **Table strategy** — table containing a "Страхователь" anchor cell
   AND an "ОГРН(ИП)?" label cell.
2. **Anchored text strategy** — within the policyholder block, find
   ОГРН(ИП)? labels followed by validated digits.

Guard: we don't emit ОГРН from outside the policyholder block — the
signature/footer block carries the insurer's ОГРН and would otherwise
leak in as a false positive.
"""

from __future__ import annotations

import re
from typing import List

from ..candidates import Candidate, ConfidenceComponents
from ..policyholder_block import locate_policyholder_block
from ..validators import validate_ogrn_13, validate_ogrn_15
from .base import ExtractionContext, FieldParser


_OGRN_DIGITS_RE = re.compile(r"(?<!\d)(\d{13}|\d{15})(?!\d)")
_OGRN_LABEL_AND_DIGITS_RE = re.compile(
    r"ОГРН(?:ИП)?\b[\s:№#\-]*(\d{13}|\d{15})(?!\d)", re.IGNORECASE
)

_OGRN_TABLE_LABEL_RE = re.compile(r"^\s*ОГРН(?:ИП)?\b", re.IGNORECASE)
_STRAKH_TABLE_LABEL_RE = re.compile(
    r"^\s*(?:Страхователь|СТРАХОВАТЕЛЬ)\b"
)


def _validate_any_length(s: str) -> bool:
    if len(s) == 13:
        return validate_ogrn_13(s)
    if len(s) == 15:
        return validate_ogrn_15(s)
    return False


def _table_has_policyholder_anchor(table) -> bool:
    for row in table or []:
        for cell in row or []:
            if cell and _STRAKH_TABLE_LABEL_RE.match(cell):
                return True
    return False


class PolicyholderOGRNParser(FieldParser):
    field_name = "policyholder_ogrn"

    def parse(self, ctx: ExtractionContext) -> List[Candidate]:
        candidates: List[Candidate] = []
        candidates.extend(self._from_tables(ctx))
        candidates.extend(self._from_text(ctx))
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

    def _from_tables(self, ctx: ExtractionContext) -> List[Candidate]:
        out: List[Candidate] = []
        for page in ctx.tables or []:
            for table in page or []:
                if not _table_has_policyholder_anchor(table):
                    continue
                for row in table:
                    if not row or len(row) < 2:
                        continue
                    if not _OGRN_TABLE_LABEL_RE.match(row[0] or ""):
                        continue
                    for cell in row[1:]:
                        if cell is None:
                            continue
                        digits_match = _OGRN_DIGITS_RE.search(cell)
                        if digits_match is None:
                            continue
                        digits = digits_match.group(1)
                        if not _validate_any_length(digits):
                            continue
                        out.append(
                            Candidate(
                                value=digits,
                                state="found",
                                pattern_id="table_cell",
                                source_fragment=f"ОГРН | {digits}",
                                components=ConfidenceComponents(
                                    pattern_strength=0.75,
                                    context_strength=0.25,
                                ),
                            )
                        )
        return out

    def _from_text(self, ctx: ExtractionContext) -> List[Candidate]:
        block = locate_policyholder_block(ctx.normalized)
        if block is None:
            return []
        text = ctx.normalized.text
        start, end = block
        block_text = text[start:end]
        out: List[Candidate] = []
        for match in _OGRN_LABEL_AND_DIGITS_RE.finditer(block_text):
            digits = match.group(1)
            if not _validate_any_length(digits):
                continue
            span_abs = (start + match.start(), start + match.end())
            out.append(
                Candidate(
                    value=digits,
                    state="found",
                    pattern_id="anchored_text",
                    source_fragment=self.take_fragment(
                        text, span_abs[0], span_abs[1]
                    ),
                    span=span_abs,
                    components=ConfidenceComponents(
                        pattern_strength=0.65,
                        context_strength=0.25,
                    ),
                )
            )
        return out
