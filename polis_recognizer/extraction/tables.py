"""Helpers for reading pdfplumber-extracted tables.

pdfplumber returns each table as a list of rows; each row a list of
cell strings (or None for empty cells). The cells are split on the
finest column boundary the visual layout suggests, so a single
logical column can be spread across many cells with the data in just
one of them and the rest as ``None`` / empty strings.

The functions here turn that fine-grained per-cell view into the
coarse "what numbers does this row carry?" view the field parsers
actually want.
"""

from __future__ import annotations

import re
from typing import Iterable, List, Optional

from .numeric import parse_numeric


# Match a number with thousands-grouping spaces and optional decimal,
# e.g. "5 525 000.00", "12 060 000,00", "0,00", "256878.00".
# Used as a whole-cell match — the cell must contain ONLY the number
# (with maybe leading/trailing whitespace), not a number embedded in
# prose. That keeps row-header text like "СТРАХОВАЯ СУММА" from being
# mistaken for a numeric value.
_PURE_NUMBER_RE = re.compile(
    r"^\s*\d[\d\s]*(?:[.,]\d{1,2})?\s*$",
)

# Placeholder values that mean "no franchise" in АльфаСтрахование
# forms (and a few other insurers).
_ABSENT_PLACEHOLDERS = (
    "не установлена",
    "не установлен",
    "не установлено",
    "—",
    "–",
    "-",
)


def _cell_kind(cell: Optional[str]) -> str:
    """Classify a table cell as ``"number"``, ``"absent"`` or ``"text"``.

    Empty / whitespace-only cells return ``"text"`` (they're skipped by
    callers).
    """
    if cell is None:
        return "text"
    stripped = cell.strip()
    if not stripped:
        return "text"
    if _PURE_NUMBER_RE.match(stripped):
        return "number"
    low = stripped.lower()
    for marker in _ABSENT_PLACEHOLDERS:
        if low == marker or low.startswith(marker):
            return "absent"
    return "text"


def _row_signal_cells(row: Iterable[Optional[str]]) -> List[tuple[str, str]]:
    """Return a list of ``(kind, raw_value)`` for every non-text cell in row order.

    Text cells (labels, headers, empty cells) are dropped; numbers and
    absent-placeholders survive in left-to-right order. This is the
    coarse signal-only view of the row that field parsers consume.
    """
    out: List[tuple[str, str]] = []
    for cell in row:
        kind = _cell_kind(cell)
        if kind == "text":
            continue
        out.append((kind, cell.strip() if cell else ""))
    return out


def _row_label(row: Iterable[Optional[str]]) -> str:
    """Concatenate all text cells in a row into one lowercase string.

    Used as a quick "does this row mention КАСКО ПОЛНОЕ" check.
    """
    parts = []
    for cell in row:
        if cell is None:
            continue
        s = cell.strip()
        if s:
            parts.append(s)
    return " ".join(parts).lower()


def find_kasko_polnoe_row_signals(
    tables: List[List[List[List[Optional[str]]]]],
) -> Optional[List[tuple[str, str]]]:
    """Find the first table row whose label contains "КАСКО ПОЛНОЕ".

    Returns the row's signal cells (numbers + absent-placeholders, in
    order), or ``None`` if no such row exists. Caller decides which
    column maps to limit/franchise/premium — the canonical Альфа
    layout is sum / franchise / premium, so callers typically take
    indices 0, 1 and 2 of the returned list.

    ``tables`` follows the ExtractedTextResult shape: per-page list,
    each page a list of tables, each table a list of rows.
    """
    for page_tables in tables or []:
        for table in page_tables or []:
            for row in table or []:
                if "каско полное" not in _row_label(row):
                    continue
                signals = _row_signal_cells(row)
                if signals:
                    return signals
    return None


def parse_signal_value(raw: str) -> Optional[float]:
    """Parse a numeric signal cell into a float. Returns ``None`` on failure."""
    return parse_numeric(raw)
