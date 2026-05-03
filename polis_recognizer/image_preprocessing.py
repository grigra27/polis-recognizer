"""Image preprocessing pipeline before Tesseract OCR.

Tesseract was trained on clean printed text. Real-world scans of insurance
policies — especially the 76% of our corpus that's archive scans from
2018-2023 — usually arrive skewed, with grey/colored backgrounds, faint
glyphs, JPEG ringing, and variable DPI. Tesseract on those images returns
near-empty text. Running the same Tesseract on the *same scans* after a
short OpenCV preprocessing chain typically lifts recall by 15-25% in our
target language (Russian printed Cyrillic).

The chain (in order):

  1. RGBA / RGB → grayscale (`cv2.cvtColor`).
  2. Median denoise — removes salt-and-pepper without smearing thin
     glyph strokes (Russian Cyrillic has many of those).
  3. Deskew — minAreaRect over thresholded foreground pixels gives a
     small rotation correction (typically 0–4°). Only applied when the
     detected angle is non-trivial (>= 0.3°), so well-aligned scans pay
     no rotation cost.
  4. Adaptive binarization — Otsu's global threshold; falls back to
     adaptive Gaussian if Otsu produces a near-empty mask (which happens
     when the page is largely uniform).
  5. DPI normalize — upscale 1.5× via cv2 `INTER_CUBIC` when the input
     is small (height < 1500 px ≈ A4 at 180 DPI). Tesseract sees more
     pixels per glyph, the LSTM head needs that for confident decoding.

Pipeline contract:

  * Input: ``PIL.Image.Image`` (mode=any).
  * Output: ``PIL.Image.Image`` mode=L (grayscale binarized).
  * Pure: same input → same output. No global state.
  * Idempotent: feeding the output back through the chain is a no-op
    (already grayscale + binarized, deskew angle near zero).
  * **Never raises**. On any exception inside the chain we log the
    failure and return the input image unchanged — preprocessing is
    best-effort, the OCR call must still get *something*.

Memory budget: holds two ``np.uint8`` arrays of the page size at most.
For a 200 DPI A4 page that is ~2 × 7 MB = 14 MB peak. Within the
existing 512 MB worker headroom.
"""

from __future__ import annotations

import logging
from typing import Optional

from PIL import Image

logger = logging.getLogger(__name__)


# Thresholds picked from manual inspection of batch_3 (archive scans):
#   * 0.3° — below this, rotation noise > correction signal.
#   * 1500 px height — A4 at ~180 DPI. Anything smaller benefits from
#     1.5× upscaling for Tesseract's LSTM head.
_DESKEW_MIN_ANGLE_DEGREES = 0.3
_DESKEW_MAX_ANGLE_DEGREES = 15.0  # safety cap; > 15° is almost certainly junk
_UPSCALE_TARGET_HEIGHT = 1500
_UPSCALE_FACTOR = 1.5


def preprocess_for_ocr(image: Image.Image) -> Image.Image:
    """Preprocess a page image before handing it to Tesseract.

    Best-effort: any failure inside the chain returns the input image
    unchanged so the OCR call always gets *something* to work with.
    """
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        logger.warning(
            "image_preprocessing_disabled_no_opencv",
            extra={"event_type": "image_preprocessing_no_opencv", "error": str(exc)},
        )
        return image

    try:
        arr = _pil_to_array(image)
        steps_applied: list[str] = []

        gray = _to_grayscale(arr, cv2)
        steps_applied.append("grayscale")

        denoised = cv2.medianBlur(gray, 3)
        steps_applied.append("denoise_median3")

        deskewed, angle = _deskew(denoised, cv2, np)
        if angle is not None:
            steps_applied.append(f"deskew_{angle:.2f}deg")

        binarized = _binarize_otsu_with_fallback(deskewed, cv2)
        steps_applied.append("binarize_otsu")

        scaled = _upscale_if_small(binarized, cv2)
        if scaled is not binarized:
            steps_applied.append(f"upscale_{_UPSCALE_FACTOR}x")

        out = Image.fromarray(scaled, mode="L")
        logger.debug(
            "image_preprocessing_done",
            extra={
                "event_type": "image_preprocessing_done",
                "steps": steps_applied,
                "input_size": (image.width, image.height),
                "output_size": (out.width, out.height),
            },
        )
        return out
    except Exception as exc:  # noqa: BLE001 — best-effort path, log and bail
        logger.warning(
            "image_preprocessing_failed",
            extra={
                "event_type": "image_preprocessing_failed",
                "error": str(exc),
            },
            exc_info=True,
        )
        return image


