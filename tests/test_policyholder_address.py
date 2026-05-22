"""Tests for PolicyholderAddressParser (PR #5)."""

from __future__ import annotations

from polis_recognizer import PolicyExtractor
from polis_recognizer.extraction import run_extraction


class TestAnchoredText:
    def test_extracts_inline_address(self):
        text = (
            "Страхователь: ООО Альфа\n"
            "Адрес: 101000, г. Москва, ул. Ленина, д. 1\n"
            "ИНН 7707083893\n"
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_address"
        )
        assert cand is not None and cand.state == "found"
        assert cand.value == "101000, г. Москва, ул. Ленина, д. 1"

    def test_stops_at_inn_label(self):
        text = (
            "Страхователь: ООО Альфа\n"
            "Адрес: 101000, г. Москва, ул. Ленина, д. 1 ИНН 7707083893\n"
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_address"
        )
        assert cand is not None
        assert "ИНН" not in cand.value
        assert "Москва" in cand.value

    def test_stops_at_phone_label(self):
        text = (
            "Страхователь: ООО Альфа\n"
            "Адрес: 101000, г. Москва Телефон: +7 (495) 111-22-33\n"
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_address"
        )
        assert cand is not None
        assert "Телефон" not in cand.value
        assert "Москва" in cand.value

    def test_collapses_internal_whitespace(self):
        text = (
            "Страхователь: ООО Альфа\n"
            "Адрес:   101000,    г.   Москва,  ул. Ленина\n"
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_address"
        )
        assert cand is not None
        assert "  " not in cand.value
        assert cand.value.startswith("101000")

    def test_trims_trailing_punctuation(self):
        text = (
            "Страхователь: ООО Альфа\n"
            "Адрес: 101000, г. Москва, ул. Ленина.\n"
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_address"
        )
        assert cand is not None
        assert not cand.value.endswith(".")
        assert not cand.value.endswith(",")

    def test_zaregistrirovan_po_adresu_anchor(self):
        text = (
            "Страхователь: Иванов И. И.\n"
            "Зарегистрирован по адресу: 190000, г. Санкт-Петербург, "
            "Невский пр., 1\n"
            "ИНН 500100732259\n"
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_address"
        )
        assert cand is not None
        assert "Санкт-Петербург" in cand.value

    def test_no_address_outside_policyholder_block(self):
        text = (
            "Договор страхования\n"
            "Страховщик: ПАО СК Дельта\n"
            "Адрес: 119992, г. Москва, ул. Корпоративная, д. 1\n"
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_address"
        )
        assert cand is None or cand.state == "not_found"


class TestTableStrategy:
    def test_address_picked_from_anchored_table(self):
        tables = [
            [
                [
                    ["Страхователь", "ООО Гамма"],
                    ["Адрес", "101000, г. Москва, ул. Тверская, д. 7"],
                ]
            ]
        ]
        cand = run_extraction(
            "Страхователь: ООО Гамма\n", tables=tables
        ).additional_fields.get("policyholder_address")
        assert cand is not None
        assert "Тверская" in cand.value


class TestExtractedPolicyIntegration:
    def test_address_in_contacts(self):
        text = (
            "Страхователь: ООО Альфа\n"
            "Адрес: 101000, г. Москва, ул. Ленина, д. 1\n"
        )
        result = PolicyExtractor().extract_from_text(text)
        assert result.policyholder_contacts is not None
        assert (
            "Москва" in result.policyholder_contacts["address"]
        )
