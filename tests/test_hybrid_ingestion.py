"""Tests for HybridIngestionService — pypdf text + pdfplumber tables."""

from unittest.mock import MagicMock, patch

import pytest

from polis_recognizer.hybrid_ingestion import HybridIngestionService
from polis_recognizer.policy_ingestion import ExtractedTextResult


def _stub_text_service(text="hello\nworld", pages=2, warnings=None):
    svc = MagicMock()
    svc.extract_text_from_pdf.return_value = ExtractedTextResult(
        text=text,
        pages=pages,
        warnings=list(warnings or []),
    )
    return svc


def test_returns_pypdf_text_unchanged(tmp_path):
    pdf_path = tmp_path / "fake.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")  # placeholder bytes, pdfplumber will fail to parse but we don't crash

    text_svc = _stub_text_service(text="extracted by pypdf")
    hybrid = HybridIngestionService(text_service=text_svc)

    result = hybrid.extract_text_from_pdf(str(pdf_path))

    # Text path must be untouched: same string the wrapped service returned.
    assert result.text == "extracted by pypdf"
    text_svc.extract_text_from_pdf.assert_called_once_with(str(pdf_path))


def test_pdfplumber_open_failure_does_not_break_text(tmp_path):
    """If pdfplumber raises (malformed PDF), text result still ships."""
    pdf_path = tmp_path / "bad.pdf"
    pdf_path.write_bytes(b"not a pdf")

    text_svc = _stub_text_service(text="text via pypdf")
    hybrid = HybridIngestionService(text_service=text_svc)

    with patch("pdfplumber.open", side_effect=Exception("malformed")):
        result = hybrid.extract_text_from_pdf(str(pdf_path))

    assert result.text == "text via pypdf"
    assert result.tables == []  # default-empty


def test_pdfplumber_per_page_failure_does_not_break_other_pages(tmp_path):
    """A bad page returns [] for that page; other pages still extract."""
    pdf_path = tmp_path / "ok.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    text_svc = _stub_text_service(text="text", pages=3)
    hybrid = HybridIngestionService(text_service=text_svc)

    good_page = MagicMock()
    good_page.extract_tables.return_value = [[["cell-A", "cell-B"]]]
    bad_page = MagicMock()
    bad_page.extract_tables.side_effect = Exception("bad geometry")
    other_page = MagicMock()
    other_page.extract_tables.return_value = []

    fake_pdf = MagicMock()
    fake_pdf.pages = [good_page, bad_page, other_page]
    fake_pdf.__enter__ = lambda self: fake_pdf
    fake_pdf.__exit__ = lambda self, *args: False

    with patch("pdfplumber.open", return_value=fake_pdf):
        result = hybrid.extract_text_from_pdf(str(pdf_path))

    assert result.tables == [[[["cell-A", "cell-B"]]], [], []]


def test_missing_file_returns_text_result_path_unchanged(tmp_path):
    """File-not-exists short-circuits before pdfplumber is touched.

    The wrapped pypdf service decides what to do with a missing file —
    we just return whatever it returned.
    """
    text_svc = _stub_text_service(text="(empty)", pages=0, warnings=["no_text_layer"])
    hybrid = HybridIngestionService(text_service=text_svc)

    nonexistent = str(tmp_path / "does-not-exist.pdf")
    result = hybrid.extract_text_from_pdf(nonexistent)

    assert result.text == "(empty)"
    assert "no_text_layer" in result.warnings
    assert result.tables == []
