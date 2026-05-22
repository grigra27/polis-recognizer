"""Tests for PolicyholderKPPParser (PR #3).

КПП has no checksum, so the parser must rely on label-anchor context
to keep precision up. There's deliberately no bare-9-digit fallback —
account fragments and phone numbers would flood it.
"""

from __future__ import annotations

from polis_recognizer import PolicyExtractor
from polis_recognizer.extraction import run_extraction


_VALID_INN_10 = "7707083893"
_KPP_EXAMPLE = "770701001"


class TestAnchoredText:
    def test_extracts_kpp_with_label(self):
        text = (
            f"Страхователь: ООО Альфа\n"
            f"ИНН {_VALID_INN_10}\n"
            f"КПП {_KPP_EXAMPLE}\n"
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_kpp"
        )
        assert cand is not None and cand.state == "found"
        assert cand.value == _KPP_EXAMPLE

    def test_extracts_kpp_with_colon(self):
        text = (
            f"Страхователь: ООО Альфа\n"
            f"КПП: {_KPP_EXAMPLE}\n"
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_kpp"
        )
        assert cand is not None and cand.value == _KPP_EXAMPLE

    def test_does_not_match_unlabeled_9_digits(self):
        # A naked 9-digit number must not become a КПП.
        text = (
            "Страхователь: ООО Альфа\n"
            "Контактный номер 123456789\n"
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_kpp"
        )
        assert cand is None or cand.state == "not_found"

    def test_ignores_kpp_outside_policyholder_block(self):
        text = f"Страховщик: ПАО СК Дельта\nКПП {_KPP_EXAMPLE}\n"
        cand = run_extraction(text).additional_fields.get(
            "policyholder_kpp"
        )
        assert cand is None or cand.state == "not_found"


class TestTableStrategy:
    def test_table_with_policyholder_anchor_picks_kpp(self):
        tables = [
            [
                [
                    ["Страхователь", "ООО Гамма"],
                    ["ИНН", _VALID_INN_10],
                    ["КПП", _KPP_EXAMPLE],
                ]
            ]
        ]
        cand = run_extraction(
            "Страхователь: ООО Гамма\n", tables=tables
        ).additional_fields.get("policyholder_kpp")
        assert cand is not None
        assert cand.value == _KPP_EXAMPLE


class TestExtractedPolicyIntegration:
    def test_kpp_surfaces_on_extracted_policy(self):
        text = (
            f"Страхователь: ООО Альфа\n"
            f"ИНН {_VALID_INN_10}\n"
            f"КПП {_KPP_EXAMPLE}\n"
        )
        result = PolicyExtractor().extract_from_text(text)
        assert result.policyholder is not None
        assert result.policyholder["kpp"] == _KPP_EXAMPLE
