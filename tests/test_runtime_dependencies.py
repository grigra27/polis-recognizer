"""Small real PDF/image/OCR checks for the Pillow 13 release (no mocks).

Run with Tesseract rus+eng and Poppler installed, as in the package CI.
All inputs are synthetic and generated in memory.
"""

import shutil
from io import BytesIO

import pdfplumber
import pytest
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader, PdfWriter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

from polis_recognizer.ocr_service import OCRService
from polis_recognizer.pdf_extraction_router import PdfExtractionRouter, build_text_service


def _image_bytes(image_format="PNG", rotated=False):
    image = Image.new("RGB", (1200, 300), "white")
    ImageDraw.Draw(image).text(
        (70, 100), "POLICY 12345", fill="black", font=ImageFont.load_default(size=75)
    )
    exif = Image.Exif()
    if rotated:
        image = image.transpose(Image.Transpose.ROTATE_90)
        exif[274] = 6
    out = BytesIO()
    image.save(out, format=image_format, exif=exif)
    return out.getvalue()


def _pdf_bytes(scanned=False, pages=2):
    out = BytesIO()
    canvas = Canvas(out)
    for page in range(pages):
        if scanned:
            canvas.drawImage(ImageReader(BytesIO(_image_bytes())), 20, 400, 560, 140)
        else:
            for line in range(6):
                canvas.drawString(30, 750 - 25 * line, f"POLICY 12345 page {page + 1} coverage details")
        canvas.showPage()
    canvas.save()
    return out.getvalue()


@pytest.fixture
def ocr():
    if not shutil.which("tesseract") or not shutil.which("pdftoppm"):
        pytest.skip("Tesseract and Poppler are required for the real runtime smoke checks")
    service = OCRService(ocr_language="rus+eng", timeout_seconds=30, image_preprocessing="always")
    assert service.ocr_language == "rus+eng"
    return service


@pytest.mark.parametrize("image_format,rotated", [("PNG", False), ("JPEG", False), ("JPEG", True)])
def test_real_image_ocr_and_exif(ocr, image_format, rotated):
    result = ocr.process_image(_image_bytes(image_format, rotated), "policy." + image_format.lower())
    assert result.status == "DONE", result.error
    assert "12345" in result.extracted_text
    assert result.pages_processed == 1


@pytest.mark.parametrize("choice", ["pypdf", "pdfplumber", "hybrid"])
def test_real_text_multipage_pdf(ocr, choice):
    data = _pdf_bytes()
    assert len(PdfReader(BytesIO(data)).pages) == 2
    with pdfplumber.open(BytesIO(data)) as pdf:
        assert "12345" in pdf.pages[1].extract_text()
    result = PdfExtractionRouter(ocr, build_text_service(choice)).route(data, "text.pdf")
    assert result.status == "DONE", result.error
    assert "12345" in result.extracted_text
    assert result.pages_processed == 2
    assert result.extraction_method != "ocr"


def test_real_scanned_multipage_pdf(ocr):
    result = ocr.process_pdf(_pdf_bytes(scanned=True), "scan.pdf")
    assert result.status == "DONE", result.error
    assert result.pages_total == 2
    assert result.pages_processed == 2
    assert "12345" in result.extracted_text


def test_real_pdf_page_limit_and_malformed_input(ocr):
    ocr.page_limit = 1
    result = ocr.process_pdf(_pdf_bytes(scanned=True), "limited.pdf")
    assert result.status == "DONE", result.error
    assert result.pages_total == 2
    assert result.pages_processed == 1
    assert result.warnings
    assert ocr.process_pdf(b"not a PDF", "malformed.pdf").status == "FAILED"


@pytest.mark.parametrize("algorithm", ["AES-128", "AES-256"])
def test_real_aes_pdf(algorithm):
    writer = PdfWriter(clone_from=BytesIO(_pdf_bytes()))
    writer.encrypt("synthetic-password", algorithm=algorithm)
    encrypted = BytesIO()
    writer.write(encrypted)
    reader = PdfReader(BytesIO(encrypted.getvalue()))
    assert reader.is_encrypted
    assert reader.decrypt("synthetic-password")
    assert "12345" in reader.pages[0].extract_text()
    with pdfplumber.open(BytesIO(encrypted.getvalue()), password="synthetic-password") as pdf:
        assert len(pdf.pages) == 2
        assert "12345" in pdf.pages[1].extract_text()


def test_malformed_and_oversize_images_fail(ocr):
    assert ocr.process_image(b"not an image", "bad.png").status == "FAILED"
    limited = OCRService(ocr_language="rus+eng", max_image_size_bytes=10)
    result = limited.process_image(_image_bytes(), "oversize.png")
    assert result.status == "FAILED"
    assert result.warnings == ["image_size_limit_exceeded"]
