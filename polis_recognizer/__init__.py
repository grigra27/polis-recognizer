"""polis-recognizer — Russian insurance policy field extractor.

Extracts structured fields from KASKO insurance policy PDFs:
``policy_period``, ``franchise``, ``limit``, ``repair_mode``,
``premium``, ``sum_type``, ``policy_number``, plus ``policyholder``
(name, type, INN, OGRN, KPP, and — behind an opt-in PII flag —
passport and birth date) and ``policyholder_contacts`` (phones in
E.164, emails, raw address, postal code).

Quick start::

    from polis_recognizer import PolicyExtractor

    extractor = PolicyExtractor()
    result = extractor.extract_from_pdf("/path/to/polis.pdf")

    print(result.policy_number)
    print(result.policy_period.start, result.policy_period.end)
    print(result.franchise.value, result.franchise.currency)

The extractor combines a text-layer reader (pypdf) with optional
table-aware extraction (pdfplumber) and an OCR fallback (Tesseract).
See README.md for the full API and supported insurer formats.
"""

from .contract_field_extractor import (
    ContractFieldExtractor,
    ContractFieldsResult,
    FieldDiagnostic,
    MonetaryField,
    PolicyPeriodField,
    TextField,
)
from .exceptions import (
    OCRProcessingError,
    OCRTimeoutError,
    UnsupportedFileTypeError,
)
from .extraction import (
    Candidate,
    ExtractionV2Result,
    run_extraction,
)
from .extractor import ExtractedPolicy, PolicyExtractor
from .hybrid_ingestion import HybridIngestionService
from .ocr_config import (
    OCRConfig,
    OCRResult,
    get_ocr_config,
    reset_ocr_config,
    validate_language_pack,
)
from .ocr_service import OCRService
from .pdf_extraction_router import (
    PdfExtractionOutcome,
    PdfExtractionRouter,
    build_text_service,
)
from .pdfplumber_ingestion import PdfPlumberIngestionService
from .policy_ingestion import ExtractedTextResult, PolicyIngestionService


__version__ = "0.3.4"


__all__ = [
    "__version__",
    # Top-level facade (recommended entry point)
    "PolicyExtractor",
    "ExtractedPolicy",
    # Field result dataclasses
    "ContractFieldsResult",
    "FieldDiagnostic",
    "MonetaryField",
    "PolicyPeriodField",
    "TextField",
    # Lower-level pipeline (for advanced use)
    "ContractFieldExtractor",
    "Candidate",
    "ExtractionV2Result",
    "run_extraction",
    # OCR service
    "OCRService",
    "OCRConfig",
    "OCRResult",
    "get_ocr_config",
    "reset_ocr_config",
    "validate_language_pack",
    # PDF ingestion
    "PdfExtractionRouter",
    "PdfExtractionOutcome",
    "ExtractedTextResult",
    "PolicyIngestionService",
    "PdfPlumberIngestionService",
    "HybridIngestionService",
    "build_text_service",
    # Exceptions
    "OCRProcessingError",
    "OCRTimeoutError",
    "UnsupportedFileTypeError",
]
