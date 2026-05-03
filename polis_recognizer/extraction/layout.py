"""Layout analysis: detect risk-row table structure inside normalized text.

КАСКО polises typically contain a table such as

    Страховые риски   Страховая сумма   Страховая премия
    Автокаско (Ущерб и Угон)  2000000,00    41245,50
    Гражданская ответственность (ГО)  1000000,00  1900,00
    ИТОГО:                                 43145,50

After PDF text-extraction the rows arrive as plain lines with column
values separated by spaces. The LayoutAnalyzer treats those lines as
records: a label prefix followed by N numeric columns. The output is
``TableRow`` objects which downstream parsers (limit, premium) consume
without re-running their own column heuristics.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .normalizer import NormalizedText
from .numeric import NUMERIC_TOKEN_RE, parse_numeric


# A "numeric column" is a token that LOOKS like a money amount: at least
# one digit, optionally with thousands grouping and a decimal portion.
# We require ≥ 2 digits to avoid matching footnote numerals like "1)".
_NUMERIC_COLUMN_RE = re.compile(r"\d[\d ]{0,12}[.,]\d{2}|\d{2,}[\d ]*")


@dataclass
class TableRow:
    """A line that looks like ``<label>  <num>  <num> [...]``."""

    line_no: int
    line_text: str
    label: str
    columns: List[Tuple[float, str]]  # (parsed_value, raw_token)


@dataclass
class TableHeader:
    """A header row classifying numeric columns by purpose.

    For risk-row tables the header is something like:
        "Страховые риски  Страховая сумма  Страховая премия"
    We use it to label columns so a parser can ask for the "sum_insured"
    column instead of guessing by index.
    """

    line_no: int
    line_text: str
    column_kinds: List[str]  # e.g. ["sum_insured", "premium"]


# Header tokens that classify a numeric column.
_HEADER_KIND_PATTERNS = [
    ("sum_insured", re.compile(r"страховая\s+сумма", re.IGNORECASE)),
    ("premium", re.compile(r"страховая\s+премия", re.IGNORECASE)),
]


class LayoutAnalyzer:
    """Detect risk-row table structure in NormalizedText."""

    # Min/max length for a label prefix on a risk-row line. Short
    # captions ("ИТОГО:") and very long sentences ("При наступлении
    # страхового случая ...") are excluded.
    LABEL_MIN_CHARS = 3
    LABEL_MAX_CHARS = 80

    def find_rows(
        self,
        normalized: NormalizedText,
        label_pattern: re.Pattern,
    ) -> List[TableRow]:
        """Return TableRows whose label matches ``label_pattern``.

        A line is considered a row when it starts with a label that
        matches the pattern AND contains at least one numeric column
        AFTER the label. The first numeric column index is taken as the
        end of the label.
        """
        rows: List[TableRow] = []
        for line_no, line in enumerate(normalized.lines):
            stripped = line.strip()
            if not stripped:
                continue
            label_match = label_pattern.match(stripped)
            if label_match is None:
                continue

            label_end = label_match.end()
            tail = stripped[label_end:]
            # Optional parenthesised qualifier directly after the label
            # ("(Ущерб и Угон)"). Roll the label_end forward past it so
            # the numeric scan starts on the actual value column.
            paren_match = re.match(r"\s*\([^)]{0,80}\)", tail)
            if paren_match:
                label_end += paren_match.end()

            label_text = stripped[:label_end].strip()
            if not (self.LABEL_MIN_CHARS <= len(label_text) <= self.LABEL_MAX_CHARS):
                continue

            columns = self._extract_numeric_columns(stripped[label_end:])
            if not columns:
                continue

            rows.append(
                TableRow(
                    line_no=line_no,
                    line_text=stripped,
                    label=label_text,
                    columns=columns,
                )
            )
        return rows

    def find_header(
        self,
        normalized: NormalizedText,
        near_line: int,
        lookback: int = 4,
    ) -> Optional[TableHeader]:
        """Find a column-header row within ``lookback`` lines above ``near_line``.

        Used to classify the columns of a numeric table so the caller can
        ask for `column_kinds.index("sum_insured")` instead of guessing.
        """
        lo = max(0, near_line - lookback)
        for candidate_line_no in range(near_line - 1, lo - 1, -1):
            line = normalized.lines[candidate_line_no].strip()
            if not line:
                continue
            kinds = []
            seen_kind = set()
            for kind, pattern in _HEADER_KIND_PATTERNS:
                for _ in pattern.finditer(line):
                    if kind in seen_kind:
                        continue
                    kinds.append(kind)
                    seen_kind.add(kind)
            if len(kinds) >= 2:
                return TableHeader(
                    line_no=candidate_line_no,
                    line_text=line,
                    column_kinds=kinds,
                )
        return None

    @staticmethod
    def _extract_numeric_columns(tail: str) -> List[Tuple[float, str]]:
        out: List[Tuple[float, str]] = []
        for match in _NUMERIC_COLUMN_RE.finditer(tail):
            raw = match.group(0).strip()
            value = parse_numeric(raw)
            if value is None:
                continue
            out.append((value, raw))
        return out
