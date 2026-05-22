"""Locate the policyholder block within normalized policy text.

The "policyholder block" is the contiguous text segment that names the
contracting party and (typically) carries their requisites — INN,
address, contact phone. Every policyholder-* parser (name, type, INN,
phones, emails, address, postal code) narrows its search to this block
so it doesn't catch the insurer's data (signature block) or the
beneficiary / insured (which are separate parties).

This module is a single helper because every consumer needs the same
block boundaries — encoding them in one place keeps behaviour
consistent and the heuristics tunable in one spot.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

from .normalizer import NormalizedText


# Anchor marking the START of the policyholder block. The label is
# usually followed by a colon, dash, or end-of-line; the trailing
# character class accepts all three plus space so the captured span
# starts right after the anchor word.
_BLOCK_START_RE = re.compile(
    r"(?:Страхователь|СТРАХОВАТЕЛЬ)(?=[\s:\-—–])",
)

# Section headers that close the block. Short list of high-confidence
# stoppers — every label here is a separate document section, not a
# subfield of the policyholder (those — ИНН, Адрес, Тел — stay inside
# the block and are extracted by their own parsers).
_BLOCK_END_RE = re.compile(
    r"(?:"
    r"Страховщик|СТРАХОВЩИК|"
    r"Выгодоприобретатель|ВЫГОДОПРИОБРЕТАТЕЛЬ|"
    r"Застрахован(?:ный|ное|ные)|"
    r"Объект\s+страхования|"
    r"Транспортное\s+средство|"
    r"Сведения\s+о\s+ТС|"
    r"Страховая\s+сумма|"
    r"Срок\s+страхования|"
    r"Условия\s+страхования|"
    r"Страховая\s+премия|"
    r"Подпись"
    r")"
)

# Fallback cap when no stopper fires. ~30 dense lines of КАСКО text;
# beyond that the capture almost certainly bleeds into unrelated
# sections.
_MAX_BLOCK_CHARS = 1500


def locate_policyholder_block(
    normalized: NormalizedText,
) -> Optional[Tuple[int, int]]:
    """Return ``(start, end)`` char span of the policyholder block.

    ``start`` is the first char AFTER the ``Страхователь`` anchor word
    (the colon/dash that usually follows is intentionally left inside
    the span — sub-parsers strip it). ``end`` is the position of the
    next section stopper, or ``start + _MAX_BLOCK_CHARS``, or the end
    of text — whichever comes first.

    Returns ``None`` when no anchor is present. The first anchor wins
    if there are several; multi-block documents are out of scope.
    """
    text = normalized.text
    start_match = _BLOCK_START_RE.search(text)
    if start_match is None:
        return None
    start = start_match.end()
    tail = text[start : start + _MAX_BLOCK_CHARS]
    end_match = _BLOCK_END_RE.search(tail)
    if end_match is not None:
        end = start + end_match.start()
    else:
        end = min(start + _MAX_BLOCK_CHARS, len(text))
    return (start, end)


def policyholder_block_text(normalized: NormalizedText) -> Optional[str]:
    """Convenience: return the text inside the policyholder block.

    Returns ``None`` when the block could not be located.
    """
    span = locate_policyholder_block(normalized)
    if span is None:
        return None
    return normalized.text[span[0] : span[1]]


_TABLE_STRAKH_LABEL_RE = re.compile(
    r"^\s*(?:Страхователь|СТРАХОВАТЕЛЬ)\b"
)


def table_has_policyholder_anchor(table) -> bool:
    """True iff one of the table's cells is a "Страхователь" label.

    Used by per-subfield parsers (INN, OGRN, KPP, phones, emails, …)
    as a precondition for scanning a table: without a policyholder
    anchor anywhere in the table, we cannot tell whose subfields the
    rows belong to and the table is skipped.
    """
    for row in table or []:
        for cell in row or []:
            if cell and _TABLE_STRAKH_LABEL_RE.match(cell):
                return True
    return False
