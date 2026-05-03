"""
PDF extraction router: text-layer first, OCR as fallback.

Used by the async `pre_ingest_task` pipeline. Pre-C1 it was also used
by the legacy `ingest_policy_file` task — that task has been removed,
but the router is kept because pre_ingest itself depends on the same
text-layer-first, OCR-fallback decision logic plus the structured
PdfExtractionOutcome it produces.

NO LLM is used here. The router is pure deterministic glue around
`PolicyIngestionService` (pypdf text-layer) and `OCRService` (Tesseract).
"""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass, field
from typing import List, Literal, Optional

from .hybrid_ingestion import HybridIngestionService
from .ocr_config import OCRResult
from .ocr_service import OCRService
from .pdfplumber_ingestion import PdfPlumberIngestionService
from .policy_ingestion import PolicyIngestionService

logger = logging.getLogger(__name__)


ExtractionMethod = Literal["text_layer", "ocr", "mixed", "failed"]


# ---------------------------------------------------------------------------
# EDI envelope detection
#
# Diadoc / Kontur and other EDI operators wrap signed PDFs with extra pages
# of metadata (signature info, certificate, "Страница X из N" stamps). pypdf
# happily extracts that text while the actual policy is embedded as raster.
# `len(text) >= min_text_threshold` is then a false positive — the router
# would skip OCR and feed the field parsers a notarization wrapper that has
# zero insurance content.
#
# Heuristic: classify text-layer as an envelope only if it
#   (a) is short (≤ EDO_ENVELOPE_MAX_TEXT_LEN),
#   (b) contains ≥ 2 EDI markers, and
#   (c) contains *zero* domain markers — i.e. the wrapper has no insurance
#       content at all.
#
# Just (a)+(b) is too aggressive: many real digital PDFs are sent through
# Diadoc/Kontur and carry a one-liner header from the operator alongside
# the actual policy text. Those documents do mention "полис"/"страхов" /
# "каско" etc., so requiring zero domain hits keeps them on the text-layer
# path. Genuine envelopes (e.g. SOGAZ Diadoc-only wrappers) contain only
# notarization metadata and pass all three conditions.
_EDO_MARKERS = (
    "передан через диадок",
    "документ подписан и передан через оператора эдо",
    "идентификатор документа",
    "сертификат: серийный",
    "подпись соответствует файлу документа",
)
_POLICY_DOMAIN_MARKERS = (
    "полис",
    "страхователь",
    "страховщик",
    "страхов",          # страховой/страхования/страховая etc.
    "каско",
    "осаго",
    "франшиз",
    "транспортн",       # транспортное средство
    "выгодоприобрет",
    "договор страхов",
)
_EDO_ENVELOPE_MAX_TEXT_LEN = 10_000


def _looks_like_edo_envelope(text: str) -> bool:
    if not text or len(text) > _EDO_ENVELOPE_MAX_TEXT_LEN:
        return False
    lower = text.lower()
    edo_hits = sum(1 for marker in _EDO_MARKERS if marker in lower)
    if edo_hits < 2:
        return False
    domain_hits = sum(1 for marker in _POLICY_DOMAIN_MARKERS if marker in lower)
    return domain_hits == 0


# Some PDF renderers (notably the "АбсолютСтрахование" stack) emit a text
# layer where adjacent glyphs were never separated by space characters —
# pypdf extracts one continuous run like "Обществосограниченнойответственностью".
# Field parsers cannot match anything because every label/keyword they look
# for ("срок действия", "страховая сумма" …) requires whitespace tokens.
#
# Heuristic: in a healthy Russian text-layer the whitespace ratio sits in
# 13–27% across the live corpus. Glued documents land at < 1% (real samples
# measured at 0.2%). 5% gives a wide safety margin; require minimum length
# so we don't trip on tiny envelopes that are already covered by EDO/no-
# text-layer paths.
_GLUED_TEXT_MIN_LEN = 200
_GLUED_TEXT_WS_RATIO = 0.05


def _looks_like_glued_text(text: str) -> bool:
    if not text or len(text) < _GLUED_TEXT_MIN_LEN:
        return False
    ws = sum(1 for c in text if c.isspace())
    return (ws / len(text)) < _GLUED_TEXT_WS_RATIO


@dataclass
class PdfExtractionOutcome:
    """Result of a PDF extraction attempt.

    Attributes:
        extracted_text: Final text used downstream by extractors. Empty on failure.
        status: "DONE" or "FAILED". Mirrors OCRResult.status semantics.
        extraction_method: Which path produced the text.
        pages_total: Total number of pages discovered (best effort).
        pages_processed: Pages whose text was actually consumed.
        warnings: Aggregated warning codes across paths.
        error: Last error message if status is FAILED.
        fallback_reason: Set when text-layer was attempted and OCR fallback
            was triggered. Possible values:
              "no_text_layer" - text-layer present but below threshold
              "text_extraction_exception" - pypdf raised
            None when text-layer was sufficient or no fallback was attempted.
        tables: Per-page list of tables (each a list of rows; each row a
            list of cell strings). Populated only by the layout-aware
            text-layer path (pdfplumber). Empty list on the OCR path
            and on the pypdf path (which has no table-extraction API).
            Parsers may use it as an extra signal source; ignoring it
            preserves backward compatibility.
    """

    extracted_text: str
    status: str
    extraction_method: ExtractionMethod
    pages_total: int
    pages_processed: int
    warnings: List[str] = field(default_factory=list)
    error: Optional[str] = None
    fallback_reason: Optional[str] = None
    tables: List[List[List[List[str]]]] = field(default_factory=list)


