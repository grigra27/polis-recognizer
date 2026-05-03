"""Exceptions raised by the OCR / extraction pipeline.

These cover failure modes of the recognizer itself (not the
polishelper application). Polishelper-specific exceptions
(PlaybookNotFoundError, LLMGuardViolationError) live in
``apps.analyses.exceptions``.
"""


class OCRTimeoutError(Exception):
    """Raised when OCR processing exceeds the configured timeout.

    The pipeline enforces a per-document timeout (default 300s) to keep
    a hung tesseract subprocess from blocking the request indefinitely.
    """
    pass


class OCRProcessingError(Exception):
    """Raised when the OCR engine fails on otherwise valid input.

    Covers corrupted files, invalid image data, or internal tesseract
    crashes that aren't a timeout.
    """
    pass


class UnsupportedFileTypeError(Exception):
    """Raised when a file with an unsupported extension is submitted.

    The recognizer accepts PDF, PNG, JPG, JPEG.
    """
    pass
