"""Tests for PolicyholderTypeParser (PR #2).

Decision rules tested in priority order:
1. Organisation prefix → legal_entity.
2. Valid 10-digit ИНН → legal_entity.
3. Valid 12-digit ИНН → individual.
4. ФИО pattern → individual.
"""

from __future__ import annotations

from polis_recognizer import PolicyExtractor
from polis_recognizer.extraction import run_extraction


# Public, checksum-correct fixtures used throughout.
_VALID_INN_10 = "7707083893"
_VALID_INN_12 = "500100732259"


class TestOrgPrefixRule:
    def test_ooo_prefix_marks_legal_entity(self):
        text = 'Страхователь: ООО "Ромашка"\n'
        cand = run_extraction(text).additional_fields.get(
            "policyholder_type"
        )
        assert cand is not None and cand.state == "found"
        assert cand.value == "legal_entity"
        assert cand.pattern_id == "org_prefix"

    def test_ip_prefix_marks_legal_entity(self):
        # ИП — natural person registered as entrepreneur, but treated as
        # legal_entity in the type schema (see parser docstring).
        text = "Страхователь: ИП Петров Петр Петрович\n"
        cand = run_extraction(text).additional_fields.get(
            "policyholder_type"
        )
        assert cand is not None and cand.value == "legal_entity"
        assert cand.pattern_id == "org_prefix"

    def test_pao_prefix_marks_legal_entity(self):
        text = "Страхователь: ПАО Сбербанк\n"
        cand = run_extraction(text).additional_fields.get(
            "policyholder_type"
        )
        assert cand is not None and cand.value == "legal_entity"


class TestInnChecksumRule:
    def test_valid_inn_10_marks_legal_entity(self):
        # Use a non-org name so the org-prefix rule does not preempt.
        text = f"Страхователь: некий контрагент\nИНН {_VALID_INN_10}\n"
        cand = run_extraction(text).additional_fields.get(
            "policyholder_type"
        )
        assert cand is not None and cand.state == "found"
        assert cand.value == "legal_entity"
        assert cand.pattern_id == "inn10_checksum"

    def test_valid_inn_12_marks_individual(self):
        # Use initials so the FIO fallback rule does not preempt the
        # ИНН-12 rule (initials don't match the 3-Cyrillic-words shape).
        text = f"Страхователь: Петров П. П.\nИНН {_VALID_INN_12}\n"
        cand = run_extraction(text).additional_fields.get(
            "policyholder_type"
        )
        assert cand is not None and cand.state == "found"
        assert cand.value == "individual"
        assert cand.pattern_id == "inn12_checksum"

    def test_invalid_inn_does_not_classify(self):
        # 10 digits that fail checksum — must not produce a legal_entity
        # verdict via the INN rule.
        text = "Страхователь: некий контрагент\nИНН 1234567890\n"
        cand = run_extraction(text).additional_fields.get(
            "policyholder_type"
        )
        # Either not_found, or classified via a different rule (not
        # inn10_checksum). The OCR-junk INN must not pass.
        assert cand is None or cand.pattern_id != "inn10_checksum"


class TestFioFallbackRule:
    def test_three_word_fio_marks_individual(self):
        # No org prefix, no INN — only the ФИО shape gives a signal.
        text = "Страхователь: Иванов Иван Иванович\n"
        cand = run_extraction(text).additional_fields.get(
            "policyholder_type"
        )
        assert cand is not None and cand.state == "found"
        assert cand.value == "individual"
        assert cand.pattern_id == "fio_pattern"


class TestNoMatch:
    def test_no_anchor_returns_not_found(self):
        text = "Договор страхования транспортного средства\n"
        cand = run_extraction(text).additional_fields.get(
            "policyholder_type"
        )
        assert cand is None or cand.state == "not_found"

    def test_anchor_without_recognisable_party_is_not_found(self):
        text = "Страхователь: ---\nИНН: ---\n"
        cand = run_extraction(text).additional_fields.get(
            "policyholder_type"
        )
        assert cand is None or cand.state == "not_found"


class TestRulePriority:
    def test_org_prefix_beats_inn_12(self):
        # ИП Петров has ИНН-12 (12-digit) but ИП prefix should win and
        # classify as legal_entity, not individual.
        text = f"Страхователь: ИП Петров Петр Петрович\nИНН {_VALID_INN_12}\n"
        cand = run_extraction(text).additional_fields.get(
            "policyholder_type"
        )
        assert cand is not None
        assert cand.value == "legal_entity"
        assert cand.pattern_id == "org_prefix"


class TestExtractedPolicyIntegration:
    def test_type_surfaces_on_extracted_policy(self):
        text = f"Страхователь: ООО Альфа\nИНН {_VALID_INN_10}\n"
        result = PolicyExtractor().extract_from_text(text)
        assert result.policyholder is not None
        assert result.policyholder["type"] == "legal_entity"
