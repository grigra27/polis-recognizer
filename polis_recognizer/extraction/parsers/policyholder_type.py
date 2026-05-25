"""PolicyholderTypeParser — classify policyholder as individual or legal.

Decision rules inside the policyholder block, in priority order:

1. **Organisation prefix** (``ООО``, ``АО``, ``ИП``, …) — strongest
   signal; legal_entity. Note: ИП ends up here too, although it has
   ИНН-12 like a natural person. From contract perspective ИП acts
   as a business; ``policyholder.ogrn`` length (15 vs 13) is the
   discriminator if downstream cares.
2. **Valid 10-digit ИНН** (checksum-passing) → legal_entity.
3. **Valid 12-digit ИНН** (checksum-passing) → individual.
4. **3-Cyrillic-word ФИО pattern** → individual.

First matching rule wins; one Candidate emitted, or ``not_found``.

Parsers in the v2 pipeline can't see each other's results, so this
parser duplicates a tiny ИНН regex + checksum call rather than
depending on ``PolicyholderINNParser`` (which arrives in PR #3).
"""

from __future__ import annotations

import re
from typing import List, Tuple

from ..candidates import Candidate, ConfidenceComponents
from ..policyholder_block import locate_policyholder_block
from ..validators import validate_inn_10, validate_inn_12
from .base import ExtractionContext, FieldParser


_ORG_PREFIX_RE = re.compile(
    r"\b(?:ООО|ОАО|АО|ПАО|ЗАО|НКО|АНО|ТСЖ|ТСН|МУП|ГУП|ФГУП|"
    r"Общество\s+с\s+ограниченной)\b",
    re.IGNORECASE,
)

# ИП / Индивидуальный предприниматель — natural person classification.
# 0.3.3 treated ИП as legal_entity (it's registered as a business),
# but on real corpus that obscures the natural-person status that
# downstream CRM / 152-ФЗ flows care about, and the ИНН length (12)
# always agrees with the individual classification anyway. ИП now
# maps to ``individual`` — callers that need the entrepreneur subtype
# can detect it via ``policyholder.name.startswith("ИП ")`` or via
# ``policyholder.ogrn`` length (15 = ОГРНИП).
_IP_PREFIX_RE = re.compile(
    r"\b(?:ИП|Индивидуальный\s+предприниматель)\b",
    re.IGNORECASE,
)

_INN_ANY_RE = re.compile(r"(?<!\d)(\d{10}|\d{12})(?!\d)")

_FIO_RE = re.compile(
    r"\b[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+)?\b"
)


class PolicyholderTypeParser(FieldParser):
    field_name = "policyholder_type"

    def parse(self, ctx: ExtractionContext) -> List[Candidate]:
        block = locate_policyholder_block(ctx.normalized)
        if block is None:
            return self._not_found()
        text = ctx.normalized.text
        start, end = block
        block_text = text[start:end]

        # Rule 0 — ИП / Индивидуальный предприниматель — natural-person.
        # Checked BEFORE the generic org prefix because "ИП" used to be
        # part of _ORG_PREFIX_RE (mapping to legal_entity), which lost
        # the natural-person semantics downstream.
        match = _IP_PREFIX_RE.search(block_text)
        if match is not None:
            return [
                self._candidate(
                    value="individual",
                    pattern_id="ip_prefix",
                    fragment=self.take_fragment(
                        text, start + match.start(), start + match.end()
                    ),
                    span=(start + match.start(), start + match.end()),
                    pattern_strength=0.75,
                    context_strength=0.2,
                )
            ]

        # Rule 1 — organisation prefix.
        match = _ORG_PREFIX_RE.search(block_text)
        if match is not None:
            return [
                self._candidate(
                    value="legal_entity",
                    pattern_id="org_prefix",
                    fragment=self.take_fragment(
                        text, start + match.start(), start + match.end()
                    ),
                    span=(start + match.start(), start + match.end()),
                    pattern_strength=0.7,
                    context_strength=0.2,
                )
            ]

        # Rules 2/3 — ИНН with checksum. Iterate matches because the
        # first 10/12-digit run might fail the checksum (random noise);
        # we only commit to a verdict on a checksum-passing match.
        for inn_match in _INN_ANY_RE.finditer(block_text):
            digits = inn_match.group(1)
            verdict = None
            pattern_id = None
            if len(digits) == 10 and validate_inn_10(digits):
                verdict = "legal_entity"
                pattern_id = "inn10_checksum"
            elif len(digits) == 12 and validate_inn_12(digits):
                verdict = "individual"
                pattern_id = "inn12_checksum"
            if verdict is not None:
                return [
                    self._candidate(
                        value=verdict,
                        pattern_id=pattern_id,
                        fragment=self.take_fragment(
                            text,
                            start + inn_match.start(),
                            start + inn_match.end(),
                        ),
                        span=(
                            start + inn_match.start(),
                            start + inn_match.end(),
                        ),
                        pattern_strength=0.65,
                        context_strength=0.2,
                    )
                ]

        # Rule 4 — ФИО shape fallback.
        match = _FIO_RE.search(block_text)
        if match is not None:
            return [
                self._candidate(
                    value="individual",
                    pattern_id="fio_pattern",
                    fragment=self.take_fragment(
                        text, start + match.start(), start + match.end()
                    ),
                    span=(start + match.start(), start + match.end()),
                    pattern_strength=0.5,
                    context_strength=0.2,
                )
            ]

        return self._not_found()

    # ----- helpers -----

    def _candidate(
        self,
        *,
        value: str,
        pattern_id: str,
        fragment: str,
        span: Tuple[int, int],
        pattern_strength: float,
        context_strength: float,
    ) -> Candidate:
        return Candidate(
            value=value,
            state="found",
            pattern_id=pattern_id,
            source_fragment=fragment,
            span=span,
            components=ConfidenceComponents(
                pattern_strength=pattern_strength,
                context_strength=context_strength,
            ),
        )

    def _not_found(self) -> List[Candidate]:
        return [
            Candidate(
                value=None,
                state="not_found",
                pattern_id="no_pattern_match",
                source_fragment="",
            )
        ]
