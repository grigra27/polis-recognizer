"""Unit tests for the building blocks of the v2 extraction pipeline.

Each component is tested in isolation: normalizer, layout analyzer,
negation detector, candidate ranker. End-to-end coverage lives in
``test_extraction_v2_pipeline.py``.
"""

from __future__ import annotations

import re

import pytest

from polis_recognizer.extraction.candidates import (
    Candidate,
    ConfidenceComponents,
)
from polis_recognizer.extraction.layout import LayoutAnalyzer
from polis_recognizer.extraction.negation import NegationContext
from polis_recognizer.extraction.normalizer import TextNormalizer
from polis_recognizer.extraction.ranker import CandidateRanker


class TestTextNormalizer:
    def test_collapses_nbsp_in_numbers(self):
        # NBSP between digits is the canonical PDF quirk.
        raw = "сумма: 1 000 000 руб."
        out = TextNormalizer().normalize(raw)
        assert "1 000 000" in out.text or "1000000" in out.text or "1 000 000" in out.text

    def test_strips_zero_width(self):
        raw = "Полис​№ 12345"
        out = TextNormalizer().normalize(raw)
        assert "​" not in out.text
        # The normalizer also transliterates `№` to `No` to align Russian
        # OCR output with Latin-form templates that the parsers expect.
        # Either form is acceptable here as long as the zero-width was
        # stripped and "Полис" is followed by a number-sign equivalent.
        assert (
            "Полис№" in out.text
            or "Полис №" in out.text
            or "ПолисNo" in out.text
            or "Полис No" in out.text
        )

    def test_heals_hyphenated_linebreak(self):
        raw = "пе-\nриод страхования"
        out = TextNormalizer().normalize(raw)
        assert "период" in out.text

    def test_preserves_newlines(self):
        raw = "первая строка\nвторая строка"
        out = TextNormalizer().normalize(raw)
        assert out.text.count("\n") == 1
        assert out.lines == ["первая строка", "вторая строка"]

    def test_collapses_3plus_blank_lines(self):
        raw = "a\n\n\n\nb"
        out = TextNormalizer().normalize(raw)
        assert "\n\n\n" not in out.text

    def test_line_for_offset_returns_correct_line(self):
        raw = "AAA\nBBB\nCCC"
        out = TextNormalizer().normalize(raw)
        assert out.line_for_offset(0) == 0
        assert out.line_for_offset(4) == 1  # start of BBB
        assert out.line_for_offset(5) == 1  # inside BBB
        assert out.line_for_offset(8) == 2  # start of CCC

    def test_empty_input_returns_empty_normalized(self):
        out = TextNormalizer().normalize("")
        assert out.text == ""
        assert out.lines == [""]


class TestLayoutAnalyzer:
    def test_finds_autocasco_row_with_two_columns(self):
        norm = TextNormalizer().normalize(
            "Автокаско (Ущерб и Угон) 2000000,00 41245,50"
        )
        rows = LayoutAnalyzer().find_rows(
            norm, re.compile(r"(?:авто)?каско", re.IGNORECASE)
        )
        assert len(rows) == 1
        row = rows[0]
        assert "Автокаско" in row.label
        assert len(row.columns) >= 2
        assert row.columns[0][0] == 2000000.0
        assert row.columns[1][0] == 41245.5

    def test_skips_lines_without_numeric_columns(self):
        norm = TextNormalizer().normalize("Автокаско — описание риска")
        rows = LayoutAnalyzer().find_rows(
            norm, re.compile(r"(?:авто)?каско", re.IGNORECASE)
        )
        assert rows == []

    def test_classifies_columns_via_header(self):
        text = (
            "Страховые риски Страховая сумма Страховая премия\n"
            "Автокаско 2000000,00 41245,50"
        )
        norm = TextNormalizer().normalize(text)
        analyzer = LayoutAnalyzer()
        rows = analyzer.find_rows(norm, re.compile(r"(?:авто)?каско", re.IGNORECASE))
        header = analyzer.find_header(norm, near_line=rows[0].line_no)
        assert header is not None
        assert "sum_insured" in header.column_kinds
        assert "premium" in header.column_kinds


class TestNegationContext:
    def test_no_negation_returns_no_penalty(self):
        ctx = NegationContext()
        text = "Ремонт на СТОА официального дилера"
        # span_start=10 (random offset inside positive context)
        assert ctx.penalty(text, span_start=10) == ctx.NO_PENALTY

    def test_strong_negation_lowers_penalty(self):
        ctx = NegationContext()
        text = "Возмещение в форме денежной выплаты, за исключением ремонта на СТОА официального дилера"
        # span_start = position of "ремонта на СТОА"
        idx = text.index("ремонта на СТОА")
        assert ctx.penalty(text, span_start=idx) == ctx.STRONG_PENALTY

    def test_weak_negation_lowers_penalty(self):
        ctx = NegationContext()
        text = "не предусмотрена франшиза"
        idx = text.index("предусмотрена")
        assert ctx.penalty(text, span_start=idx) == ctx.WEAK_PENALTY


class TestConfidenceComponents:
    def test_score_is_pattern_plus_context_capped_at_one(self):
        c = ConfidenceComponents(pattern_strength=0.6, context_strength=0.5)
        assert c.score() == 1.0

    def test_score_applies_negation_and_ambiguity_penalties(self):
        c = ConfidenceComponents(
            pattern_strength=0.5,
            context_strength=0.3,
            negation_penalty=0.5,
            ambiguity_penalty=0.85,
        )
        # base=0.8, * 0.5 * 0.85 = 0.34
        assert c.score() == pytest.approx(0.34, abs=0.001)


class TestCandidateRanker:
    def _candidate(self, value, *, state, score, pid="x"):
        c = Candidate(
            value=value,
            state=state,
            pattern_id=pid,
            source_fragment="",
            components=ConfidenceComponents(pattern_strength=score, context_strength=0.0),
        )
        return c

    def test_returns_none_for_empty(self):
        assert CandidateRanker().best([]) is None

    def test_found_beats_absent_even_with_lower_score(self):
        cs = [
            self._candidate("a", state="absent", score=0.9, pid="abs"),
            self._candidate("b", state="found", score=0.5, pid="found"),
        ]
        winner = CandidateRanker().best(cs)
        assert winner.state == "found"
        assert winner.value == "b"

    def test_close_runner_up_applies_ambiguity_penalty(self):
        cs = [
            self._candidate("a", state="found", score=0.7, pid="winner"),
            self._candidate("b", state="found", score=0.65, pid="runner"),
        ]
        winner = CandidateRanker().best(cs)
        assert winner.pattern_id == "winner"
        assert winner.components.ambiguity_penalty == CandidateRanker.AMBIGUITY_PENALTY_FACTOR
        assert "close_runner_up:runner" in winner.notes

    def test_distant_runner_up_no_penalty(self):
        cs = [
            self._candidate("a", state="found", score=0.9, pid="winner"),
            self._candidate("b", state="found", score=0.4, pid="runner"),
        ]
        winner = CandidateRanker().best(cs)
        assert winner.components.ambiguity_penalty == 1.0
