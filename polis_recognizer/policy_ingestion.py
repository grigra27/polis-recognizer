"""
Policy Text Extraction Service

This module provides services for extracting text from policy PDF files.
It implements deterministic text-based PDF ingestion without OCR.

Data Contract:
--------------
The extraction metadata is stored in Analysis.contract_context_json following this schema:

{
  "extraction": {
    "status": "NOT_STARTED | DONE | FAILED",
    "method": "pdf_text | none",
    "pages": 0,
    "text_length": 0,
    "warnings": [],
    "error": null
  }
}

Status Values:
- NOT_STARTED: Extraction has not been attempted
- DONE: Extraction completed (with or without text)
- FAILED: Extraction failed due to error

Method Values:
- pdf_text: PDF text layer extraction was attempted
- none: No extraction method was attempted

Warning Codes:
- no_text_layer: PDF contains < 100 characters
- pages_truncated: PDF had > 20 pages
- text_truncated: Text exceeded 200,000 characters

The actual extracted text is stored in the separate Analysis.extracted_text TextField,
NOT in the JSON structure above.
"""

from dataclasses import dataclass, field
from typing import List
from pathlib import Path
from pypdf import PdfReader


@dataclass
class ExtractedTextResult:
    """Result of PDF text extraction.

    Attributes:
        text: The extracted text content (empty string if no text found).
        pages: Number of pages processed.
        warnings: List of warning codes (e.g., "no_text_layer", "pages_truncated").
        tables: Per-page list of tables, where each table is a list of
            rows and each row a list of cell strings. Only populated by
            layout-aware extractors (pdfplumber). The pypdf path leaves
            this empty. Parsers that don't care about tables ignore it.
    """
    text: str
    pages: int
    warnings: List[str]
    tables: List[List[List[List[str]]]] = field(default_factory=list)


class PolicyIngestionService:
    """Service for extracting text from policy PDF files.
    
    This service extracts text from PDFs with embedded text layers (no OCR).
    It enforces page limits, character limits, and minimum text thresholds.
    
    Constants:
        MAX_PAGES: Maximum number of pages to process (20)
        MAX_CHARS: Maximum characters to extract (200,000)
        MIN_TEXT_THRESHOLD: Minimum characters to consider text layer present (100)
    """
    
    MAX_PAGES = 20
    MAX_CHARS = 200_000
    MIN_TEXT_THRESHOLD = 100

    def extract_text_from_pdf(self, file_path: str) -> ExtractedTextResult:
        """
        Extract text from a PDF file.
        
        This method extracts text from PDFs with embedded text layers (no OCR).
        It enforces page limits (20 pages), character limits (200,000 chars),
        and minimum text thresholds (100 chars).
        
        Args:
            file_path: Absolute path to the PDF file
            
        Returns:
            ExtractedTextResult with text, page count, and warnings
            
        Raises:
            FileNotFoundError: If file does not exist
            Exception: If PDF is corrupted or cannot be read
        """
        # Check if file path exists
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"PDF file not found: {file_path}")
        
        warnings = []
        extracted_text = ""
        pages_processed = 0
        
        try:
            # Open PDF and get page count
            with open(file_path, 'rb') as pdf_file:
                reader = PdfReader(pdf_file)
                total_pages = len(reader.pages)
                
                # Enforce page limit
                pages_to_process = min(total_pages, self.MAX_PAGES)
                if total_pages > self.MAX_PAGES:
                    warnings.append("pages_truncated")
                
                # Extract text from pages with safe None handling
                for page_num in range(pages_to_process):
                    page = reader.pages[page_num]
                    page_text = page.extract_text()
                    # CRITICAL: PyPDF2/pypdf extract_text() can return None - treat as empty string
                    if page_text:
                        extracted_text += page_text
                    pages_processed += 1
                
                # Enforce character limit
                if len(extracted_text) > self.MAX_CHARS:
                    extracted_text = extracted_text[:self.MAX_CHARS]
                    warnings.append("text_truncated")
                
                # Check minimum text threshold
                if len(extracted_text) < self.MIN_TEXT_THRESHOLD:
                    extracted_text = ""
                    warnings.append("no_text_layer")
                
        except Exception as e:
            raise Exception(f"Failed to extract text from PDF: {str(e)}")
        
        return ExtractedTextResult(
            text=extracted_text,
            pages=pages_processed,
            warnings=warnings
        )
