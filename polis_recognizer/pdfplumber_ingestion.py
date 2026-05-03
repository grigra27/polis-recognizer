"""Layout-aware PDF text extraction via pdfplumber.

Drop-in replacement for ``PolicyIngestionService.extract_text_from_pdf``.
Same return contract (``ExtractedTextResult``), same per-page / per-text
limits and warning codes, but uses pdfplumber under the hood instead of
pypdf. The benefit comes from pdfplumber's layout-aware behavior:

- Word boundaries reconstructed from glyph bounding boxes — fixes the
  "glued text-layer" problem on АбсолютСтрахование and similar.
- Table-cell ordering matches the visual reading order — fixes
  АльфаСтрахование form-mask layouts where pypdf returned cells in
  PDF-stream order instead of left-to-right top-to-bottom.

Used only when settings.PDF_EXTRACTOR == "pdfplumber". The pypdf path
remains the default until benchmarks confirm pdfplumber doesn't
regress on the live corpus.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

import pdfplumber

from .policy_ingestion import ExtractedTextResult


class PdfPlumberIngestionService:
    """pdfplumber-backed text extractor with the same contract as PolicyIngestionService."""

    MAX_PAGES = 20
    MAX_CHARS = 200_000
    MIN_TEXT_THRESHOLD = 100

    def extract_text_from_pdf(self, file_path: str) -> ExtractedTextResult:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"PDF file not found: {file_path}")

        warnings: List[str] = []
        chunks: List[str] = []
        # Per-page list of tables; each table is a list of rows; each row
        # a list of cells (strings or None). Empty list when no tables are
        # detected on a page so the per-page index aligns with `pages`.
        tables: List[List[List[List[str]]]] = []
        pages_processed = 0

        try:
            with pdfplumber.open(file_path) as pdf:
                total_pages = len(pdf.pages)
                pages_to_process = min(total_pages, self.MAX_PAGES)
                if total_pages > self.MAX_PAGES:
                    warnings.append("pages_truncated")

                for page_num in range(pages_to_process):
                    page = pdf.pages[page_num]
                    # `extract_text(layout=False)` reconstructs reading order
                    # from glyph bounding boxes and inserts spaces where the
                    # PDF stream omits them. layout=True preserves visual
                    # positioning — useful for human inspection but introduces
                    # excess whitespace that breaks our regex parsers.
                    page_text = page.extract_text() or ""
                    if page_text:
                        chunks.append(page_text)
                    # Tables are extracted alongside text. pdfplumber
                    # uses ruling-line + edge detection; on table-less
                    # pages this returns []. The cost is ~3-5x the bare
                    # text path on dense forms — acceptable for the
                    # 24%-of-corpus digital_pdf flow only.
                    try:
                        page_tables = page.extract_tables() or []
                    except Exception:
                        # extract_tables() can raise on unusual page
                        # geometry. Don't let it fail the whole document.
                        page_tables = []
                    tables.append(page_tables)
                    pages_processed += 1

            extracted_text = "\n".join(chunks)

            if len(extracted_text) > self.MAX_CHARS:
                extracted_text = extracted_text[:self.MAX_CHARS]
                warnings.append("text_truncated")

            if len(extracted_text) < self.MIN_TEXT_THRESHOLD:
                extracted_text = ""
                warnings.append("no_text_layer")

        except Exception as exc:
            raise Exception(f"Failed to extract text from PDF (pdfplumber): {exc}") from exc

        return ExtractedTextResult(
            text=extracted_text,
            pages=pages_processed,
            warnings=warnings,
            tables=tables,
        )
