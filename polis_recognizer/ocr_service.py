"""
OCR Service Module

This module provides the OCRService class for performing optical character recognition
on images and scanned PDFs. The service operates entirely in-memory without temporary
files and enforces resource limits (page limits, text size limits, timeouts).

IMPORTANT - CELERY ASYNC OCR SAFETY (Future Implementation):
-------------------------------------------------------------
If OCR processing is moved to a Celery task in the future, the following
requirements MUST be enforced to prevent race conditions and ensure robustness:

1. FILE PERSISTENCE GUARANTEE:
   - Policy files MUST be saved to storage and verified (storage.exists()) BEFORE
     dispatching any Celery task
   - The stored_filename (UUID-based) MUST be passed to the worker, NOT file bytes
   - Workers MUST access files via Django storage backend, NOT in-memory objects

2. WORKER FILE ACCESS PATTERN:
   - Workers SHALL verify file existence before processing:
     if not default_storage.exists(file_path):
         raise FileNotFoundError(f"Policy file not found: {stored_filename}")
   - Workers SHALL read files from storage:
     with default_storage.open(file_path, 'rb') as f:
         file_bytes = f.read()
   - Workers SHALL NOT rely on any in-memory file objects passed from the view

3. TASK RETRY LOGIC WITH EXPONENTIAL BACKOFF:
   - Use Celery retry decorator with exponential backoff:
     @shared_task(
         bind=True,
         max_retries=3,
         autoretry_for=(FileNotFoundError,),
         retry_backoff=True,
         retry_backoff_max=600  # Max 10 minutes between retries
     )
   - Log all retry attempts with correlation_id for debugging
   - After max retries, mark extraction status as "FAILED" with appropriate error

4. RACE CONDITION PREVENTION:
   - NEVER dispatch Celery task before file persistence is confirmed
   - ALWAYS use transaction.on_commit() if database writes are involved:
     transaction.on_commit(lambda: async_ocr_task.delay(stored_filename, correlation_id))
   - This ensures the Analysis record (if any) is committed before the task runs

5. IDEMPOTENCY CONSIDERATIONS:
   - OCR tasks SHOULD be idempotent (safe to run multiple times)
   - Use correlation_id to track and deduplicate processing attempts
   - Check existing extraction status before re-processing

Example Celery Task Structure:

    @shared_task(
        bind=True,
        max_retries=3,
        autoretry_for=(FileNotFoundError,),
        retry_backoff=True,
        retry_backoff_max=600
    )
    def async_ocr_task(self, stored_filename, correlation_id, **kwargs):
        '''
        Async OCR processing task.
        
        Args:
            stored_filename: UUID-based filename (e.g., "abc123.pdf")
            correlation_id: Request correlation ID for tracing
        '''
        from django.core.files.storage import default_storage
        from django.conf import settings
        import os
        
        logger.info(
            f"Starting async OCR processing",
            extra={
                "correlation_id": correlation_id,
                "stored_filename": stored_filename,
                "retry": self.request.retries
            }
        )
        
        # Construct file path
        file_path = os.path.join(settings.POLICY_STORAGE_PREFIX, stored_filename)
        
        # CRITICAL: Verify file exists before processing
        if not default_storage.exists(file_path):
            logger.error(
                f"File not found in storage, will retry",
                extra={
                    "correlation_id": correlation_id,
                    "file_path": file_path,
                    "retry": self.request.retries
                }
            )
            # This will trigger automatic retry with exponential backoff
            raise FileNotFoundError(f"Policy file not found: {stored_filename}")
        
        # Read file from storage
        try:
            with default_storage.open(file_path, 'rb') as f:
                file_bytes = f.read()
        except Exception as e:
            logger.error(
                f"Failed to read file from storage",
                extra={
                    "correlation_id": correlation_id,
                    "error": str(e)
                },
                exc_info=True
            )
            raise
        
        # Initialize OCR service and process
        ocr_service = OCRService(...)
        ocr_result = ocr_service.process_pdf(file_bytes, stored_filename)
        
        # Update Analysis record or return result
        # ... rest of processing logic

Validates: Requirements 1-11, Task 9.3
"""

import io
import logging
import os
from typing import List, Tuple, Optional

import pdfplumber

from .ocr_config import OCRConfig, OCRResult, validate_language_pack
from .exceptions import OCRTimeoutError, OCRProcessingError, UnsupportedFileTypeError

logger = logging.getLogger(__name__)


