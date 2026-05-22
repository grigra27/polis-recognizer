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


# Anchor marking the START of the policyholder block.
#
# Russian lizinging КАСКО polises often print a combined label like
# "СТРАХОВАТЕЛЬ / ЛИЗИНГОПОЛУЧАТЕЛЬ:" or
# "СТРАХОВАТЕЛЬ / ВЫГОДОПРИОБРЕТАТЕЛЬ" — both refer to the same
# (single) party. The optional suffix is absorbed by the anchor so
# the block span starts AFTER the full combined label, not in the
# middle of " / ЛИЗИНГОПОЛУЧАТЕЛЬ:" (which used to produce names
# like "/ ЛИЗИНГОПОЛУЧА" — batch_1 inspector).
_BLOCK_START_RE = re.compile(
    r"(?:Страхователь|СТРАХОВАТЕЛЬ)"
    r"(?:\s*/\s*(?:"
    r"Лизингополучатель|ЛИЗИНГОПОЛУЧАТЕЛЬ|"
    r"Выгодоприобретатель|ВЫГОДОПРИОБРЕТАТЕЛЬ|"
    r"Залогодатель|ЗАЛОГОДАТЕЛЬ|"
    r"Грузополучатель|ГРУЗОПОЛУЧАТЕЛЬ"
    r"))?"
    r"(?=[\s:\-—–])",
)

