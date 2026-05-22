"""Tests for PolicyholderINNParser (PR #3).

The parser must:
- accept anchored ИНН-10/12 with a valid checksum;
- reject ИНН with a bad checksum (precision guard);
- ignore ИНН outside the policyholder block (insurer ИНН leak guard);
- prefer table-cell matches when pdfplumber surfaced them.
"""

from __future__ import annotations

from polis_recognizer import PolicyExtractor
from polis_recognizer.extraction import run_extraction


_VALID_INN_10 = "7707083893"  # СберБанк, public
_VALID_INN_12 = "500100732259"  # Canonical algorithm example


class TestAnchoredText:
    def test_extracts_valid_inn_10_anchored(self):
        text = f"Страхователь: ООО Альфа\nИНН {_VALID_INN_10}\n"
        cand = run_extraction(text).additional_fields.get(
            "policyholder_inn"
        )
        assert cand is not None and cand.state == "found"
        assert cand.value == _VALID_INN_10
        assert cand.pattern_id in ("anchored_text", "table_cell")

    def test_extracts_valid_inn_12_anchored(self):
        text = f"Страхователь: Петров П. П.\nИНН {_VALID_INN_12}\n"
        cand = run_extraction(text).additional_fields.get(
            "policyholder_inn"
        )
        assert cand is not None and cand.state == "found"
        assert cand.value == _VALID_INN_12

    def test_rejects_inn_with_invalid_checksum(self):
        # 10-digit run but checksum-invalid (last digit deliberately
        # wrong). Must NOT emit a found candidate.
        bad_inn = _VALID_INN_10[:-1] + (
            "0" if _VALID_INN_10[-1] != "0" else "1"
        )
        text = f"Страхователь: ООО Альфа\nИНН {bad_inn}\n"
        cand = run_extraction(text).additional_fields.get(
            "policyholder_inn"
        )
        assert cand is None or cand.state == "not_found"

    def test_ignores_inn_outside_policyholder_block(self):
        # Insurer's ИНН appears in the signature block. Without a
        # Страхователь anchor, no ИНН should be extracted.
        text = (
            "Договор страхования транспортного средства\n"
            "...\n"
            f"Страховщик: ПАО СК Дельта\nИНН {_VALID_INN_10}\n"
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_inn"
        )
        assert cand is None or cand.state == "not_found"

    def test_picks_policyholder_inn_when_insurer_inn_also_present(self):
        # Two ИНН in the document — only the one inside the policyholder
        # block should be picked. The block locator stops at "Страховщик"
        # so the insurer's ИНН is unreachable.
        text = (
            f"Страхователь: ООО Альфа\nИНН {_VALID_INN_10}\n"
            f"Страховщик: ПАО СК Дельта\nИНН 7710140679\n"
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_inn"
        )
        assert cand is not None and cand.value == _VALID_INN_10


class TestTableStrategy:
    def test_table_with_policyholder_anchor_picks_inn(self):
        tables = [
            [
                [
                    ["Страхователь", "ООО Гамма"],
                    ["ИНН", _VALID_INN_10],
                ]
            ]
        ]
        cand = run_extraction(
            "Страхователь: ООО Гамма\n", tables=tables
        ).additional_fields.get("policyholder_inn")
        assert cand is not None and cand.value == _VALID_INN_10
        # Table strategy outranks anchored-text strategy.
        assert cand.pattern_id == "table_cell"

    def test_table_without_policyholder_anchor_does_not_match(self):
        # A table that doesn't contain a Страхователь cell is
        # ambiguous — could be the insurer block — so we ignore it.
        tables = [[[["Страховщик", "ПАО СК Дельта"], ["ИНН", _VALID_INN_10]]]]
        cand = run_extraction("", tables=tables).additional_fields.get(
            "policyholder_inn"
        )
        assert cand is None or cand.state == "not_found"


class TestExtractedPolicyIntegration:
    def test_inn_surfaces_on_extracted_policy(self):
        text = f"Страхователь: ООО Альфа\nИНН {_VALID_INN_10}\n"
        result = PolicyExtractor().extract_from_text(text)
        assert result.policyholder is not None
        assert result.policyholder["inn"] == _VALID_INN_10
