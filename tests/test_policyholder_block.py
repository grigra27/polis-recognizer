"""Tests for the policyholder block locator.

Every policyholder-* parser narrows its search to this block, so the
locator is in the hot path. Boundary heuristics are deliberately
conservative; these tests guard them.
"""

from __future__ import annotations

from polis_recognizer.extraction.normalizer import TextNormalizer
from polis_recognizer.extraction.policyholder_block import (
    locate_policyholder_block,
    policyholder_block_text,
)


def _normalize(text: str):
    return TextNormalizer().normalize(text)


class TestLocate:
    def test_returns_none_when_no_anchor(self):
        n = _normalize("Договор страхования транспортного средства\n")
        assert locate_policyholder_block(n) is None

    def test_finds_block_starting_after_anchor(self):
        text = "Страхователь: ООО Ромашка\nИНН 7707083893\n"
        n = _normalize(text)
        span = locate_policyholder_block(n)
        assert span is not None
        start, end = span
        # The captured text must start with what follows the anchor
        # (colon, space, ООО...). The anchor itself is left out.
        block = n.text[start:end]
        assert block.lstrip(": ").startswith("ООО")

    def test_stops_at_next_section_header(self):
        text = (
            "Страхователь: ООО Альфа\n"
            "ИНН 7707083893\n"
            "Страховщик: ПАО СК Гамма\n"
            "ОГРН 1027700132195\n"
        )
        n = _normalize(text)
        span = locate_policyholder_block(n)
        assert span is not None
        block = n.text[span[0] : span[1]]
        assert "ООО Альфа" in block
        assert "ИНН" in block  # subfield stays inside
        assert "Страховщик" not in block  # section stopper excluded
        assert "ПАО СК Гамма" not in block

    def test_stops_at_vygodopriobretatel(self):
        text = (
            "Страхователь: ООО Альфа\n"
            "ИНН 7707083893\n"
            "Выгодоприобретатель: ПАО Сбербанк\n"
        )
        n = _normalize(text)
        block = policyholder_block_text(n)
        assert block is not None
        assert "ПАО Сбербанк" not in block

    def test_uppercase_anchor(self):
        text = "СТРАХОВАТЕЛЬ: ООО Альфа\nИНН 7707083893\n"
        n = _normalize(text)
        assert locate_policyholder_block(n) is not None

    def test_caps_block_length_when_no_stopper(self):
        text = "Страхователь: " + ("x" * 5000)
        n = _normalize(text)
        span = locate_policyholder_block(n)
        assert span is not None
        # ~1500 char cap; should not run to end of the 5000-char tail.
        assert (span[1] - span[0]) <= 1600


class TestBlockText:
    def test_returns_none_when_no_anchor(self):
        n = _normalize("ничего здесь не написано про страхователя")
        assert policyholder_block_text(n) is None

    def test_returns_block_text_when_anchor_present(self):
        n = _normalize("Страхователь: ИП Петров П. П.\n")
        block = policyholder_block_text(n)
        assert block is not None
        assert "ИП Петров" in block
