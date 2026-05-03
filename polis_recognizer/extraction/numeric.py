"""Shared numeric/currency helpers.

Used by both LayoutAnalyzer (to detect numeric tokens) and individual
field parsers (to convert captured strings into floats). Centralised so
exotic input formats — Russian decimal commas, NBSP-grouped thousands,
mixed currency suffixes — get exactly one place to be handled.
"""

from __future__ import annotations

import re
from typing import Optional


# Numeric token: an integer with optional space/NBSP-grouped thousands
# and optional decimal portion using either "," or ".". Two-decimal forms
# (",00") are most common; we accept 1-2 decimals to be flexible.
NUMERIC_TOKEN_RE = re.compile(r"\d[\d ]*(?:[.,]\d{1,2})?")

CURRENCY_TOKEN_RE = re.compile(
    # Order matters: longer tokens first so the regex engine doesn't
    # short-circuit "руб" before checking "рублей". \b on the bare
    # "руб"/"rub" prevents matching prefixes of longer words.
    # ``RUR`` is the legacy ISO-4217 code Ингосстрах still prints in
    # their KASKO polises (replaced by RUB in 1998 but the templates
    # never updated); accepting it is harmless because RUR maps 1:1
    # to today's RUB.
    r"(?:рублей|руб\.|руб\b|₽|RUB|RUR|USD|долл\.?(?:\s+США)?|EUR|евро)",
    re.IGNORECASE,
)


_CURRENCY_MAP = {
    "руб": "RUB",
    "руб.": "RUB",
    "рубл": "RUB",
    "рублей": "RUB",
    "₽": "RUB",
    "rub": "RUB",
    "rur": "RUB",  # legacy code, still printed by Ингосстрах
    "usd": "USD",
    "долл": "USD",
    "долл.": "USD",
    "долл. сша": "USD",
    "долл сша": "USD",
    "eur": "EUR",
    "евро": "EUR",
}


# Confusable letters Tesseract sometimes emits in place of digits on
# noisy scans (Cyrillic О for 0, capital З for 3, lowercase l for 1
# etc.). Translating them back recovers numeric values like "1О00 000"
# (Cyrillic О, not zero) → 1000000. Lowercase + uppercase variants
# both included; map covers digits 0/1/3/5/6/8 where confusion is
# common in printed Russian-language scans.
_CONFUSABLE_DIGITS = str.maketrans({
    "О": "0", "о": "0", "O": "0", "o": "0",
    "I": "1", "l": "1", "|": "1",
    "З": "3", "з": "3",
    "S": "5", "s": "5",
    "Б": "6",
    "В": "8",
})


def parse_numeric(raw: str) -> Optional[float]:
    """Parse a Russian-style numeric token to float. Returns None on garbage.

    Supports:
        "10 000"        → 10000.0
        "1 000 000,50"  → 1000000.5  (Russian decimal comma)
        "0,00"          → 0.0
        "5000"          → 5000.0
        "  5 000 "      → 5000.0  (NBSP / extra whitespace stripped upstream)
        "1О00 000"      → 1000000.0 (Cyrillic О → 0 confusable)
        "1З 000"        → 13000.0   (Cyrillic З → 3 confusable)

    Mixed comma + dot forms ("1,234.56") are intentionally rejected — the
    Russian convention is one or the other, not both.
    """
    if raw is None:
        return None
    s = str(raw).strip().replace(" ", "").replace(" ", "")
    if not s:
        return None
    # Normalize confusable letters to their digit lookalikes BEFORE the
    # comma → dot pass so that e.g. "1О,5" and "1О.5" both work.
    s = s.translate(_CONFUSABLE_DIGITS)
    if "," in s and "." not in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def normalize_currency(raw: Optional[str]) -> Optional[str]:
    """Normalize a currency-suffix capture to RUB / USD / EUR."""
    if not raw:
        return None
    key = str(raw).strip().lower().rstrip(".")
    return _CURRENCY_MAP.get(key) or _CURRENCY_MAP.get(key + ".") or None
