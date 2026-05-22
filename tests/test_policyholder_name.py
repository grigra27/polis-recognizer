"""Tests for PolicyholderNameParser (PR #2)."""

from __future__ import annotations

from polis_recognizer import PolicyExtractor
from polis_recognizer.extraction import run_extraction


class TestAnchoredTextStrategy:
    def test_extracts_legal_entity_name(self):
        text = (
            'Страхователь: ООО "Ромашка"\n'
            "ИНН 7707083893\n"
            "Адрес: 101000, г. Москва, ул. Ленина, д. 1\n"
        )
        result = run_extraction(text)
        cand = result.additional_fields.get("policyholder_name")
        assert cand is not None and cand.state == "found"
        assert cand.value == 'ООО "Ромашка"'
        assert cand.pattern_id in ("anchor_text", "table_cell")

    def test_extracts_individual_full_fio(self):
        text = (
            "Страхователь: Иванов Иван Иванович\n"
            "Дата рождения: 01.01.1980\n"
            "ИНН 500100732259\n"
        )
        result = run_extraction(text)
        cand = result.additional_fields.get("policyholder_name")
        assert cand is not None and cand.state == "found"
        assert cand.value == "Иванов Иван Иванович"

    def test_stops_at_inn_label(self):
        # ИНН label must terminate the name capture even without a
        # newline between them.
        text = "Страхователь: ООО Альфа ИНН 7707083893\n"
        result = run_extraction(text)
        cand = result.additional_fields.get("policyholder_name")
        assert cand is not None and cand.state == "found"
        assert cand.value == "ООО Альфа"

    def test_stops_at_address_label(self):
        text = "Страхователь: ИП Петров Петр Петрович Адрес: 101000, г. Москва"
        result = run_extraction(text)
        cand = result.additional_fields.get("policyholder_name")
        assert cand is not None and cand.state == "found"
        assert cand.value == "ИП Петров Петр Петрович"

    def test_does_not_bleed_into_strakhovshchik(self):
        # The block locator stops at "Страховщик"; the name parser must
        # not capture anything from the insurer block.
        text = (
            "Страхователь: ООО Альфа\n"
            "ИНН 7707083893\n"
            "Страховщик: ПАО СК Бета\n"
        )
        result = run_extraction(text)
        cand = result.additional_fields.get("policyholder_name")
        assert cand is not None and cand.state == "found"
        assert "ООО Альфа" == cand.value
        assert "Бета" not in cand.value

    def test_returns_not_found_without_anchor(self):
        text = (
            "Договор страхования транспортного средства\n"
            "Иванов Иван Иванович\n"
        )
        result = run_extraction(text)
        cand = result.additional_fields.get("policyholder_name")
        # Either the candidate is None, or it's a not_found state.
        assert cand is None or cand.state == "not_found"

    def test_uppercase_label(self):
        text = "СТРАХОВАТЕЛЬ: АО Дельта\nИНН 7707083893\n"
        result = run_extraction(text)
        cand = result.additional_fields.get("policyholder_name")
        assert cand is not None and cand.state == "found"
        assert cand.value == "АО Дельта"

    def test_confidence_boosted_when_name_looks_like_org_or_fio(self):
        # Both ООО Ромашка (org shape) and Иванов Иван Иванович
        # (ФИО shape) should land above a marginal threshold thanks
        # to the shape bonus.
        for text in (
            "Страхователь: ООО Ромашка\nИНН 7707083893\n",
            "Страхователь: Иванов Иван Иванович\nИНН 500100732259\n",
        ):
            cand = run_extraction(text).additional_fields.get(
                "policyholder_name"
            )
            assert cand is not None and cand.confidence >= 0.6


class TestTableStrategy:
    def test_table_cell_match_beats_anchor_when_both_present(self):
        # When pdfplumber surfaces a labelled cell, that path produces
        # a higher-confidence Candidate than the in-text anchor path.
        text = "Страхователь: ООО Альфа\nИНН 7707083893\n"
        tables = [
            [  # page 1
                [  # one table
                    ["Страхователь", 'ООО "Гамма"'],
                    ["ИНН", "7707083893"],
                ]
            ]
        ]
        result = run_extraction(text, tables=tables)
        cand = result.additional_fields.get("policyholder_name")
        assert cand is not None and cand.state == "found"
        # Table value should win on confidence.
        assert cand.value == 'ООО "Гамма"'
        assert cand.pattern_id == "table_cell"

    def test_table_cell_ignored_when_label_doesnt_match(self):
        tables = [[[["Страховщик", "ПАО СК Бета"]]]]
        result = run_extraction("Страхователь: ООО Альфа\n", tables=tables)
        cand = result.additional_fields.get("policyholder_name")
        # The table cell must not be picked up — its label is the insurer.
        if cand and cand.state == "found":
            assert cand.value != "ПАО СК Бета"


class TestExtractedPolicyIntegration:
    def test_policyholder_populated_when_name_extracted(self):
        text = "Страхователь: ООО Эпсилон\nИНН 7707083893\n"
        result = PolicyExtractor().extract_from_text(text)
        assert result.policyholder is not None
        assert result.policyholder["name"] == "ООО Эпсилон"