# Section headers that close the block. Short list of high-confidence
# stoppers — every label here is a separate document section, not a
# subfield of the policyholder (those — ИНН, Адрес, Тел — stay inside
# the block and are extracted by their own parsers).
#
# В лизинговых КАСКО следом за блоком страхователя обычно идут разделы
# СОБСТВЕННИК / ЛИЗИНГОДАТЕЛЬ / ВЫГОДОПРИОБРЕТАТЕЛЬ / ОБРЕМЕНЕНИЕ ТС —
# с их собственными ИНН/ОГРН/КПП. Без явных стопперов эти реквизиты
# протекают в `policyholder_*` слотами лизингодателя, не страхователя
# (см. batch_1 inspector — `Договор 763-25-102_БЛ-…`,
# `Печатная форма AC*`).
_BLOCK_END_RE = re.compile(
    r"(?:"
    r"Страховщик|СТРАХОВЩИК|"
    r"Выгодоприобретатель|ВЫГОДОПРИОБРЕТАТЕЛЬ|"
    r"Собственник|СОБСТВЕННИК|"
    r"Лизингодатель|ЛИЗИНГОДАТЕЛЬ|"
    r"Залогодержатель|ЗАЛОГОДЕРЖАТЕЛЬ|"
    r"ОБРЕМЕНЕНИЕ|Обременение|"
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


def _anchor_label_score(text: str, anchor_start: int, anchor_end: int = -1) -> int:
    """Score how "label-like" the position of an anchor is.

    Higher score = more likely to be a real field label, not the word
    "Страхователь" appearing in prose. Used to disambiguate when a
    document contains multiple anchors (e.g. a labeled "Страхователь:"
    field AND a prose sentence like "Страхователь подтверждает, что
    Правила страхования получил…").

    Two independent signals are combined:

    **Prefix** (what precedes the anchor):
        +2 — at the start of a line, or after a numbered list prefix
             like ``1.`` / ``2. ``;
        +1 — within the first ~10 chars of a line;
         0 — deep in a line.

    **Suffix** (what follows the anchor):
        -3 — anchor word is immediately followed by a lowercase
             Cyrillic letter — the classic prose continuation
             ("Страхователь подтверждает", "обязан", "вправе" …).
         0 — followed by ``:``/space/dash, then a capital letter
             (a name) or punctuation like ``/`` — label-like.

    A prose-only sentence at the start of a document gets score ``-3``;
    a labeled field anywhere later wins easily. When every anchor is
    prose, the highest-scored (i.e. least-negative) still wins, so we
    don't accidentally produce ``None``.
    """
    line_start = text.rfind("\n", 0, anchor_start) + 1
    prefix = text[line_start:anchor_start]
    prefix_clean = re.sub(r"^\s*\d+[\.\)]\s*", "", prefix).strip()
    if not prefix_clean:
        prefix_score = 2
    elif len(prefix_clean) <= 10:
        prefix_score = 1
    else:
        prefix_score = 0

    suffix_score = 0
    if anchor_end >= 0:
        suffix = text[anchor_end : anchor_end + 40]
        # Strip the bit of whitespace / colon / dash that legitimately
        # separates a label from its content; whatever's after that is
        # the actual continuation.
        head = re.match(r"^[\s:\-—–]*", suffix)
        after_punct = suffix[head.end() :] if head else suffix
        if after_punct:
            first = after_punct[0]
            # Lowercase Cyrillic letter → prose verb in 99% of cases.
            if first.isalpha() and first.islower():
                suffix_score = -3

    return prefix_score + suffix_score


def locate_policyholder_block(
    normalized: NormalizedText,
) -> Optional[Tuple[int, int]]:
    """Return ``(start, end)`` char span of the policyholder block.

    ``start`` is the first char AFTER the ``Страхователь`` anchor word
    (the colon/dash that usually follows is intentionally left inside
    the span — sub-parsers strip it). ``end`` is the position of the
    next section stopper, or ``start + _MAX_BLOCK_CHARS``, or the end
    of text — whichever comes first.

    Returns ``None`` when no anchor is present. When multiple anchors
    are present, prefers labeled positions (start of line, after ``1.``)
    over prose matches like "Страхователь подтверждает, что…". The
    first highest-scored anchor wins.
    """
    text = normalized.text
    matches = list(_BLOCK_START_RE.finditer(text))
    if not matches:
        return None
    best = max(
        matches,
        key=lambda m: (
            _anchor_label_score(text, m.start(), m.end()),
            -m.start(),
        ),
    )
    start = best.end()
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

# Labels of OTHER parties that close the policyholder rows range
# within a table. A subsequent row starting with one of these belongs
# to a different party (broker, insurer, lizingodatel, …) and must
# NOT contribute identifiers or contacts to the policyholder slot.
#
# Real corpus example (batch_1): a single pdfplumber table carries
# rows ["Брокер", "ООО ..."] / ["Адрес", "Люберцы …"] /
# ["Email", "online@on-linebroker.ru"] BEFORE the
# ["Страхователь", "<actual name>"] row. Without row-grouping,
# `online@on-linebroker.ru` lands in `policyholder_contacts.emails`.
_TABLE_OTHER_PARTY_RE = re.compile(
    r"^\s*(?:"
    r"Брокер|БРОКЕР|"
    r"Страховой\s+брокер|СТРАХОВОЙ\s+БРОКЕР|"
    r"Страховщик|СТРАХОВЩИК|"
    r"Выгодоприобретатель|ВЫГОДОПРИОБРЕТАТЕЛЬ|"
    r"Собственник|СОБСТВЕННИК|"
    r"Лизингодатель|ЛИЗИНГОДАТЕЛЬ|"
    r"Залогодержатель|ЗАЛОГОДЕРЖАТЕЛЬ|"
    r"Контактное\s+лицо|КОНТАКТНОЕ\s+ЛИЦО|"
    r"Представитель\b|ПРЕДСТАВИТЕЛЬ\b"
    r")",
    re.IGNORECASE,
)


def table_has_policyholder_anchor(table) -> bool:
    """True iff one of the table's cells is a "Страхователь" label.

    Used by per-subfield parsers as a quick precondition. For more
    precise row-level scoping (excluding rows that belong to other
    parties in the same table), use :func:`policyholder_table_rows`
    instead.
    """
    for row in table or []:
        for cell in row or []:
            if cell and _TABLE_STRAKH_LABEL_RE.match(cell):
                return True
    return False


def policyholder_table_rows(table):
    """Return the slice of rows in ``table`` belonging to the policyholder.

    Begins at the row containing a "Страхователь" label cell. Ends
    just before the next row whose first cell labels a different party
    (Брокер / Страховщик / Выгодоприобретатель / Собственник /
    Лизингодатель / Залогодержатель / Контактное лицо / Представитель)
    or at end of table. Returns ``[]`` when no Страхователь row exists.

    This is the precise alternative to
    :func:`table_has_policyholder_anchor` + iterate-all-rows that
    parsers should use when scanning for sub-field values — without
    it, broker / insurer rows in the same table leak into the
    policyholder slot.
    """
    if not table:
        return []
    start = None
    for i, row in enumerate(table):
        for cell in row or []:
            if cell and _TABLE_STRAKH_LABEL_RE.match(cell):
                start = i
                break
        if start is not None:
            break
    if start is None:
        return []
    end = len(table)
    for i in range(start + 1, len(table)):
        row = table[i] or []
        if not row:
            continue
        first_cell = (row[0] or "").strip() if row else ""
        if first_cell and _TABLE_OTHER_PARTY_RE.match(first_cell):
            end = i
            break
    return table[start:end]
