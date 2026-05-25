"""PolicyholderNameParser — extract the contracting party's name.

КАСКО polises name the policyholder via a labeled block, either inline
or as a table cell:

    Страхователь: ООО "Ромашка"
    ИНН 7707012345 ...

    Страхователь: Иванов Иван Иванович
    Дата рождения: ...

The parser returns the literal text as written. No Title-Case
normalization, no ФИО splitting — those are caller decisions; the
authoritative source-of-truth form depends on the integration.

Strategy, in descending strength:

1. **Table-cell match** via ``ctx.tables`` — left-cell starts with
   ``Страхователь``, right cell is the name. Strongest path:
   pdfplumber gives us the label cell directly, no boundary heuristics.
2. **Anchored text match** — ``Страхователь:`` prefix; capture from
   the anchor to the next labeled subfield (``ИНН``, ``Адрес`` …),
   end-of-line, or end-of-block (see ``policyholder_block.py``).

No bare-ФИО fallback in PR #2 — too noisy without an anchor; the
type-classifier handles bare-ФИО as a hint, not as identity.
"""

from __future__ import annotations

import re
from typing import List

from ..candidates import Candidate, ConfidenceComponents
from ..policyholder_block import (
    locate_policyholder_block,
    policyholder_table_rows,
)
from .base import ExtractionContext, FieldParser


_NAME_LABEL_RE = re.compile(
    r"^\s*(?:Страхователь|СТРАХОВАТЕЛЬ)\s*[:\-—–]?\s*$",
)

# Form-field labels that masquerade as values — when the captured text
# IS one of these (case-insensitive, possibly with a trailing colon),
# it's actually the label for the value on the next line. Common in
# СОГАЗ/SGZA and ВСК two-column polises where pdfplumber emits:
#     Страхователь:
#     Наименование
#     ООО "Альфа"
# We skip the "Наименование" line and continue capture from the next
# non-empty line.
_LABEL_VALUE_RE = re.compile(
    r"^\s*(?:"
    r"Наименование(?:\s+организации)?"
    r"|Полное\s+наименование"
    r"|Сокращ[её]нное\s+наименование"
    r"|ФИО(?:\s+гражданина)?"
    r"|Фамилия\s*,\s*Имя\s*,\s*Отчество"
    r"|Юридический"
    r"|наименование\s+ИП,\s+юр\.?\s*лица"
    r"|Имя"
    r")\s*[:\-—–]?\s*$",
    re.IGNORECASE,
)

# Labels that mark the END of the name capture within the block.
# Keep deliberately short — false positives here truncate the name.
#
# Each label is anchored with ``\b`` at BOTH ends. Without the trailing
# ``\b``, case-insensitive search would happily match "Тел" inside
# "СТРОИТЕЛЬНОЕ", chopping a captured legal name mid-word (`"СПЕЦИА-
# ЛИЗИРОВАННОЕ СТРОИ` — batch_5 regression).
_NAME_STOP_RE = re.compile(
    r"\s*(?:"
    r"\bИНН\b|\bКПП\b|\bОГРН(?:ИП)?\b|"
    r"\bАдрес\b|\bМесто\s+жительства\b|\bМесто\s+нахождения\b|\bЗарегистр|"
    r"\bТел(?:ефон)?\b|\bE-?mail\b|\bЭл\.?\s*почта\b|\bПочта\s*:|"
    r"\bПаспорт\b|\bДата\s+рождения\b|\bг\.р\.|"
    r"\bКонтактн"
    r")",
    re.IGNORECASE,
)

# Three Cyrillic words / two-word Surname-First / surname + initials.
# Used only to BOOST confidence on captured anchor text; never as a
# capture pattern itself.
_FIO_SHAPE_RE = re.compile(
    r"\b[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+)?\b"
)
_FIO_INITIALS_SHAPE_RE = re.compile(
    r"\b[А-ЯЁ][а-яё]+\s+[А-ЯЁ]\.\s*[А-ЯЁ]\."
)

