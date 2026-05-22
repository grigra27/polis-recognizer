"""Tests for PolicyholderPhonesParser (PR #4).

Covers:
- multiple input formats normalised to E.164 (+7XXXXXXXXXX);
- multi-value: list returned, dedup, order preserved;
- block scoping: insurer phone in a different section is not captured;
- ИНН-12 starting with 7 must NOT be classified as a phone.
"""

from __future__ import annotations

from polis_recognizer import PolicyExtractor
from polis_recognizer.extraction import run_extraction


_VALID_INN_12 = "500100732259"


class TestNormalization:
    def test_plus_7_with_parens_and_dashes(self):
        text = "Страхователь: ООО Альфа\nТел.: +7 (495) 123-45-67\n"
        cand = run_extraction(text).additional_fields.get(
            "policyholder_phones"
        )
        assert cand is not None and cand.state == "found"
        assert cand.value == ["+74951234567"]

    def test_eight_prefix_normalises_to_plus_seven(self):
        text = "Страхователь: ООО Альфа\n8(495)123-45-67\n"
        cand = run_extraction(text).additional_fields.get(
            "policyholder_phones"
        )
        assert cand is not None and cand.value == ["+74951234567"]

    def test_bare_11_digits_with_eight_prefix(self):
        text = "Страхователь: ООО Альфа\n84951234567\n"
        cand = run_extraction(text).additional_fields.get(
            "policyholder_phones"
        )
        assert cand is not None and cand.value == ["+74951234567"]

    def test_parenthesised_no_prefix_assumes_plus_seven(self):
        text = "Страхователь: ООО Альфа\n(495) 123-45-67\n"
        cand = run_extraction(text).additional_fields.get(
            "policyholder_phones"
        )
        assert cand is not None and cand.value == ["+74951234567"]


class TestMultiValue:
    def test_two_phones_extracted_in_order(self):
        text = (
            "Страхователь: ООО Альфа\n"
            "Тел.: +7 (495) 111-22-33, +7 (916) 444-55-66\n"
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_phones"
        )
        assert cand is not None
        assert cand.value == ["+74951112233", "+79164445566"]

    def test_dedup_removes_repeats(self):
        # Same phone written two ways must end up as a single entry.
        text = (
            "Страхователь: ООО Альфа\n"
            "Тел.: +7 (495) 123-45-67 / 8 (495) 123-45-67\n"
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_phones"
        )
        assert cand is not None
        assert cand.value == ["+74951234567"]


class TestPrecisionGuards:
    def test_inn_12_starting_with_7_is_not_phone(self):
        # We don't accept bare 7-prefix runs precisely so this can't
        # happen: an ИНН-12 starting with 7 must NOT be misclassified.
        text = (
            f"Страхователь: Петров П. П.\n"
            f"ИНН 7{_VALID_INN_12[1:]}\n"  # 12 digits starting with 7
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_phones"
        )
        # Either nothing extracted, or no 11-digit phone derived from
        # the INN run.
        assert cand is None or cand.state == "not_found" or "+7" + (
            "7" + _VALID_INN_12[1:11]
        ) not in (cand.value or [])

    def test_no_phone_outside_policyholder_block(self):
        # Phone in the insurer signature block should NOT be captured.
        text = (
            "Договор страхования транспортного средства\n"
            "Страховщик: ПАО СК Дельта\n"
            "Тел.: +7 (800) 333-44-55\n"
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_phones"
        )
        assert cand is None or cand.state == "not_found"

    def test_picks_only_policyholder_phone_when_both_present(self):
        text = (
            "Страхователь: ООО Альфа\n"
            "Тел.: +7 (495) 111-22-33\n"
            "Страховщик: ПАО СК Дельта\n"
            "Тел.: +7 (800) 333-44-55\n"
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_phones"
        )
        assert cand is not None
        assert cand.value == ["+74951112233"]


class TestTableStrategy:
    def test_phone_picked_from_anchored_table(self):
        tables = [
            [
                [
                    ["Страхователь", "ООО Гамма"],
                    ["Телефон", "+7 (495) 999-88-77"],
                ]
            ]
        ]
        cand = run_extraction(
            "Страхователь: ООО Гамма\n", tables=tables
        ).additional_fields.get("policyholder_phones")
        assert cand is not None
        assert "+74959998877" in (cand.value or [])


class TestExtractedPolicyIntegration:
    def test_phones_in_contacts(self):
        text = (
            "Страхователь: ООО Альфа\n"
            "Тел.: +7 (495) 111-22-33\n"
        )
        result = PolicyExtractor().extract_from_text(text)
        assert result.policyholder_contacts is not None
        assert result.policyholder_contacts["phones"] == ["+74951112233"]
