"""Hybrid PDF extraction: pypdf text + pdfplumber tables in one pass.

Why this exists: full pdfplumber-as-extractor regresses our regex
parsers because pdfplumber's text-reconstruction differs subtly from
pypdf's (e.g. policy_period dates land in different shapes). But
pdfplumber's ``page.extract_tables()`` does recover the column structure
of АльфаСтрахование KASKO ПОЛНОЕ rows that pypdf flattens. The hybrid
extractor takes the BEST of both: pypdf text (so the existing regex
parsers behave identically to today) plus pdfplumber tables (so the
table-aware paths added in B3 phase 2 can fire).

Cost: pdfplumber.open() + extract_tables() per page is ~2-3x the bare
pypdf path on dense forms. On the digital_pdf flow only — scanned PDFs
take the OCR fallback that has no tables. Acceptable for the recall gain
the table-aware paths give on Альфа forms.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from .policy_ingestion import ExtractedTextResult, PolicyIngestionService


class HybridIngestionService:
    """pypdf text extractor + pdfplumber tables. Same return contract."""

    MAX_PAGES = 20

    def __init__(
        self, *, text_service: Optional[PolicyIngestionService] = None
    ) -> None:
        self._text_service = text_service or PolicyIngestionService()

    def extract_text_from_pdf(self, file_path: str) -> ExtractedTextResult:
        # Step 1 — text via pypdf, exactly as today. Whatever happens
        # with tables below, the text path is unchanged so all existing
        # regex parsers see identical input.
        result = self._text_service.extract_text_from_pdf(file_path)

        # Step 2 — tables via pdfplumber. Failures here MUST NOT poison
        # the text result that the rest of the pipeline depends on; we
        # swallow exceptions and ship `tables=[]`.
        try:
            import pdfplumber
        except ImportError:
            return result

        path = Path(file_path)
        if not path.exists():
            return result

        tables: List[List[List[List[str]]]] = []
        try:
            with pdfplumber.open(file_path) as pdf:
                pages_to_process = min(len(pdf.pages), self.MAX_PAGES)
                for page_num in range(pages_to_process):
                    page = pdf.pages[page_num]
                    try:
                        page_tables = page.extract_tables() or []
                    except Exception:
                        # Bad page geometry. Skip — keep the rest.
                        page_tables = []
                    tables.append(page_tables)
        except Exception:
            # pdfplumber.open itself can raise on malformed PDFs.
            # Text already succeeded; ship without tables.
            return result

        result.tables = tables
        return result