# Strong "this is a legal entity" hint inside the captured name.
_ORG_PREFIX_RE = re.compile(
    r"\b(?:ООО|ОАО|АО|ПАО|ЗАО|ИП|НКО|АНО|ТСЖ|ТСН|МУП|ГУП|ФГУП|"
    r"Общество\s+с\s+ограниченной)\b",
    re.IGNORECASE,
)


# Patterns that strongly indicate the captured text is NOT a name. Used
# as a reject filter on candidate captures.
#
# Bank details — happens on lizinging layouts where pdfplumber column
# flattening puts the actual name on a different page and the line
# immediately after the anchor is "р/с 40701810500160000472, БАНК
# ВТБ(ПАО), к/с …". Without this reject the captured "name" was
# "р/с 40701…".
#
# Numbered contract clauses — happens when "Страхователь:" is at end
# of one section and the next non-empty line is "10.2. Выплата по
# риску …". The strict-anchor correctly picks the labeled position;
# this reject prevents the contract clause from landing as the name.
_NAME_REJECT_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"р/с|к/с|БИК|БАНК\b|кор\.?\s*счет|расчетный\s+счет|"
    r"\d+\.\d+(?:\.\d+)?\.?\s+|"  # "10.2 ", "10.2. ", "12.1.1. " — clause
    r"\d+\)\s+|"                  # "1) ", "10) " — enumerated clause
    # Signature / footer / form debris captured when the labeled
    # anchor's content area is empty and the next "non-empty" line is
    # a closing-block leftover.
    r"Подпись\b|"
    r"Идентификатор\s+документа\b|"
    r"Уполномоченный\s+представи|"  # "...тель Страховщика"
    r"М\.\s*П\.|"                   # "М. П." stamp marker
    r"Инициалы\s*,\s*Фамилия|"
    r"подпись\s+Ф\.И\.О\.|"
    r"No\s*\d{4,}[-/]|"             # "No 2037207-1036257/24" — policy ID
    r"«\s*\d{1,2}\s*»\s+\w+\s+\d{4}|"  # "«16» апреля 2024 г." — date
    r"/\s+|"                        # "/ Губин Ю.И." — signature leadin
    r"места\s+нахождения|"          # "места нахождения 180016..." stranded label
    r"места\s+жительства|"
    r"юридического\s+лица|"
    r"по\s+месту\s+жительства|"
    # Disclaimer / regulatory boilerplate captured when the anchor
    # word "Страхователь" appears in a long sentence and our prose-
    # suffix-detection in the block locator picked the wrong anchor.
    r"Информация,\s+указанная\s+в\s+Полисе"
    r")",
    re.IGNORECASE,
)

# Substrings that, if they appear ANYWHERE inside the captured value,
# mark it as disclaimer/regulatory boilerplate rather than a name.
# These complement the prefix-reject above for cases where the bad
# capture starts with a name-like word but the rest is prose.
_NAME_REJECT_SUBSTRING_RE = re.compile(
    r"подтверждает,?\s+что\s+Правила"
    r"|проинформирован\s+об\s+условиях"
    r"|проверена\s+и\s+подтверждается"
    r"|условия\s+Правил\s+страхования\s+разъяснены",
    re.IGNORECASE,
)


def _looks_like_name(s: str) -> bool:
    """A real name has letters; bare digit / punctuation strings don't.

    Rejects: bank-details headers, numbered contract clauses, signature
    / footer debris, dates, policy-ID stand-ins, and disclaimer prose.
    See ``_NAME_REJECT_PREFIX_RE`` and ``_NAME_REJECT_SUBSTRING_RE``.
    """
    if not s or not any(c.isalpha() for c in s):
        return False
    if _NAME_REJECT_PREFIX_RE.match(s):
        return False
    if _NAME_REJECT_SUBSTRING_RE.search(s):
        return False
    return True