def validate_file_type(filename: str) -> None:
    """
    Validate that the file has a supported extension.
    
    Extracts the file extension from the filename and checks it against
    the list of supported extensions (pdf, png, jpg, jpeg). The check is
    case-insensitive.
    
    Args:
        filename: The name of the file to validate (e.g., "document.pdf")
        
    Raises:
        UnsupportedFileTypeError: If the file extension is not supported
        
    Validates: Requirements 10.1, 10.2, 10.3
    """
    # Extract file extension (without the dot) and convert to lowercase
    _, ext = os.path.splitext(filename)
    file_ext = ext.lstrip('.').lower()
    
    # Define supported extensions
    supported_extensions = {'pdf', 'png', 'jpg', 'jpeg'}
    
    # Check if extension is supported
    if file_ext not in supported_extensions:
        error_msg = (
            f"Unsupported file type: {file_ext}. "
            f"Supported formats: PDF, PNG, JPG, JPEG"
        )
        logger.warning(f"File validation failed for '{filename}': {error_msg}")
        raise UnsupportedFileTypeError(error_msg)
    
    logger.debug(f"File validation passed for '{filename}' with extension '{file_ext}'")


def build_error_context(status: str, error: str) -> dict:
    """
    Build contract context for error cases.
    
    Creates a standardized contract_context_json structure for error scenarios
    with empty extracted text, zero page counts, and no warnings. The error
    message is included in the extraction metadata.
    
    Args:
        status: Extraction status (typically "FAILED")
        error: Descriptive error message explaining what went wrong
        
    Returns:
        Dictionary containing contract_context_json structure with error metadata:
        - extracted_text: Empty string
        - extraction.status: The provided status value
        - extraction.method: Set to "ocr"
        - extraction.pages_total: 0
        - extraction.pages_processed: 0
        - extraction.warnings: Empty array
        - extraction.error: The provided error message
    
    Validates: Requirements 7.1, 7.4
    """
    return {
        "extracted_text": "",
        "extraction": {
            "status": status,
            "method": "ocr",
            "pages_total": 0,
            "pages_processed": 0,
            "warnings": [],
            "error": error
        }
    }


def get_pdf_page_count(pdf_bytes: bytes) -> int:
    """
    Get the total number of pages in a PDF document.
    
    Uses pdfplumber to count pages from PDF bytes. Handles errors gracefully
    by returning 0 if the PDF cannot be read.
    
    Args:
        pdf_bytes: Raw PDF file bytes
        
    Returns:
        Total number of pages in the PDF, or 0 if an error occurs
        
    Validates: Requirements 4.4
    """
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            return len(pdf.pages)
    except Exception as e:
        logger.error(f"Failed to get PDF page count: {str(e)}", exc_info=True)
        return 0


