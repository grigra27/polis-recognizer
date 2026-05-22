"""Tests for PolicyholderBirthDateParser (PR #6) — PII-gated."""

from __future__ import annotations

import datetime

from polis_recognizer import PolicyExtractor
from polis_recognizer.extraction import run_extraction


_TEXT_LABEL_FIRST = (
    "Страхователь: Иванов И. И.\n"
    "Дата рождения: 01.01.1980\n"
    "ИНН 500100732259\n"
)

_TEXT_DATE_FIRST = (
    "Страхователь: Иванов И. И.\n"
    "01.01.1980 г.р.\n"
    "ИНН 500100732259\n"
)

_TEXT_TEXTUAL_DATE = (
    "Страхователь: Иванов И. И.\n"
    "Дата рождения: 1 января 1980\n"
)


class TestPiiGate:
    def test_birth_date_not_extracted_when_pii_off(self):
        cand = run_extraction(_TEXT_LABEL_FIRST).additional_fields.get(
            "policyholder_birth_date"
        )
        assert cand is None

    def test_birth_date_extracted_when_pii_on(self):
        cand = run_extraction(
            _TEXT_LABEL_FIRST, extract_pii=True
        ).additional_fields.get("policyholder_birth_date")
        assert cand is not None and cand.state == "found"
        assert cand.value == "1980-01-01"


class TestDateFormats:
    def test_label_then_numeric_date(self):
        cand = run_extraction(
            _TEXT_LABEL_FIRST, extract_pii=True
        ).additional_fields.get("policyholder_birth_date")
        assert cand is not None and cand.value == "1980-01-01"

    def test_date_then_label(self):
        cand = run_extraction(
            _TEXT_DATE_FIRST, extract_pii=True
        ).additional_fields.get("policyholder_birth_date")
        assert cand is not None and cand.value == "1980-01-01"

    def test_textual_date(self):
        cand = run_extraction(
            _TEXT_TEXTUAL_DATE, extract_pii=True
        ).additional_fields.get("policyholder_birth_date")
        assert cand is not None and cand.value == "1980-01-01"


class TestExtractedPolicyIntegration:
    def test_birth_date_none_by_default(self):
        result = PolicyExtractor().extract_from_text(_TEXT_LABEL_FIRST)
        assert result.policyholder is not None
        assert result.policyholder["birth_date"] is None

    def test_birth_date_converted_to_date_object_with_pii_flag(self):
        result = PolicyExtractor(extract_pii=True).extract_from_text(
            _TEXT_LABEL_FIRST
        )
        assert result.policyholder is not None
        bd = result.policyholder["birth_date"]
        # Public API surface uses datetime.date, not ISO strings —
        # matches the policy_period convention.
        assert isinstance(bd, datetime.date)
        assert bd == datetime.date(1980, 1, 1)