def _strip_trailing_punctuation(s: str) -> str:
    return s.rstrip(" \t,;.:—–-")


# A captured value ending with a pure "form" phrase (no quoted name yet)
# needs continuation onto the next line. Real corpus example
# (batch_5): pdfplumber emits "ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ\n"
# + "\"ИНФОКАР\"" — the actual company name on the next line.
_FORM_WITHOUT_NAME_RE = re.compile(
    r"^(?:"
    r"ОБЩЕСТВО\s+С\s+ОГРАНИЧЕННОЙ(?:\s+ОТВЕТСТВЕННОСТЬЮ)?"
    r"|Общество\s+с\s+ограниченной(?:\s+ответственностью)?"
    r"|(?:Открытое|Закрытое|Публичное)?\s*Акционерное\s+общество"
    r"|(?:Открытое|Закрытое|Публичное)?\s*АКЦИОНЕРНОЕ\s+ОБЩЕСТВО"
    r"|Непубличное\s+акционерное\s+общество"
    r")\s*$",
    re.IGNORECASE,
)

_MAX_CONTINUATION_LINES = 2


def _needs_continuation(value: str) -> bool:
    """True iff the captured name looks truncated mid-construct.

    Two cheap-but-effective signals:
    1. Pure "ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ" / "Акционерное
       общество" etc. without the quoted brand name that should follow.
    2. Unbalanced quote count — opening ``"`` or ``«`` without the
       matching closing mark.
    """
    if not value:
        return False
    if _FORM_WITHOUT_NAME_RE.match(value):
        return True
    if value.count('"') % 2 == 1:
        return True
    if value.count('«') != value.count('»'):
        return True
    return False


