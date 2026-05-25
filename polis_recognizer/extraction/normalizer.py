"""Text normalization for the v2 extraction pipeline.

Real PDF text extracts arrive with Unicode noise that makes naïve regex
brittle: NBSP between digits, zero-width joiners, ligatures from font
glyphs, hyphenated word breaks at line ends, mixed dashes, etc. The
normalizer is the single place where every parser downstream gets a
clean input that retains line structure (so layout-aware parsers can
still operate on lines and source fragments still point back at
recognizable text).

NormalizedText also carries a `line_starts` index so any character span
in the normalized string can be mapped back to a line number cheaply —
that's how the LayoutAnalyzer knows which line a candidate matched on.
"""

from __future__ import annotations

import re
import unicodedata
from bisect import bisect_right
from dataclasses import dataclass
from typing import List


# Whitespace-class characters PDF extractors emit that should collapse to
# a regular ASCII space. The NBSP variants in particular routinely sneak
# inside numeric values like "1 000 000,00".
_INVISIBLE_WHITESPACE = {
    " ",  # NBSP
    " ",  # THIN SPACE
    " ",  # NARROW NO-BREAK SPACE
    " ",  # FIGURE SPACE
    " ",  # HAIR SPACE
}

# Zero-width characters that have no business inside extracted policy
# text but show up in some PDFs anyway. Stripped outright.
_ZERO_WIDTH_RE = re.compile(r"[​‌‍﻿]")

# U+00AD SOFT HYPHEN — pdfplumber emits this where the source PDF
# marked an optional line-break ("Прогресс­Тех", "Санкт­Петербург",
# "пр­кт", "e­mail"). Replaced with a regular ASCII hyphen so the
# normalised text matches the rendered form ("Прогресс-Тех"); the
# hyphenated-line-break healer further down then joins SHY-broken
# words that wrapped to a new line.
_SOFT_HYPHEN_RE = re.compile(r"­")

# Hyphenated line-break healing: "пе-\nриод" → "период". Conservative —
# only triggers when both sides are word characters.
_HYPHEN_LINEBREAK_RE = re.compile(r"(\w)-\n(\w)")

# Tabs are pretty rare but pdf2text can emit them when columns align —
# normalize to a single space.
_TAB_RE = re.compile(r"\t")

# Collapse runs of regular spaces (not newlines) to a single space.
_SPACE_RUN_RE = re.compile(r"[ ]{2,}")

# Trim trailing spaces on every line.
_TRAILING_SPACES_RE = re.compile(r" +\n")

# More than two blank lines is purely decorative — collapse to two.
_MULTI_BLANK_RE = re.compile(r"\n{3,}")


@dataclass
class NormalizedText:
    """Normalized text with a line index for span↔line mapping.

    Attributes:
        text: The normalized string. Parsers run regex against this.
        lines: ``text.split("\\n")`` — convenience list, kept in sync.
        line_starts: Character offset of each line in ``text``. Used by
            ``line_for_offset`` to translate a regex span back to the
            line that contained it.
    """

    text: str
    lines: List[str]
    line_starts: List[int]

    def line_for_offset(self, offset: int) -> int:
        """Return 0-based line index that contains ``offset``."""
        if not self.line_starts:
            return 0
        # bisect_right returns the position of the FIRST line that starts
        # AFTER `offset`; subtract 1 to get the line containing it.
        idx = bisect_right(self.line_starts, offset) - 1
        if idx < 0:
            idx = 0
        return idx

    def line_window(self, line_no: int, before: int = 0, after: int = 0) -> str:
        """Return joined lines [line_no-before .. line_no+after] inclusive."""
        lo = max(0, line_no - before)
        hi = min(len(self.lines), line_no + after + 1)
        return "\n".join(self.lines[lo:hi])


class TextNormalizer:
    """Normalize raw extracted text for downstream parsers.

    The normalizer is intentionally conservative — it never removes data
    that a downstream parser might need. Heuristics applied:

    1. Unicode NFKC normalization — collapses ligatures and compatibility
       glyphs that some PDF fonts emit.
    2. Zero-width characters removed.
    3. Invisible whitespace (NBSP family) → ASCII space.
    4. Tabs → single space.
    5. Hyphenated line-break healing for plain word characters.
    6. Trailing spaces per line stripped.
    7. Runs of 3+ blank lines collapsed to 2.
    8. Runs of 2+ ASCII spaces collapsed to 1 (after NBSP step).

    Newlines are preserved so layout-aware parsers can keep working on
    line structure.
    """

    def normalize(self, raw: str) -> NormalizedText:
        if not raw:
            return NormalizedText(text="", lines=[""], line_starts=[0])

        text = unicodedata.normalize("NFKC", raw)
        text = _ZERO_WIDTH_RE.sub("", text)
        # SHY → ASCII hyphen BEFORE the line-break healer runs, so a
        # SHY-broken word at end-of-line ("Прогресс­\nТех") gets the
        # same treatment as a regular hyphen-broken one ("Прогресс-\nТех").
        text = _SOFT_HYPHEN_RE.sub("-", text)
        for ch in _INVISIBLE_WHITESPACE:
            text = text.replace(ch, " ")
        text = _TAB_RE.sub(" ", text)
        text = _HYPHEN_LINEBREAK_RE.sub(r"\1\2", text)
        text = _SPACE_RUN_RE.sub(" ", text)
        text = _TRAILING_SPACES_RE.sub("\n", text)
        text = _MULTI_BLANK_RE.sub("\n\n", text)

        lines = text.split("\n")
        line_starts: List[int] = []
        offset = 0
        for line in lines:
            line_starts.append(offset)
            offset += len(line) + 1  # +1 for the consumed "\n"

        return NormalizedText(text=text, lines=lines, line_starts=line_starts)