def build_text_service(choice: str = "hybrid"):
    """Pick the text-layer extractor by name.

    Three options:
      "pypdf"      — fast, no tables (legacy default).
      "pdfplumber" — layout-aware, with tables. Regresses overall on our
                     KASKO corpus because pdfplumber's text-reconstruction
                     differs from pypdf's on dates.
      "hybrid"     — pypdf text + pdfplumber tables in one pass. The
                     best of both: existing regex parsers see identical
                     pypdf text, AND the table-aware parsers (limit /
                     franchise / premium) get pdfplumber's column data.
                     Default.
    """
    choice = (choice or "hybrid").lower()
    if choice == "pdfplumber":
        return PdfPlumberIngestionService()
    if choice == "pypdf":
        return PolicyIngestionService()
    return HybridIngestionService()


def _build_default_text_service():
    """Default factory used when ``PdfExtractionRouter.text_service`` is None.

    Reads the ``PDF_EXTRACTOR`` env var directly (no Django dependency)
    so the module is importable in standalone contexts. Polishelper's
    Django settings layer also reads the same env var so behaviour stays
    consistent between in-Django and standalone use.
    """
    return build_text_service(os.getenv("PDF_EXTRACTOR", "hybrid"))


class PdfExtractionRouter:
    """Route a PDF through text-layer extraction first, OCR as fallback."""

    def __init__(
        self,
        ocr_service: OCRService,
        text_service=None,
    ) -> None:
        self._ocr_service = ocr_service
        self._text_service = text_service or _build_default_text_service()

    def route(
        self,
        pdf_bytes: bytes,
        filename: str,
        *,
        correlation_id: Optional[str] = None,
    ) -> PdfExtractionOutcome:
        """Run text-layer extraction first, fall back to OCR if needed.

        Args:
            pdf_bytes: Raw PDF bytes.
            filename: Original filename, used only for logs.
            correlation_id: Optional trace id propagated to logs.

        Returns:
            PdfExtractionOutcome describing the chosen path and result.
        """
        # NOTE: do NOT use "filename" as an extra key — it collides with the
        # built-in LogRecord.filename attribute and Logger.makeRecord raises
        # KeyError. Use a namespaced key instead.
        log_extra = {"correlation_id": correlation_id, "pdf_filename": filename}

        # 1) Try pypdf text-layer extraction.
        text_result = None
        text_layer_error: Optional[BaseException] = None
        tmp_path: Optional[str] = None

        try:
            with tempfile.NamedTemporaryFile(
                suffix=".pdf", delete=False
            ) as tmp_file:
                tmp_file.write(pdf_bytes)
                tmp_path = tmp_file.name
            text_result = self._text_service.extract_text_from_pdf(tmp_path)
        except Exception as exc:  # pypdf can raise a wide set of errors
            text_layer_error = exc
            logger.warning(
                "pdf_text_layer_extraction_failed",
                extra={
                    **log_extra,
                    "error": str(exc),
                    "stage": "text_layer",
                },
                exc_info=True,
            )
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

        edo_envelope = (
            text_result is not None and _looks_like_edo_envelope(text_result.text)
        )
        glued_text = (
            text_result is not None and _looks_like_glued_text(text_result.text)
        )

        if (
            text_result is not None
            and not edo_envelope
            and not glued_text
            and not self._ocr_service.should_use_ocr(text_result.text)
        ):
            # Text-layer is sufficient. Skip OCR entirely.
            logger.info(
                "pdf_text_layer_sufficient",
                extra={
                    **log_extra,
                    "text_length": len(text_result.text),
                    "pages": text_result.pages,
                    "warnings": list(text_result.warnings),
                },
            )
            return PdfExtractionOutcome(
                extracted_text=text_result.text,
                status="DONE",
                extraction_method="text_layer",
                pages_total=text_result.pages,
                pages_processed=text_result.pages,
                warnings=list(text_result.warnings),
                error=None,
                fallback_reason=None,
                tables=list(getattr(text_result, "tables", []) or []),
            )

        # 2) Fallback to OCR.
        if text_layer_error is not None:
            fallback_reason = "text_extraction_exception"
        elif edo_envelope:
            fallback_reason = "edo_envelope_detected"
        elif glued_text:
            fallback_reason = "glued_text_layer"
        else:
            fallback_reason = "no_text_layer"

        logger.info(
            "pdf_ocr_fallback",
            extra={
                **log_extra,
                "fallback_reason": fallback_reason,
                "text_layer_chars": (
                    len(text_result.text) if text_result is not None else 0
                ),
            },
        )

        ocr_result: OCRResult = self._ocr_service.process_pdf(pdf_bytes, filename)

        warnings = list(ocr_result.warnings)
        if text_result is not None:
            for warning in text_result.warnings:
                if warning not in warnings:
                    warnings.append(warning)
        if edo_envelope and "edo_envelope_detected" not in warnings:
            warnings.append("edo_envelope_detected")
        if glued_text and "glued_text_layer" not in warnings:
            warnings.append("glued_text_layer")

        return PdfExtractionOutcome(
            extracted_text=ocr_result.extracted_text,
            status=ocr_result.status,
            extraction_method="ocr",
            pages_total=ocr_result.pages_total,
            pages_processed=ocr_result.pages_processed,
            warnings=warnings,
            error=ocr_result.error,
            fallback_reason=fallback_reason,
        )
