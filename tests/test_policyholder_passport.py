"""Tests for PolicyholderPassportParser (PR #6) — PII-gated."""

from __future__ import annotations

from polis_recognizer import PolicyExtractor
from polis_recognizer.extraction import run_extraction


_TEXT_WITH_PASSPORT = (
    "Страхователь: Иванов Иван Иванович\n"
    "Паспорт 12 34 567890 выдан 01.01.2010\n"
    "ИНН 500100732259\n"
)


class TestPiiGate:
    def test_passport_not_extracted_when_pii_off(self):
        # Default: extract_pii=False. The parser must short-circuit.
        cand = run_extraction(_TEXT_WITH_PASSPORT).additional_fields.get(
            "policyholder_passport"
        )
        # Either the key isn't present at all, or it's None (no winner).
        assert cand is None

    def test_passport_extracted_when_pii_on(self):
        cand = run_extraction(
            _TEXT_WITH_PASSPORT, extract_pii=True
        ).additional_fields.get("policyholder_passport")
        assert cand is not None and cand.state == "found"
        assert cand.value == {"series": "1234", "number": "567890"}


class TestPassportFormats:
    def test_paspoport_with_internal_series_space(self):
        text = (
            "Страхователь: Иванов И. И.\n"
            "Паспорт 12 34 567890\n"
        )
        cand = run_extraction(
            text, extract_pii=True
        ).additional_fields.get("policyholder_passport")
        assert cand is not None
        assert cand.value == {"series": "1234", "number": "567890"}

    def test_paspoport_with_seria_keyword(self):
        text = (
            "Страхователь: Иванов И. И.\n"
            "Паспорт серия 1234 номер 567890\n"
        )
        cand = run_extraction(
            text, extract_pii=True
        ).additional_fields.get("policyholder_passport")
        assert cand is not None
        assert cand.value == {"series": "1234", "number": "567890"}

    def test_paspoport_grazhdanina_rf(self):
        text = (
            "Страхователь: Иванов И. И.\n"
            "Паспорт гражданина РФ 1234 567890\n"
        )
        cand = run_extraction(
            text, extract_pii=True
        ).additional_fields.get("policyholder_passport")
        assert cand is not None
        assert cand.value == {"series": "1234", "number": "567890"}


class TestExtractedPolicyIntegration:
    def test_passport_none_by_default(self):
        result = PolicyExtractor().extract_from_text(_TEXT_WITH_PASSPORT)
        assert result.policyholder is not None
        assert result.policyholder["passport"] is None

    def test_passport_surfaces_with_pii_flag(self):
        result = PolicyExtractor(extract_pii=True).extract_from_text(
            _TEXT_WITH_PASSPORT
        )
        assert result.policyholder is not None
        assert result.policyholder["passport"] == {
            "series": "1234",
            "number": "567890",
        }
