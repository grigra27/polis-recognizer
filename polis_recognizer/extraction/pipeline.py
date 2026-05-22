"""Extraction v2 pipeline orchestrator.

Single entry point: ``run_extraction(raw_text)`` returns a structured
result containing the winning candidate per field plus a diagnostics
trace. The legacy ``ContractFieldExtractor`` calls this and converts
the output into the dataclass shapes the rest of the system expects.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .candidates import Candidate
from .layout import LayoutAnalyzer
from .negation import NegationContext
from .normalizer import TextNormalizer
from .parsers import ADDITIONAL_PARSERS, ALL_PARSERS, ExtractionContext, LEGACY_PARSERS
from .ranker import CandidateRanker

logger = logging.getLogger(__name__)


@dataclass
class ExtractionV2Result:
    """Structured output of the v2 pipeline.

    Attributes:
        legacy_fields: ``field_name → winning Candidate`` for the six
            fields whose shape is part of the public contract.
        additional_fields: ``field_name → winning Candidate`` for fields
            new in v2 (premium, sum_type). Surface separately so they
            can be added to the API without breaking consumers.
        diagnostics: One trace entry per parser, including all
            candidates considered (not just the winner).
        text_length: Length of the normalized text.
        elapsed_ms: Wall-clock time spent in the pipeline.
    """

    legacy_fields: Dict[str, Optional[Candidate]] = field(default_factory=dict)
    additional_fields: Dict[str, Optional[Candidate]] = field(default_factory=dict)
    diagnostics: List[Dict[str, Any]] = field(default_factory=list)
    text_length: int = 0
    elapsed_ms: float = 0.0


_LEGACY_FIELD_NAMES = {p.field_name for p in LEGACY_PARSERS}


def run_extraction(
    raw_text: str,
    *,
    correlation_id: Optional[str] = None,
    tables: Optional[List[List[List[List[str]]]]] = None,
    extract_pii: bool = False,
) -> ExtractionV2Result:
    """Run the full v2 extraction pipeline and return structured candidates.

    Args:
        raw_text: Document text from any extractor (pypdf / pdfplumber /
            OCR). Required.
        correlation_id: Optional trace id propagated to log entries.
        tables: Optional per-page list of tables, populated only when the
            upstream extractor is layout-aware (pdfplumber). Parsers
            that read tables get a higher-confidence path; the others
            ignore this argument. Default ``None`` keeps the legacy
            text-only contract intact.
        extract_pii: Opt-in gate for special-category PII parsers
            (passport, birth date). Default ``False`` keeps those
            parsers inert even if their patterns match the input.
            Contact parsers (phone/email/address) are not affected.
    """
    start = time.time()
    normalizer = TextNormalizer()
    normalized = normalizer.normalize(raw_text or "")
    layout = LayoutAnalyzer()
    negation = NegationContext()
    ctx = ExtractionContext(
        raw=raw_text or "",
        normalized=normalized,
        layout=layout,
        negation=negation,
        tables=tables or [],
        extract_pii=extract_pii,
    )
    ranker = CandidateRanker()

    legacy_fields: Dict[str, Optional[Candidate]] = {}
    additional_fields: Dict[str, Optional[Candidate]] = {}
    diagnostics: List[Dict[str, Any]] = []

    for parser in ALL_PARSERS:
        try:
            candidates = parser.parse(ctx)
        except Exception as exc:  # pragma: no cover - defensive path
            logger.exception(
                "extraction_parser_exception",
                extra={
                    "field": parser.field_name,
                    "correlation_id": correlation_id,
                    "error_type": type(exc).__name__,
                },
            )
            candidates = []
            diagnostics.append({
                "field": parser.field_name,
                "winner": None,
                "candidates": [],
                "error": f"{type(exc).__name__}: {exc}",
            })
            if parser.field_name in _LEGACY_FIELD_NAMES:
                legacy_fields[parser.field_name] = None
            else:
                additional_fields[parser.field_name] = None
            continue

        winner = ranker.best(candidates)
        diagnostics.append({
            "field": parser.field_name,
            "winner": winner.to_dict() if winner else None,
            "candidates": [c.to_dict() for c in candidates],
        })
        if parser.field_name in _LEGACY_FIELD_NAMES:
            legacy_fields[parser.field_name] = winner
        else:
            additional_fields[parser.field_name] = winner

    return ExtractionV2Result(
        legacy_fields=legacy_fields,
        additional_fields=additional_fields,
        diagnostics=diagnostics,
        text_length=len(normalized.text),
        elapsed_ms=round((time.time() - start) * 1000.0, 2),
    )
