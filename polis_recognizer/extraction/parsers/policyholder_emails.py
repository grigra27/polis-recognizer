"""PolicyholderEmailsParser — extract policyholder email addresses.

Scoped to the policyholder block (or to tables anchored on
"Страхователь") to keep the insurer's signature-block email out of
the output. Multi-value: one Candidate with ``value=list[str]``,
lowercased and deduped.

Regex is intentionally simple — full RFC 5321/5322 compliance is
overkill for what appears on Russian insurance PDFs. Cyrillic local
parts and ``.рф`` domains are technically legal but vanishingly rare;
defer to a later release if a real corpus shows them.
"""

from __future__ import annotations

import re
from typing import List

from ..candidates import Candidate, ConfidenceComponents
from ..policyholder_block import (
    locate_policyholder_block,
    table_has_policyholder_anchor,
)
from .base import ExtractionContext, FieldParser


_EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"
)


class PolicyholderEmailsParser(FieldParser):
    field_name = "policyholder_emails"

    def parse(self, ctx: ExtractionContext) -> List[Candidate]:
        seen = set()
        ordered: List[str] = []
        text = ctx.normalized.text
        span = None

        block = locate_policyholder_block(ctx.normalized)
        if block is not None:
            start, end = block
            block_text = text[start:end]
            for match in _EMAIL_RE.finditer(block_text):
                normalized = match.group(0).lower()
                if normalized in seen:
                    continue
                seen.add(normalized)
                ordered.append(normalized)
            if ordered:
                span = (start, end)

        for page in ctx.tables or []:
            for table in page or []:
                if not table_has_policyholder_anchor(table):
                    continue
                for row in table or []:
                    for cell in row or []:
                        if not cell:
                            continue
                        for match in _EMAIL_RE.finditer(cell):
                            normalized = match.group(0).lower()
                            if normalized in seen:
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
            fragment = self.take_fragment(text, span[0], min(span[0] + 80, span[1]))
        return [
            Candidate(
                value=ordered,
                state="found",
                pattern_id="block_scan",
                source_fragment=fragment or ", ".join(ordered)[:240],
                span=span,
                components=ConfidenceComponents(
                    pattern_strength=0.7,
                    context_strength=0.25,
                ),
            )
        ]