class PolicyholderNameParser(FieldParser):
    field_name = "policyholder_name"

    @staticmethod
    def _capture_end(block_text: str, capture_start: int) -> int:
        """Compute the end offset for a name capture from ``capture_start``.

        Stops at the first of: next known subfield label, end of line,
        or end of block.
        """
        stop_match = _NAME_STOP_RE.search(block_text, capture_start)
        eol_pos = block_text.find("\n", capture_start)
        bounds = [len(block_text)]
        if stop_match is not None:
            bounds.append(stop_match.start())
        if eol_pos != -1:
            bounds.append(eol_pos)
        return min(bounds)

    def parse(self, ctx: ExtractionContext) -> List[Candidate]:
        candidates: List[Candidate] = []
        candidates.extend(self._from_tables(ctx))
        candidates.extend(self._from_anchor(ctx))

        if not candidates:
            candidates.append(
                Candidate(
                    value=None,
                    state="not_found",
                    pattern_id="no_pattern_match",
                    source_fragment="",
                )
            )
        return candidates

    # ----- strategies -----

    def _from_tables(self, ctx: ExtractionContext) -> List[Candidate]:
        out: List[Candidate] = []
        for page in ctx.tables or []:
            for table in page or []:
                # Two-stage scan within the policyholder rows of each
                # table. Stage 1 (preferred): label-cell == "Страхователь"
                # — the name is in adjacent cells of the same row.
                # Stage 2 (fallback for РСГ/SGZA/АльфаЛизинг XLS forms):
                # the section header row is "2. СТРАХОВАТЕЛЬ / ЛИЗИНГО-
                # ПОЛУЧАТЕЛЬ:" with no value, and the next row labels
                # "Наименование" with the actual name to its right.
                rows = policyholder_table_rows(table)
                for row in rows:
                    if not row or len(row) < 2:
                        continue
                    label = (row[0] or "").strip()
                    pattern_id = None
                    if _NAME_LABEL_RE.match(label):
                        pattern_id = "table_cell"
                    elif _LABEL_VALUE_RE.match(label):
                        pattern_id = "table_form_label"
                    else:
                        continue
                    value_raw = " ".join(
                        (c or "").strip() for c in row[1:]
                        if (c or "").strip()
                    )
                    # Same stoppers as the in-text capture — XLS form
                    # masks render each form field as its own cell so
                    # ("ООО Альфа", "ИНН 7707…", "РЕЗИДЕНТ РФ", "ДА")
                    # collapse into one value string.
                    stop_match = _NAME_STOP_RE.search(value_raw)
                    if stop_match is not None:
                        value_raw = value_raw[: stop_match.start()]
                    value = _strip_trailing_punctuation(value_raw.strip())
                    if not _looks_like_name(value):
                        continue
                    out.append(
                        Candidate(
                            value=value,
                            state="found",
                            pattern_id=pattern_id,
                            source_fragment=f"{label} | {value}"[:240],
                            components=ConfidenceComponents(
                                pattern_strength=0.7,
                                context_strength=0.3,
                            ),
                        )
                    )
        return out

    def _from_anchor(self, ctx: ExtractionContext) -> List[Candidate]:
        block = locate_policyholder_block(ctx.normalized)
        if block is None:
            return []
        text = ctx.normalized.text
        start, end = block
        block_text = text[start:end]

        # Strip the anchor's trailing punctuation/whitespace (colon,
        # dash, leading spaces left by the start-anchor's lookahead).
        head = re.match(r"^[\s:\-—–]*", block_text)
        capture_start = head.end() if head else 0

        # Walk up to 3 lines past the anchor to skip label-only lines
        # ("Наименование", "Полное наименование", "Юридический", "ФИО
        # гражданина") that some templates print between the anchor
        # and the actual name. Without this skip, the captured name
        # ends up being the label word itself.
        capture_end = self._capture_end(block_text, capture_start)
        value = _strip_trailing_punctuation(
            block_text[capture_start:capture_end].strip()
        )
        for _ in range(3):
            if value and not _LABEL_VALUE_RE.match(value):
                break
            # value is empty or a known label — advance past the next
            # newline and try again.
            next_eol = block_text.find("\n", capture_start)
            if next_eol == -1:
                break
            capture_start = next_eol + 1
            capture_end = self._capture_end(block_text, capture_start)
            value = _strip_trailing_punctuation(
                block_text[capture_start:capture_end].strip()
            )

        # Multi-line legal-name continuation. If the captured value
        # looks truncated mid-construct ("ОБЩЕСТВО С ОГРАНИЧЕННОЙ
        # ОТВЕТСТВЕННОСТЬЮ" without the quoted brand, or unbalanced
        # quotes), pull the next 1–2 lines into the capture.
        for _ in range(_MAX_CONTINUATION_LINES):
            if not _needs_continuation(value):
                break
            next_eol = block_text.find("\n", capture_end + 1)
            if next_eol == -1:
                next_eol = len(block_text)
            extension = block_text[capture_end + 1 : next_eol]
            ext_stop = _NAME_STOP_RE.search(extension)
            if ext_stop is not None:
                extension = extension[: ext_stop.start()]
            extension = extension.strip()
            if not extension:
                break
            value = _strip_trailing_punctuation(
                (value + " " + extension).strip()
            )
            capture_end = next_eol

        if not _looks_like_name(value):
            return []

        is_org = bool(_ORG_PREFIX_RE.search(value))
        is_fio = bool(
            _FIO_SHAPE_RE.search(value)
            or _FIO_INITIALS_SHAPE_RE.search(value)
        )
        pattern_strength = 0.55
        context_strength = 0.25 if (is_org or is_fio) else 0.10

        span_abs = (start + capture_start, start + capture_end)
        fragment = self.take_fragment(text, span_abs[0], span_abs[1])
        return [
            Candidate(
                value=value,
                state="found",
                pattern_id="anchor_text",
                source_fragment=fragment,
                span=span_abs,
                components=ConfidenceComponents(
                    pattern_strength=pattern_strength,
                    context_strength=context_strength,
                ),
            )
        ]
