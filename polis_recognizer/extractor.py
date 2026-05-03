"""Top-level facade — :class:`PolicyExtractor` and :class:`ExtractedPolicy`.

This is the recommended entry point for the library. It bundles
the PDF ingestion pipeline (text-layer first, OCR fallback) and the
deterministic field extractor into one call.

For lower-level access — running the pipeline on raw text without
a PDF, or using a custom OCR engine — see
:func:`polis_recognizer.run_extraction` and
:class:`polis_recognizer.OCRService`.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .contract_field_extractor import ContractFieldExtractor
from .ocr_config import get_ocr_config
from .ocr_service import OCRService
from .pdf_extraction_router import PdfExtractionRouter, build_text_service


@dataclass
class ExtractedPolicy:
    """Structured result of a policy extraction.

    All fields are ``Optional`` — a value of ``None`` means the parser
    didn't find that field with sufficient confidence. ``franchise``
    has a special ``absent`` flag for the case "no franchise / 0 руб"
    (the polis explicitly states there is no deductible).
    """

    policy_number: Optional[str] = None
    policy_period: Optional[dict] = None  # {"start": date, "end": date}
    franchise: Optional[dict] = None  # {"value": float, "currency": str, "absent": bool}
    limit: Optional[dict] = None  # {"value": float, "currency": str}
    premium: Optional[dict] = None  # {"value": float, "currency": str}
    sum_type: Optional[str] = None  # "aggregate" | "non_aggregate"
    repair_mode: Optional[str] = None  # "dealer" | "service" | "cash"

    extraction_method: str = "unknown"  # "text_layer" | "ocr" | "mixed" | "failed"
    extraction_status: str = "unknown"  # "DONE" | "FAILED" | "skipped"
    confidence_per_field: dict = field(default_factory=dict)
    diagnostics: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    text_length: int = 0

    @property
    def is_complete(self) -> bool:
        """True if all 7 fields are populated."""
        return (
            self.policy_number is not None
            and self.policy_period is not None
            and self.franchise is not None
            and self.limit is not None
            and self.premium is not None
            and self.sum_type is not None
            and self.repair_mode is not None
        )


class PolicyExtractor:
    """High-level entry point for policy field extraction.

    Combines the PDF ingestion router (pypdf text + pdfplumber tables,
    with Tesseract OCR fallback) and the deterministic
    :class:`ContractFieldExtractor` into a single call.

    Configuration is via constructor arguments; all default to sensible
    KASKO values. The default extractor is ``"hybrid"`` (pypdf text +
    pdfplumber tables in one pass) — see README for the trade-offs.

    Example::

        extractor = PolicyExtractor(ocr_language="rus+eng")
        result = extractor.extract_from_pdf("/path/to/polis.pdf")
        if result.policy_number:
            print(f"Policy: {result.policy_number}")
    """

    def __init__(
        self,
        *,
        ocr_language: str = "rus+eng",
        ocr_timeout_seconds: int = 300,
        ocr_page_limit: int = 50,
        ocr_max_text_size: int = 500_000,
        pdf_extractor: str = "hybrid",
        image_preprocessing: str = "fallback",
        max_image_size_bytes: Optional[int] = None,
        psm: Optional[int] = None,
        oem: Optional[int] = None,
    ) -> None:
        self._ocr_service = OCRService(
            page_limit=ocr_page_limit,
            max_text_size=ocr_max_text_size,
            timeout_seconds=ocr_timeout_seconds,
            ocr_language=ocr_language,
            image_preprocessing=image_preprocessing,
            psm=psm,
            oem=oem,
            max_image_size_bytes=max_image_size_bytes,
        )
        self._router = PdfExtractionRouter(
            ocr_service=self._ocr_service,
            text_service=build_text_service(pdf_extractor),
        )
        self._field_extractor = ContractFieldExtractor()

    # ------------------------------------------------------------------
    # Entry points

    def extract_from_pdf(self, file_path) -> ExtractedPolicy:
        """Extract from a PDF file on disk."""
        path = Path(file_path)
        if not path.is_file():
            raise FileNotFoundError(f"PDF not found: {file_path}")
        with open(path, "rb") as f:
            pdf_bytes = f.read()
        return self.extract_from_bytes(pdf_bytes, filename=path.name)

    def extract_from_bytes(
        self, pdf_bytes: bytes, *, filename: str = "policy.pdf"
    ) -> ExtractedPolicy:
        """Extract from raw PDF bytes."""
        outcome = self._router.route(pdf_bytes, filename)
        result = self._field_extractor.extract_contract_fields(
            outcome.extracted_text,
            tables=list(outcome.tables) if outcome.tables else None,
        )
        return self._build_extracted_policy(result, outcome)

    def extract_from_text(self, text: str) -> ExtractedPolicy:
        """Extract from already-extracted text (no PDF/OCR step).

        Useful for testing or when text comes from a non-PDF source.
        Tables-aware paths are not available on this entry point.
        """
        result = self._field_extractor.extract_contract_fields(text)
        return self._build_extracted_policy(result, outcome=None)

    # ------------------------------------------------------------------
    # Conversion helpers

    @staticmethod
    def _build_extracted_policy(
        contract_result: Any, outcome: Optional[Any]
    ) -> ExtractedPolicy:
        cf_dict = contract_result.to_dict()
        addl = getattr(contract_result, "additional_fields", {}) or {}

        period = cf_dict.get("policy_period") or {}
        period_value = None
        if period.get("start") and period.get("end"):
            period_value = {
                "start": _to_date(period["start"]),
                "end": _to_date(period["end"]),
            }

        franchise = cf_dict.get("franchise") or {}
        franchise_value = None
        if franchise.get("value") is not None or franchise.get("absent"):
            franchise_value = {
                "value": franchise.get("value"),
                "currency": franchise.get("currency"),
                "absent": franchise.get("absent", False),
            }

        limit_dict = cf_dict.get("limit") or {}
        limit_value = None
        if limit_dict.get("value") is not None:
            limit_value = {
                "value": limit_dict["value"],
                "currency": limit_dict.get("currency"),
            }

        repair_dict = cf_dict.get("repair_mode") or {}
        repair_value = repair_dict.get("value")

        premium_cand = (addl or {}).get("premium")
        premium_value = None
        if premium_cand and isinstance(premium_cand, dict) and premium_cand.get("value") is not None:
            v = premium_cand["value"]
            if isinstance(v, dict):
                premium_value = {"value": v.get("value"), "currency": v.get("currency")}
            else:
                premium_value = {"value": v, "currency": None}

        sum_type_cand = (addl or {}).get("sum_type")
        sum_type_value = None
        if sum_type_cand and isinstance(sum_type_cand, dict):
            sum_type_value = sum_type_cand.get("value")

        policy_number_cand = (addl or {}).get("policy_number")
        policy_number_value = None
        if policy_number_cand and isinstance(policy_number_cand, dict):
            v = policy_number_cand.get("value")
            if isinstance(v, dict):
                policy_number_value = v.get("display") or v.get("number")
            elif v:
                policy_number_value = str(v)

        confidence = {}
        for k, v in cf_dict.items():
            if isinstance(v, dict) and "confidence" in v:
                confidence[k] = v["confidence"]
        for k, v in (addl or {}).items():
            if isinstance(v, dict) and "confidence" in v:
                confidence[k] = v["confidence"]

        return ExtractedPolicy(
            policy_number=policy_number_value,
            policy_period=period_value,
            franchise=franchise_value,
            limit=limit_value,
            premium=premium_value,
            sum_type=sum_type_value,
            repair_mode=repair_value,
            extraction_method=getattr(outcome, "extraction_method", "unknown") if outcome else "text_only",
            extraction_status=getattr(contract_result, "extraction_status", "unknown"),
            confidence_per_field=confidence,
            diagnostics=list(getattr(contract_result, "diagnostics", []) or []),
            warnings=list(getattr(outcome, "warnings", []) or []) if outcome else [],
            text_length=len(getattr(outcome, "extracted_text", "")) if outcome else 0,
        )


def _to_date(value):
    """Coerce ISO string / date / datetime to a date object; pass-through otherwise."""
    if isinstance(value, _dt.date) and not isinstance(value, _dt.datetime):
        return value
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return _dt.date.fromisoformat(value)
        except ValueError:
            return value
    return value
