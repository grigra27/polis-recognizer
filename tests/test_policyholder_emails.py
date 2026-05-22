"""Tests for PolicyholderEmailsParser (PR #4)."""

from __future__ import annotations

from polis_recognizer import PolicyExtractor
from polis_recognizer.extraction import run_extraction


class TestExtraction:
    def test_extracts_email(self):
        text = "Страхователь: ООО Альфа\nE-mail: contact@alpha.ru\n"
        cand = run_extraction(text).additional_fields.get(
            "policyholder_emails"
        )
        assert cand is not None and cand.state == "found"
        assert cand.value == ["contact@alpha.ru"]

    def test_lowercases(self):
        text = "Страхователь: ООО Альфа\nEmail: Contact@Alpha.RU\n"
        cand = run_extraction(text).additional_fields.get(
            "policyholder_emails"
        )
        assert cand is not None
        assert cand.value == ["contact@alpha.ru"]

    def test_multiple_emails_in_order_with_dedup(self):
        text = (
            "Страхователь: ООО Альфа\n"
            "Email: a@alpha.ru, b@alpha.ru, A@alpha.ru\n"
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_emails"
        )
        assert cand is not None
        assert cand.value == ["a@alpha.ru", "b@alpha.ru"]

    def test_no_email_outside_policyholder_block(self):
        text = (
            "Договор страхования транспортного средства\n"
            "Страховщик: ПАО СК Дельта\n"
            "Email: support@insurer.ru\n"
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_emails"
        )
        assert cand is None or cand.state == "not_found"

    def test_picks_only_policyholder_email_when_both_present(self):
        text = (
            "Страхователь: ООО Альфа\n"
            "Email: contact@alpha.ru\n"
            "Страховщик: ПАО СК Дельта\n"
            "Email: support@insurer.ru\n"
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_emails"
        )
        assert cand is not None
        assert cand.value == ["contact@alpha.ru"]

    def test_no_match_returns_not_found(self):
        text = "Страхователь: ООО Альфа\nИНН 7707083893\n"
        cand = run_extraction(text).additional_fields.get(
            "policyholder_emails"
        )
        assert cand is None or cand.state == "not_found"


class TestTableStrategy:
    def test_email_picked_from_anchored_table(self):
        tables = [
            [
                [
                    ["Страхователь", "ООО Гамма"],
                    ["Email", "ops@gamma.ru"],
                ]
            ]
        ]
        cand = run_extraction(
            "Страхователь: ООО Гамма\n", tables=tables
        ).additional_fields.get("policyholder_emails")
        assert cand is not None
        assert "ops@gamma.ru" in (cand.value or [])


class TestExtractedPolicyIntegration:
    def test_emails_in_contacts(self):
        text = "Страхователь: ООО Альфа\nEmail: contact@alpha.ru\n"
        result = PolicyExtractor().extract_from_text(text)
        assert result.policyholder_contacts is not None
        assert result.policyholder_contacts["emails"] == ["contact@alpha.ru"]
