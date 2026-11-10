"""Small regressions for the PNG/JPEG upload boundary and Pillow 13."""

from io import BytesIO
from unittest.mock import Mock

import numpy as np
import pytest
from PIL import Image, XbmImagePlugin

from polis_recognizer.image_preprocessing import preprocess_for_ocr
from polis_recognizer.ocr_service import OCRService


@pytest.fixture
def service(monkeypatch):
    monkeypatch.setattr(
        "polis_recognizer.ocr_service.validate_language_pack", lambda lang: (lang, [])
    )
    return OCRService(image_preprocessing="never", max_image_size_bytes=1024 * 1024)


def test_renamed_xbm_rejected_before_decoder_or_tesseract(service, monkeypatch):
    xbm = (
        b"#define harmless_width 1\n#define harmless_height 1\n"
        b"static char harmless_bits[] = {\n0x00\n};\n"
    )
    with Image.open(BytesIO(xbm)) as image:
        assert image.format == "XBM"
        assert image.size == (1, 1)
    decoder = Mock(side_effect=AssertionError("XBM decoder must not run"))
    ocr = Mock(side_effect=AssertionError("OCR must not run"))
    monkeypatch.setattr(XbmImagePlugin.XbmImageFile, "_open", decoder)
    monkeypatch.setattr("pytesseract.image_to_string", ocr)

    result = service.process_image(xbm, "renamed.png")

    assert result.status == "FAILED"
    assert result.pages_processed == 0
    decoder.assert_not_called()
    ocr.assert_not_called()


@pytest.mark.parametrize("image_format", ["PNG", "JPEG"])
def test_real_image_decoding_and_exif_orientation(service, monkeypatch, image_format):
    buf = BytesIO()
    image = Image.new("RGB", (40, 20), "white")
    exif = Image.Exif()
    exif[274] = 6
    image.save(buf, format=image_format, exif=exif)
    sizes = []

    def read(image, **kwargs):
        sizes.append(image.size)
        assert image.getexif().get(274) is None
        return "POLICY 12345"

    monkeypatch.setattr("pytesseract.image_to_string", read)
    result = service.process_image(buf.getvalue(), "policy." + image_format.lower())
    assert result.status == "DONE"
    assert result.extracted_text == "POLICY 12345"
    assert sizes == [(20, 40)]


def test_opencv_preprocessing_returns_grayscale_on_pillow13():
    image = Image.fromarray(np.tile(np.arange(200, dtype=np.uint8), (100, 1)))
    result = preprocess_for_ocr(image)
    assert result is not image  # best-effort fallback must not hide an API error
    assert result.mode == "L"
    assert result.width >= image.width
