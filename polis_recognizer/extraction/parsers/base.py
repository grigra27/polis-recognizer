"""FieldParser base class + ExtractionContext.

Each parser produces one or more `Candidate`s for a single field. The
`ExtractionContext` carries the pre-computed building blocks (normalized
text, layout analyzer, negation detector) so individual parsers don't
re-run that work.

Parsers should NEVER raise — they catch their own exceptions and return
either an empty list or a single ``not_found`` candidate with the failure
recorded in ``notes``. The pipeline level then logs and moves on.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import ClassVar, List

from ..candidates import Candidate
from ..layout import LayoutAnalyzer
from ..negation import NegationContext
from ..normalizer import NormalizedText


@dataclass
class ExtractionContext:
    raw: str
    normalized: NormalizedText
    layout: LayoutAnalyzer
    negation: NegationContext
    # Per-page list of tables (each a list of rows; each row a list of
    # cell strings). Populated only when the upstream PDF extractor
    # supports tables (pdfplumber); empty otherwise. Parsers that don't
    # use tables ignore this — same dataflow as the legacy text-only
    # contract.
    tables: List[List[List[List[str]]]] = field(default_factory=list)


class FieldParser(ABC):
    """Abstract base for one-field deterministic parsers."""

    field_name: ClassVar[str]

    @abstractmethod
    def parse(self, ctx: ExtractionContext) -> List[Candidate]:
        """Return zero or more Candidates for this field."""

    @staticmethod
    def take_fragment(text: str, start: int, end: int, *, span: int = 80) -> str:
        """Return up to ~240 chars of context around [start:end]."""
        lo = max(0, start - span)
        hi = min(len(text), end + span)
        fragment = text[lo:hi].strip()
        if len(fragment) > 240:
            fragment = fragment[:240]
        return fragment
