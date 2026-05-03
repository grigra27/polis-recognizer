"""
OCR Configuration Module

This module provides configuration management for OCR processing.
All configuration parameters can be overridden via environment variables.
"""

import os
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Tuple

logger = logging.getLogger(__name__)


@dataclass
class OCRResult:
    """
    Result of OCR processing operation.
    
    This dataclass encapsulates all metadata and results from an OCR operation,
    including extracted text, processing status, method used, page information,
    warnings, and any errors encountered.
    
    Attributes:
        extracted_text: The text extracted from the document via OCR
        status: Processing status - "DONE" for success, "FAILED" for failure
        method: Extraction method used - always "ocr" for OCR operations
        pages_total: Total number of pages in the document
        pages_processed: Number of pages actually processed (may be less due to page limit)
        warnings: List of warning messages (e.g., "pages_truncated", "text_truncated")
        error: Error message if processing failed, None if successful
    
    Validates: Requirements 1.4, 1.5, 1.6, 2.4, 4.4, 5.3
    """
    
    extracted_text: str
    status: str  # "DONE" or "FAILED"
    method: str  # "ocr"
    pages_total: int
    pages_processed: int
    warnings: List[str] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class OCRConfig:
    """
    Single source of truth for OCR runtime configuration. Loaded once
    via :func:`get_ocr_config` and reused process-wide. Production code
    paths (`tasks/pre_ingest.py`, `views_ux.py`) construct OCRService
    from this dataclass — the values here ARE what runs against user
    uploads. Django `settings.OCR_CONFIG` no longer exists; storage/
    lifecycle limits live in `settings.FILE_LIFECYCLE_CONFIG`.

    All parameters are read from environment variables:
    - OCR_PAGE_LIMIT: Maximum number of pages to process (default: 50)
    - OCR_MAX_TEXT_LENGTH (or legacy OCR_MAX_TEXT_SIZE): Maximum characters
      in extracted text (default: 500000)
    - OCR_TIMEOUT_SECONDS: Per-document processing timeout (default: 300)
    - OCR_MIN_TEXT_THRESHOLD: Minimum characters to consider text extraction
      successful (default: 100)
    - OCR_LANGUAGE: Tesseract language code (default: "rus+eng" for
      Russian+English)

    Attributes:
        page_limit: Maximum number of pages to process per document
        max_text_size: Maximum characters in extracted text
        timeout_seconds: Per-document processing timeout in seconds
        min_text_threshold: Minimum characters to consider text extraction successful
        ocr_language: Tesseract language code(s) for OCR processing
    """
    
    page_limit: int = 50
    max_text_size: int = 500000
    timeout_seconds: int = 300
    min_text_threshold: int = 100
    ocr_language: str = "rus+eng"
    
    @classmethod
    def from_env(cls) -> 'OCRConfig':
        """
        Create OCRConfig from environment variables with validation.
        
        Returns:
            OCRConfig instance with values from environment or defaults
            
        Raises:
            ValueError: If any configuration value is invalid
        """
        try:
            page_limit = int(os.getenv('OCR_PAGE_LIMIT', '50'))
            max_text_size_raw = os.getenv('OCR_MAX_TEXT_LENGTH', os.getenv('OCR_MAX_TEXT_SIZE', '500000'))
            max_text_size = int(max_text_size_raw)
            timeout_seconds = int(os.getenv('OCR_TIMEOUT_SECONDS', '300'))
            min_text_threshold = int(os.getenv('OCR_MIN_TEXT_THRESHOLD', '100'))
            ocr_language = os.getenv('OCR_LANGUAGE', 'rus+eng')
            
            # Validate configuration values
            if page_limit <= 0:
                raise ValueError(f"OCR_PAGE_LIMIT must be positive, got: {page_limit}")
            
            if max_text_size <= 0:
                raise ValueError(f"OCR_MAX_TEXT_SIZE must be positive, got: {max_text_size}")
            
            if timeout_seconds <= 0:
                raise ValueError(f"OCR_TIMEOUT_SECONDS must be positive, got: {timeout_seconds}")
            
            if min_text_threshold < 0:
                raise ValueError(f"OCR_MIN_TEXT_THRESHOLD must be non-negative, got: {min_text_threshold}")
            
            if not ocr_language or not ocr_language.strip():
                raise ValueError("OCR_LANGUAGE must not be empty")
            
            return cls(
                page_limit=page_limit,
                max_text_size=max_text_size,
                timeout_seconds=timeout_seconds,
                min_text_threshold=min_text_threshold,
                ocr_language=ocr_language
            )
            
        except ValueError as e:
            # Re-raise ValueError with context
            raise ValueError(f"Invalid OCR configuration: {str(e)}")
    
    def validate(self) -> None:
        """
        Validate configuration values.
        
        Raises:
            ValueError: If any configuration value is invalid
        """
        if self.page_limit <= 0:
            raise ValueError(f"page_limit must be positive, got: {self.page_limit}")
        
        if self.max_text_size <= 0:
            raise ValueError(f"max_text_size must be positive, got: {self.max_text_size}")
        
        if self.timeout_seconds <= 0:
            raise ValueError(f"timeout_seconds must be positive, got: {self.timeout_seconds}")
        
        if self.min_text_threshold < 0:
            raise ValueError(f"min_text_threshold must be non-negative, got: {self.min_text_threshold}")
        
        if not self.ocr_language or not self.ocr_language.strip():
            raise ValueError("ocr_language must not be empty")