class OCRService:
    """
    Handles OCR processing for images and scanned PDFs.
    Operates entirely in-memory without temporary files.
    
    This service provides methods to:
    - Process image files (PNG, JPG, JPEG) using OCR
    - Process scanned PDFs using OCR with page limits
    - Determine if OCR is needed based on text threshold
    - Enforce resource limits (page limits, text size, timeouts)
    
    All processing is done in-memory to avoid temporary file management.
    Language pack validation is performed on initialization.
    
    Validates: Requirements 1.1-1.6, 2.2-2.4, 3.1-3.4, 4.1-4.4, 5.1-5.3, 6.1-6.2, 9.1-9.3
    """
    
    # Per-page text length below this triggers a preprocessing retry in
    # the "fallback" mode. Picked so that "near-empty" pages re-run while
    # well-extracted pages stay on the fast path.
    _PREPROCESS_FALLBACK_PER_PAGE_MIN = 200

    # An OSD-rotation pre-pass and an adaptive PSM=6 retry were tried
    # 2026-05-02 (commit reverted the same day) and measured net
    # regression on batch_3: -4 total passes and +50% runtime. Tesseract
    # OSD false-positive-rotates noisy КАСКО scans whose text isn't
    # actually rotated, and our corpus has no real rotated content
    # to rescue. PSM=6 retry didn't fire often enough to help. Reasons
    # documented in docs/improvement_plans/POLICY_RECOGNITION_QUALITY_PLAN.md so we don't
    # try the same thing again without a triggering signal (e.g. real
    # rotated traffic surfacing in production observability).

    def __init__(
        self,
        page_limit: int = 50,
        max_text_size: int = 500000,
        timeout_seconds: int = 300,
        min_text_threshold: int = 100,
        ocr_language: str = "rus+eng",
        image_preprocessing: str = "fallback",
        psm: Optional[int] = None,
        oem: Optional[int] = None,
        max_image_size_bytes: Optional[int] = None,
    ):
        """
        Initialize OCR service with configuration parameters.

        Validates the language pack on initialization and stores configuration
        as instance variables. If the language pack is not available, a warning
        is logged and the service falls back to English.

        Args:
            page_limit: Maximum number of pages to process per document (default: 50)
            max_text_size: Maximum characters in extracted text (default: 500000)
            timeout_seconds: Per-document processing timeout in seconds (default: 300)
            min_text_threshold: Minimum characters to consider text extraction
                               successful (default: 100)
            ocr_language: Tesseract language code(s) for OCR processing
                         (default: "rus+eng" for Russian+English)
            image_preprocessing: One of:
                * "fallback" (default) — try clean Tesseract first; only
                   re-run with deskew/binarize/denoise on pages that came
                   back near-empty. Best for mixed corpora: well-extracted
                   pages keep their original recall, problem pages get a
                   second chance via preprocessing.
                * "always" — preprocess every page. Useful when the input
                   is known to be uniformly noisy (archive batches).
                * "never" — disable preprocessing entirely.
                Legacy bool values (True/False) are accepted and mapped to
                "fallback" / "never" respectively for backward compat.

        Validates: Requirements 3.1, 4.1, 5.1, 6.1
        """
        self.page_limit = page_limit
        self.max_text_size = max_text_size
        self.timeout_seconds = timeout_seconds
        self.min_text_threshold = min_text_threshold
        self.image_preprocessing = self._normalize_preprocess_mode(image_preprocessing)
        self.psm = psm
        self.oem = oem
        self.max_image_size_bytes = max_image_size_bytes
        self._tesseract_config = self._build_tesseract_config(psm, oem)

        # Validate language pack on initialization
        validated_lang, lang_warnings = validate_language_pack(ocr_language)
        self.ocr_language = validated_lang
        self.language_warnings = lang_warnings

        if lang_warnings:
            logger.warning(
                f"Language pack validation warnings during OCRService initialization: {lang_warnings}"
            )

        logger.info(
            f"OCRService initialized with config: "
            f"page_limit={page_limit}, max_text_size={max_text_size}, "
            f"timeout_seconds={timeout_seconds}, min_text_threshold={min_text_threshold}, "
            f"ocr_language={self.ocr_language}, psm={psm}, oem={oem}"
        )

    @staticmethod
    def _build_tesseract_config(psm, oem) -> str:
        """Compose `--psm N --oem M` config string for pytesseract.

        Empty when both are None — pytesseract then uses Tesseract's
        defaults (PSM 3 auto, OEM 3 LSTM+legacy), preserving the legacy
        behavior for callers that don't tune.
        """
        parts = []
        if psm is not None:
            parts.append(f"--psm {int(psm)}")
        if oem is not None:
            parts.append(f"--oem {int(oem)}")
        return " ".join(parts)
    
    @staticmethod
    def _normalize_preprocess_mode(value) -> str:
        """Coerce flag value into one of {'never', 'fallback', 'always'}."""
        if isinstance(value, bool):
            return "fallback" if value else "never"
        s = str(value or "").strip().lower()
        if s in ("never", "off", "false", "0", "no"):
            return "never"
        if s in ("always", "on", "true", "1", "yes"):
            return "always"
        return "fallback"

    def _preprocess_image(self, image):
        """Run the OpenCV preprocessing chain. Fail-open on missing opencv."""
        try:
            from .image_preprocessing import preprocess_for_ocr
        except Exception as exc:  # noqa: BLE001 — same fail-open behavior
            logger.warning(
                "image_preprocessing_unavailable",
                extra={
                    "event_type": "image_preprocessing_unavailable",
                    "error": str(exc),
                },
            )
            return image
        return preprocess_for_ocr(image)

    def _maybe_preprocess(self, image):
        """Used by `always` and the unconditional `process_image` path."""
        if self.image_preprocessing == "never":
            return image
        return self._preprocess_image(image)

    def should_use_ocr(self, extracted_text: str) -> bool:
        """
        Determine if OCR is needed based on text length threshold.
        
        Compares the length of extracted text against the configured minimum
        text threshold. If the text is below the threshold, OCR is needed.
        
        Args:
            extracted_text: Text extracted from PDF using standard text extraction
            
        Returns:
            True if text length is below threshold (OCR needed),
            False if text length is at or above threshold (text extraction sufficient)
        
        Validates: Requirements 2.2, 3.2
        """
        text_length = len(extracted_text)
        needs_ocr = text_length < self.min_text_threshold
        
        logger.debug(
            f"Text length check: {text_length} characters, "
            f"threshold: {self.min_text_threshold}, "
            f"needs_ocr: {needs_ocr}"
        )
        
        return needs_ocr
    
    def process_image(self, image_bytes: bytes, filename: str) -> OCRResult:
        """
        Extract text from an image file (PNG, JPG, JPEG).
        
        Loads the image from bytes, applies OCR using Tesseract with the configured
        language, and returns the extracted text with metadata. Enforces timeout
        and text size limits.
        
        Args:
            image_bytes: Raw image file bytes
            filename: Original filename for logging
            
        Returns:
            OCRResult containing extracted text and metadata
            
        Raises:
            OCRTimeoutError: If processing exceeds timeout
            OCRProcessingError: If OCR fails

        Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 5.2, 5.3, 6.2, 7.1, 9.1
        """
        from PIL import Image
        import pytesseract
        
        logger.info(
            f"OCR processing started for image: {filename}",
            extra={
                "ocr_filename": filename,
                "file_size": len(image_bytes),
                "file_type": "image"
            }
        )

        max_image_size_bytes = self.max_image_size_bytes
        if max_image_size_bytes and len(image_bytes) > max_image_size_bytes:
            logger.warning(
                "ocr_image_size_limit_exceeded",
                extra={
                    "event_type": "ocr_image_size_limit_exceeded",
                    "ocr_filename": filename,
                    "file_size": len(image_bytes),
                    "max_size": max_image_size_bytes,
                },
            )
            return OCRResult(
                extracted_text="",
                status="FAILED",
                method="ocr",
                pages_total=1,
                pages_processed=0,
                warnings=["image_size_limit_exceeded"],
                error="Image exceeds maximum allowed size",
            )
        
        # Track start time for duration logging
        import time
        start_time = time.time()

        try:
            # B5: pytesseract has its own native timeout, which works
            # under any worker pool (gevent/eventlet/threads/main) and
            # cannot be swallowed by C-extensions in tesseract — unlike
            # the SIGALRM approach this used to use, which silently
            # broke on non-main threads and on OCRs that spent all
            # their time inside libtesseract. Celery `task_time_limit`
            # (B4) covers the outer envelope; this is the inner one.
            image = Image.open(io.BytesIO(image_bytes))

            if self.image_preprocessing == "always":
                image = self._preprocess_image(image)

            try:
                extracted_text = pytesseract.image_to_string(
                    image,
                    lang=self.ocr_language,
                    timeout=self.timeout_seconds,
                    config=self._tesseract_config,
                )
                if (
                    self.image_preprocessing == "fallback"
                    and len(extracted_text) < self._PREPROCESS_FALLBACK_PER_PAGE_MIN
                ):
                    preprocessed = self._preprocess_image(image)
                    if preprocessed is not image:
                        retry_text = pytesseract.image_to_string(
                            preprocessed,
                            lang=self.ocr_language,
                            timeout=self.timeout_seconds,
                            config=self._tesseract_config,
                        )
                        if len(retry_text) > len(extracted_text):
                            extracted_text = retry_text
            except RuntimeError as exc:
                # pytesseract raises RuntimeError("Tesseract process timeout")
                # when its internal timeout fires. Translate to our
                # domain exception so the caller's try/except keeps
                # working unchanged.
                if 'timeout' in str(exc).lower():
                    raise OCRTimeoutError("OCR processing timeout") from exc
                raise

            # Initialize warnings list
            warnings = list(self.language_warnings)

            # Apply text size limiting
            if len(extracted_text) > self.max_text_size:
                extracted_text = extracted_text[:self.max_text_size]
                warnings.append("text_truncated")
                logger.info(
                    f"Text truncated to {self.max_text_size} characters for {filename}"
                )

            # Calculate duration
            duration_ms = int((time.time() - start_time) * 1000)

            # Log successful completion
            logger.info(
                f"OCR processing completed for image: {filename}",
                extra={
                    "ocr_filename": filename,
                    "duration_ms": duration_ms,
                    "status": "DONE",
                    "method": "ocr",
                    "pages_processed": 1,
                    "text_length": len(extracted_text),
                    "warnings": warnings
                }
            )

            return OCRResult(
                extracted_text=extracted_text,
                status="DONE",
                method="ocr",
                pages_total=1,
                pages_processed=1,
                warnings=warnings,
                error=None
            )

        except OCRTimeoutError:
            # Timeout occurred
            duration_ms = int((time.time() - start_time) * 1000)
            error_msg = "OCR processing timeout"
            
            logger.error(
                f"OCR timeout for image: {filename}",
                extra={
                    "ocr_filename": filename,
                    "duration_ms": duration_ms,
                    "timeout_seconds": self.timeout_seconds
                },
                exc_info=True
            )
            
            return OCRResult(
                extracted_text="",
                status="FAILED",
                method="ocr",
                pages_total=1,
                pages_processed=0,
                warnings=list(self.language_warnings),
                error=error_msg
            )
        
        except Exception as e:
            # OCR processing failed
            duration_ms = int((time.time() - start_time) * 1000)
            error_msg = f"OCR processing failed: {str(e)}"
            
            logger.error(
                f"OCR processing failed for image: {filename}",
                extra={
                    "ocr_filename": filename,
                    "duration_ms": duration_ms,
                    "error": str(e)
                },
                exc_info=True
            )
            
            return OCRResult(
                extracted_text="",
                status="FAILED",
                method="ocr",
                pages_total=1,
                pages_processed=0,
                warnings=list(self.language_warnings),
                error=error_msg
            )
    
    def process_pdf(self, pdf_bytes: bytes, filename: str) -> OCRResult:
        """
        Extract text from a scanned PDF using OCR.
        
        Converts PDF pages to images and processes with Tesseract. Applies page
        limit BEFORE conversion to avoid excessive resource usage. Processes pages
        sequentially to limit memory footprint. Ensures temporary files are cleaned up.
        
        Args:
            pdf_bytes: Raw PDF file bytes
            filename: Original filename for logging
            
        Returns:
            OCRResult containing extracted text and metadata
            
        Raises:
            OCRTimeoutError: If processing exceeds timeout
            OCRProcessingError: If OCR fails
        
        Validates: Requirements 2.3, 2.4, 4.2, 4.3, 4.4, 5.2, 5.3, 6.2, 7.1, 9.1, 9.2
        """
        import time
        from pdf2image import convert_from_bytes
        import pytesseract
        
        logger.info(
            f"OCR processing started for PDF: {filename}",
            extra={
                "ocr_filename": filename,
                "file_size": len(pdf_bytes),
                "file_type": "pdf"
            }
        )
        
        # Track start time for duration logging — used both for the
        # logged duration and as the budget for per-page timeouts.
        start_time = time.time()

        # B5: pytesseract has a native timeout that works under any
        # worker pool and cannot be swallowed by C-extensions; SIGALRM
        # silently broke on non-main threads and inside libtesseract.
        # The remaining budget shrinks with each processed page so the
        # whole `process_pdf` call still respects `self.timeout_seconds`
        # rather than allowing each page that much time on its own.
        def remaining_budget() -> int:
            elapsed = time.time() - start_time
            return max(1, int(self.timeout_seconds - elapsed))

        try:
            # Get total page count using pdfplumber
            total_pages = get_pdf_page_count(pdf_bytes)

            if total_pages == 0:
                raise OCRProcessingError("Failed to read PDF or PDF has no pages")

            # Calculate pages_to_process = min(total_pages, page_limit)
            pages_to_process = min(total_pages, self.page_limit)

            logger.debug(
                f"PDF has {total_pages} pages, will process {pages_to_process} pages"
            )

            # Initialize warnings list
            warnings = list(self.language_warnings)

            # Add "pages_truncated" warning if pages_to_process < total_pages
            if pages_to_process < total_pages:
                warnings.append("pages_truncated")
                warnings.append("page_limit_reached")
                logger.warning(
                    "ocr_page_limit_reached",
                    extra={
                        "event_type": "ocr_page_limit_reached",
                        "ocr_filename": filename,
                        "pages_total": total_pages,
                        "pages_processed": pages_to_process,
                        "page_limit": self.page_limit,
                    },
                )

            # Convert PDF pages to images sequentially and process each
            # immediately. This limits memory usage by not loading all
            # pages at once.
            extracted_texts = []

            for page_num in range(1, pages_to_process + 1):
                if time.time() - start_time >= self.timeout_seconds:
                    raise OCRTimeoutError("OCR processing timeout")

                logger.debug(f"Processing page {page_num} of {pages_to_process}")

                # Convert single page to image at 200 DPI
                images = convert_from_bytes(
                    pdf_bytes,
                    dpi=200,
                    first_page=page_num,
                    last_page=page_num
                )

                if not images:
                    logger.warning(f"No image generated for page {page_num}")
                    continue

                # Process the single page image with pytesseract immediately
                image = images[0]

                if self.image_preprocessing == "always":
                    image = self._preprocess_image(image)

                try:
                    page_text = pytesseract.image_to_string(
                        image,
                        lang=self.ocr_language,
                        timeout=remaining_budget(),
                        config=self._tesseract_config,
                    )
                    if (
                        self.image_preprocessing == "fallback"
                        and len(page_text) < self._PREPROCESS_FALLBACK_PER_PAGE_MIN
                    ):
                        # Clean run came back near-empty — re-OCR the page
                        # after deskew/binarize/denoise. Spends the cost
                        # only on pages that need it; well-extracted pages
                        # keep their original recall.
                        preprocessed = self._preprocess_image(image)
                        if preprocessed is not image:
                            retry_text = pytesseract.image_to_string(
                                preprocessed,
                                lang=self.ocr_language,
                                timeout=remaining_budget(),
                                config=self._tesseract_config,
                            )
                            if len(retry_text) > len(page_text):
                                page_text = retry_text
                                logger.debug(
                                    "preprocess_fallback_helped",
                                    extra={
                                        "event_type": "preprocess_fallback_helped",
                                        "page": page_num,
                                        "before": len(page_text),
                                        "after": len(retry_text),
                                    },
                                )
                except RuntimeError as exc:
                    if 'timeout' in str(exc).lower():
                        raise OCRTimeoutError("OCR processing timeout") from exc
                    raise

                extracted_texts.append(page_text)

                # Release image memory
                del image
                del images

            # Concatenate text from all processed pages
            concatenated_text = "\n".join(extracted_texts)

            # Apply text size limiting (truncate if exceeds max_text_size)
            if len(concatenated_text) > self.max_text_size:
                concatenated_text = concatenated_text[:self.max_text_size]
                if "text_truncated" not in warnings:
                    warnings.append("text_truncated")
                logger.info(
                    f"Text truncated to {self.max_text_size} characters for {filename}"
                )

            # Calculate duration
            duration_ms = int((time.time() - start_time) * 1000)

            # Log successful completion
            logger.info(
                f"OCR processing completed for PDF: {filename}",
                extra={
                    "ocr_filename": filename,
                    "duration_ms": duration_ms,
                    "status": "DONE",
                    "method": "ocr",
                    "pages_total": total_pages,
                    "pages_processed": pages_to_process,
                    "text_length": len(concatenated_text),
                    "warnings": warnings
                }
            )

            return OCRResult(
                extracted_text=concatenated_text,
                status="DONE",
                method="ocr",
                pages_total=total_pages,
                pages_processed=pages_to_process,
                warnings=warnings,
                error=None
            )

        except OCRTimeoutError:
            # Timeout occurred
            duration_ms = int((time.time() - start_time) * 1000)
            error_msg = "OCR processing timeout"
            
            logger.error(
                f"OCR timeout for PDF: {filename}",
                extra={
                    "ocr_filename": filename,
                    "duration_ms": duration_ms,
                    "timeout_seconds": self.timeout_seconds
                },
                exc_info=True
            )
            
            # Handle exceptions and return OCRResult with status="FAILED"
            return OCRResult(
                extracted_text="",
                status="FAILED",
                method="ocr",
                pages_total=0,
                pages_processed=0,
                warnings=list(self.language_warnings),
                error=error_msg
            )
        
        except Exception as e:
            # OCR processing failed
            duration_ms = int((time.time() - start_time) * 1000)
            error_msg = f"OCR processing failed: {str(e)}"
            
            logger.error(
                f"OCR processing failed for PDF: {filename}",
                extra={
                    "ocr_filename": filename,
                    "duration_ms": duration_ms,
                    "error": str(e)
                },
                exc_info=True
            )
            
            # Handle exceptions and return OCRResult with status="FAILED"
            return OCRResult(
                extracted_text="",
                status="FAILED",
                method="ocr",
                pages_total=0,
                pages_processed=0,
                warnings=list(self.language_warnings),
                error=error_msg
            )