# ---------------------------------------------------------------------------
# internal


def _pil_to_array(image: Image.Image):
    import numpy as np

    if image.mode == "L":
        return np.asarray(image, dtype="uint8")
    if image.mode in ("RGB", "RGBA", "BGR"):
        return np.asarray(image.convert("RGB"), dtype="uint8")
    return np.asarray(image.convert("RGB"), dtype="uint8")


def _to_grayscale(arr, cv2):
    if arr.ndim == 2:
        return arr
    return cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)


def _deskew(gray, cv2, np) -> tuple:
    """Find skew angle via minAreaRect over thresholded foreground.

    Returns (rotated_image, angle_degrees_or_None). Angle is None when
    skew was below the dead-zone threshold and rotation was skipped.
    """
    # Otsu on the *negated* image gives us a foreground mask (bright
    # pixels = ink). minAreaRect on those is a quick estimator of the
    # dominant text line angle. Doesn't need to be exact — Tesseract
    # tolerates a few tenths of a degree.
    inverted = cv2.bitwise_not(gray)
    _, mask = cv2.threshold(
        inverted, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU
    )
    coords = cv2.findNonZero(mask)
    if coords is None or len(coords) < 50:
        return gray, None

    # cv2.minAreaRect returns angle in (-90, 0]. Normalize to (-45, 45].
    rect = cv2.minAreaRect(coords)
    angle = rect[-1]
    if angle < -45:
        angle = 90 + angle
    if angle > 45:
        angle = angle - 90

    abs_angle = abs(angle)
    if abs_angle < _DESKEW_MIN_ANGLE_DEGREES or abs_angle > _DESKEW_MAX_ANGLE_DEGREES:
        return gray, None

    h, w = gray.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), angle, 1.0)
    rotated = cv2.warpAffine(
        gray,
        matrix,
        (w, h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )
    return rotated, angle


def _binarize_otsu_with_fallback(gray, cv2):
    """Otsu binarization. Fall back to adaptive Gaussian on degenerate input."""
    _, otsu = cv2.threshold(
        gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU
    )
    # When the page has nearly uniform luminance (full-color background,
    # photograph of a paper at low contrast), Otsu's mask collapses to
    # mostly-black or mostly-white. Detect that and fall back to local
    # adaptive thresholding which preserves text against gradient
    # backgrounds.
    foreground_ratio = _foreground_ratio(otsu)
    if foreground_ratio < 0.005 or foreground_ratio > 0.6:
        return cv2.adaptiveThreshold(
            gray,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            blockSize=31,
            C=10,
        )
    return otsu


def _foreground_ratio(binary) -> float:
    """Fraction of pixels considered ink (== 0 in BINARY-thresholded image)."""
    import numpy as np

    if binary.size == 0:
        return 0.0
    # cv2 BINARY puts ink at 0, background at 255 (we inverted earlier).
    return float(np.count_nonzero(binary == 0)) / float(binary.size)


def _upscale_if_small(image, cv2):
    h, w = image.shape[:2]
    if h >= _UPSCALE_TARGET_HEIGHT:
        return image
    new_size = (int(w * _UPSCALE_FACTOR), int(h * _UPSCALE_FACTOR))
    return cv2.resize(image, new_size, interpolation=cv2.INTER_CUBIC)