# Global configuration instance
# This can be imported and used throughout the OCR module
_config: Optional[OCRConfig] = None


def get_ocr_config() -> OCRConfig:
    """
    Get the global OCR configuration instance.
    
    Lazily initializes the configuration from environment variables on first call.
    
    Returns:
        OCRConfig instance
    """
    global _config
    if _config is None:
        _config = OCRConfig.from_env()
    return _config


def reset_ocr_config() -> None:
    """
    Reset the global OCR configuration instance.
    
    This is primarily useful for testing to force re-reading environment variables.
    """
    global _config
    _config = None


def validate_language_pack(lang: str) -> Tuple[str, List[str]]:
    """
    Validate that required language pack is available.
    
    Checks if the specified language pack(s) are installed in Tesseract.
    Parses multi-language strings (e.g., "rus+eng") and validates each language.
    If any language pack is missing, adds a warning and falls back to English.
    
    Args:
        lang: Tesseract language code(s), can be single (e.g., "rus") or 
              multi-language (e.g., "rus+eng")
    
    Returns:
        Tuple of (language_to_use, warnings):
        - language_to_use: The validated language string or "eng" if fallback needed
        - warnings: List of warning messages for missing language packs
    
    Examples:
        >>> validate_language_pack("eng")
        ("eng", [])
        
        >>> validate_language_pack("rus+eng")
        ("rus+eng", [])
        
        >>> validate_language_pack("nonexistent")
        ("eng", ["lang_pack_missing: nonexistent"])
    
    Validates: Design - OCR Language Configuration
    """
    warnings = []
    
    try:
        # Import pytesseract here to avoid import errors if not installed
        import pytesseract
        
        # Get available language packs from Tesseract
        available_langs = pytesseract.get_languages()
        
        # Parse multi-language string (e.g., "rus+eng" -> ["rus", "eng"])
        required_langs = lang.split('+')
        
        # Check each required language
        for required_lang in required_langs:
            required_lang = required_lang.strip()
            if required_lang not in available_langs:
                warning_msg = f"lang_pack_missing: {required_lang}"
                warnings.append(warning_msg)
                logger.warning(f"Language pack missing: {required_lang}")
        
        # If any language is missing, fallback to English
        if warnings:
            logger.warning(f"Language pack(s) missing for '{lang}', falling back to 'eng'")
            return 'eng', warnings
        
        return lang, warnings
        
    except ImportError:
        # pytesseract not installed
        logger.error("pytesseract not installed, cannot validate language packs")
        warnings.append("lang_pack_missing: pytesseract_not_installed")
        return 'eng', warnings
        
    except Exception as e:
        # Unexpected error during language pack validation
        logger.error(f"Error checking language packs: {e}", exc_info=True)
        warnings.append("lang_pack_missing")
        return 'eng', warnings
