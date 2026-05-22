"""Russian-aware date parsing for the v2 extraction pipeline.

Returns ISO-8601 date strings (``YYYY-MM-DD``) — the canonical exchange
format used inside the pipeline. Conversion to ``datetime.date`` happens
at the public API boundary (see ``PolicyExtractor._build_extracted_policy``
and the ``_to_date`` helper). Keeping ISO strings inside the pipeline
makes pipeline candidates serialisable as-is.

Supports two input shapes seen on Russian insurance documents:

- Numeric:   ``DD.MM.YYYY`` or ``DD.MM.YY`` (2-digit year treated as
  20xx — Russian insurance abbreviated form).
- Textual:   ``DD <month-word> YYYY`` — Russian month names in any
  genitive form (``апреля``, ``мая``, ``июля``, …).

OCR debris (guillemets, underscores, dashes) inside the input is
stripped before parsing, so "« 25» марта _ 2025" parses cleanly.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional


_RU_MONTH_BY_STEM = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4,
    "ма": 5, "июн": 6, "июл": 7, "август": 8,
    "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}


def parse_russian_date(date_str: str) -> Optional[str]:
    """Parse a Russian-format date and return ISO ``YYYY-MM-DD``.

    Returns ``None`` on unrecognised input. The function never raises.
    """
    s = (date_str or "").strip()
    if not s:
        return None
    # Strip guillemets, quotes, underscores, dashes left in by OCR.
    s = re.sub(r"[«»\"'_\-—–]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    for fmt in ("%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    parts = s.split()
    if len(parts) == 3 and parts[0].isdigit() and parts[2].isdigit():
        day, month_word, year = parts
        mw = month_word.lower()
        for stem, month_num in _RU_MONTH_BY_STEM.items():
            if mw.startswith(stem):
                try:
                    return datetime(
                        int(year), month_num, int(day)
                    ).strftime("%Y-%m-%d")
                except ValueError:
                    return None
    return None
