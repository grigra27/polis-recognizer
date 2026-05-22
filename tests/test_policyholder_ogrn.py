"""Tests for PolicyholderOGRNParser (PR #3)."""

from __future__ import annotations

from polis_recognizer import PolicyExtractor
from polis_recognizer.extraction import run_extraction


_VALID_INN_10 = "7707083893"
_VALID_OGRN_13 = "1027700132195"  # СберБанк, public
_VALID_OGRNIP_15 = "304500116000157"  # Canonical algorithm example


class TestAnchoredText:
    def test_extracts_valid_ogrn_13(self):
        text = (
            f"Страхователь: ООО Альфа\n"
            f"ИНН {_VALID_INN_10}\n"
            f"ОГРН {_VALID_OGRN_13}\n"
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_ogrn"
        )
        assert cand is not None and cand.state == "found"
        assert cand.value == _VALID_OGRN_13

    def test_extracts_valid_ogrnip_15(self):
        text = (
            f"Страхователь: ИП Сидоров С. С.\n"
            f"ОГРНИП {_VALID_OGRNIP_15}\n"
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_ogrn"
        )
        assert cand is not None and cand.state == "found"
        assert cand.value == _VALID_OGRNIP_15

    def test_rejects_ogrn_with_invalid_checksum(self):
        bad = _VALID_OGRN_13[:-1] + (
            "0" if _VALID_OGRN_13[-1] != "0" else "1"
        )
        text = f"Страхователь: ООО Альфа\nОГРН {bad}\n"
        cand = run_extraction(text).additional_fields.get(
            "policyholder_ogrn"
        )
        assert cand is None or cand.state == "not_found"

    def test_ignores_ogrn_outside_policyholder_block(self):
        # The insurer's ОГРН lives in the signature/footer block. Without
        # a Страхователь anchor, we must not extract anything.
        text = f"Страховщик: ПАО СК Дельта\nОГРН {_VALID_OGRN_13}\n"
        cand = run_extraction(text).additional_fields.get(
            "policyholder_ogrn"
        )
        assert cand is None or cand.state == "not_found"


class TestTableStrategy:
    def test_table_with_policyholder_anchor_picks_ogrn(self):
        tables = [
            [
                [
                    ["Страхователь", "ООО Гамма"],
                    ["ИНН", _VALID_INN_10],
                    ["ОГРН", _VALID_OGRN_13],
                ]
            ]
        ]
        cand = run_extraction(
            "Страхователь: ООО Гамма\n", tables=tables
        ).additional_fields.get("policyholder_ogrn")
        assert cand is not None
        assert cand.value == _VALID_OGRN_13


class TestExtractedPolicyIntegration:
    def test_ogrn_surfaces_on_extracted_policy(self):
        text = (
            f"Страхователь: ООО Альфа\n"
            f"ИНН {_VALID_INN_10}\n"
            f"ОГРН {_VALID_OGRN_13}\n"
        )
        result = PolicyExtractor().extract_from_text(text)
        assert result.policyholder is not None
        assert result.policyholder["ogrn"] == _VALID_OGRN_13
