"""Tests for PolicyholderPostalCodeParser (PR #5)."""

from __future__ import annotations

from polis_recognizer import PolicyExtractor
from polis_recognizer.extraction import run_extraction


class TestExtraction:
    def test_extracts_postal_code_from_address(self):
        text = (
            "Страхователь: ООО Альфа\n"
            "Адрес: 101000, г. Москва, ул. Ленина, д. 1\n"
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_postal_code"
        )
        assert cand is not None and cand.state == "found"
        assert cand.value == "101000"

    def test_accepts_only_first_digit_one_to_six(self):
        # 6-digit run starting with 7-9 must NOT be classified as a
        # postal code (no district codes 7+).
        text = "Страхователь: ООО Альфа\nКод 999000 в системе\n"
        cand = run_extraction(text).additional_fields.get(
            "policyholder_postal_code"
        )
        assert cand is None or cand.state == "not_found"

    def test_does_not_match_inside_longer_digit_runs(self):
        # 12-digit ИНН starting with [1-6] must NOT yield a postal
        # code (lookahead/lookbehind reject digit-flanked matches).
        text = (
            "Страхователь: Петров П. П.\n"
            "ИНН 500100732259\n"
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_postal_code"
        )
        assert cand is None or cand.state == "not_found"

    def test_picks_postal_code_only_from_policyholder_block(self):
        text = (
            "Договор страхования\n"
            "Страховщик: ПАО СК Дельта\n"
            "Адрес: 119992, г. Москва\n"
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_postal_code"
        )
        assert cand is None or cand.state == "not_found"


class TestExtractedPolicyIntegration:
    def test_postal_code_in_contacts(self):
        text = (
            "Страхователь: ООО Альфа\n"
            "Адрес: 190000, г. Санкт-Петербург\n"
        )
        result = PolicyExtractor().extract_from_text(text)
        assert result.policyholder_contacts is not None
        assert result.policyholder_contacts["postal_code"] == "190000"
