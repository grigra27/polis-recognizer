"""Regression tests for 0.3.2 — fixes derived from real corpus inspection.

Continuation of 0.3.1 cleanup. Each test fixes a specific failure
observed when running 0.3.1 against the digital_pdf/batch_1 batch.
"""

from __future__ import annotations

from polis_recognizer.extraction import run_extraction
from polis_recognizer.extraction.normalizer import TextNormalizer
from polis_recognizer.extraction.policyholder_block import (
    locate_policyholder_block,
    policyholder_table_rows,
)


_VALID_INN_10 = "7707083893"
_BROKER_TABLE_PREFIX = [
    ["Брокер", 'ООО "Онлайн-брокер"'],
    ["Адрес", "140002, Московская область, г.Люберцы, ул.Парковая, д.3"],
    ["Телефон", "8-800-200-0-900"],
    ["Email", "online@on-linebroker.ru"],
]


class TestSlashCombinedLabelFix:
    """Russian lizinging КАСКО polises print a combined label like
    "СТРАХОВАТЕЛЬ / ЛИЗИНГОПОЛУЧАТЕЛЬ:". The anchor must absorb the
    whole construct so the block span starts AFTER the combined
    label, not in the middle of " / ЛИЗИНГОПОЛУЧАТЕЛЬ:".

    Real corpus example (batch_1 #3, #10): without absorbing the
    suffix, the captured name was "/ ЛИЗИНГОПОЛУЧА".
    """

    def test_combined_label_lizingopoluchatel(self):
        text = (
            "2. СТРАХОВАТЕЛЬ / ЛИЗИНГОПОЛУЧАТЕЛЬ: ООО \"ПМК 77\"\n"
            f"ИНН {_VALID_INN_10}\n"
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_name"
        )
        assert cand is not None and cand.state == "found"
        assert cand.value == 'ООО "ПМК 77"'
        assert "ЛИЗИНГОПОЛУЧА" not in cand.value
        assert not cand.value.startswith("/")

    def test_combined_label_vygodopriobretatel(self):
        text = "СТРАХОВАТЕЛЬ / ВЫГОДОПРИОБРЕТАТЕЛЬ: ИП Петров П. П.\n"
        cand = run_extraction(text).additional_fields.get(
            "policyholder_name"
        )
        assert cand is not None
        # _strip_trailing_punctuation drops the final dot, which is
        # OK — initials are still recognizable.
        assert cand.value.startswith("ИП Петров П. П")

    def test_plain_anchor_still_works(self):
        text = "Страхователь: ООО Альфа\n"
        cand = run_extraction(text).additional_fields.get(
            "policyholder_name"
        )
        assert cand is not None
        assert cand.value == "ООО Альфа"


class TestBankDetailsNameRejectFix:
    """When the anchor's content area starts with bank-details tokens
    (the actual name is on a different page after pdfplumber column
    flattening), the name parser must reject rather than emit
    "р/с 40701…" as a name.
    """

    def test_rs_line_rejected_as_name(self):
        text = (
            "2. СТРАХОВАТЕЛЬ / ЛИЗИНГОПОЛУЧАТЕЛЬ:\n"
            "р/с 40701810500160000472, БАНК ВТБ(ПАО), к/с 30101810700000000187, БИК 044525187\n"
            "ОБРЕМЕНЕНИЕ ТС: Договор лизинга\n"
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_name"
        )
        assert cand is None or cand.state == "not_found"

    def test_bank_keyword_at_start_rejected(self):
        text = (
            "Страхователь:\n"
            "БАНК ВТБ(ПАО), БИК 044525187\n"
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_name"
        )
        assert cand is None or cand.state == "not_found"

    def test_numbered_contract_clause_rejected(self):
        # Real corpus case (batch_1 #11): labeled anchor at end of a
        # section, followed by a contract clause "10.2. Выплата...".
        # Strict-anchor correctly picks the labeled position; the
        # reject pattern prevents the clause from being captured as a
        # name.
        text = (
            "указанная в Полисе, проверена.\nМ.П.\n"
            "Страхователь:\n"
            "10.2. Выплата по риску \"Ущерб\" производится без износа.\n"
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_name"
        )
        assert cand is None or cand.state == "not_found"

    def test_enumerated_clause_rejected(self):
        text = (
            "Страхователь:\n"
            "1) с полномочиями представителя ознакомлен\n"
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_name"
        )
        assert cand is None or cand.state == "not_found"


class TestTableRowGrouping:
    """When a single pdfplumber table contains multiple parties
    (broker / страхователь / страховщик / лизингодатель), parsers must
    only extract sub-fields from rows that belong to the policyholder.
    """

    def test_broker_email_does_not_leak_into_policyholder(self):
        tables = [
            [_BROKER_TABLE_PREFIX + [
                ["Страхователь", 'ООО "Альфа"'],
                ["Email", "contact@alpha.ru"],
            ]]
        ]
        cand = run_extraction("", tables=tables).additional_fields.get(
            "policyholder_emails"
        )
        assert cand is not None and cand.state == "found"
        assert "online@on-linebroker.ru" not in (cand.value or [])
        assert "contact@alpha.ru" in (cand.value or [])

    def test_broker_phone_does_not_leak_into_policyholder(self):
        tables = [
            [_BROKER_TABLE_PREFIX + [
                ["Страхователь", 'ООО "Альфа"'],
                ["Телефон", "+7 (495) 111-22-33"],
            ]]
        ]
        cand = run_extraction("", tables=tables).additional_fields.get(
            "policyholder_phones"
        )
        assert cand is not None
        assert "+74951112233" in (cand.value or [])
        # 8-800-200-0-900 from the broker row must NOT be there.
        normalized_broker = "+78002000900"
        assert normalized_broker not in (cand.value or [])

    def test_broker_address_does_not_leak_into_policyholder(self):
        tables = [
            [_BROKER_TABLE_PREFIX + [
                ["Страхователь", 'ООО "Альфа"'],
                ["Адрес", "101000, г. Москва, ул. Ленина, д. 1"],
            ]]
        ]
        cand = run_extraction("", tables=tables).additional_fields.get(
            "policyholder_address"
        )
        assert cand is not None
        assert "Ленина" in cand.value
        assert "Люберцы" not in cand.value

    def test_broker_inn_does_not_leak_into_policyholder(self):
        tables = [
            [
                [
                    ["Брокер", 'ООО "Онлайн-брокер"'],
                    ["ИНН", "5027200767"],  # broker's, would have failed checksum anyway
                    ["Страхователь", 'ООО "Альфа"'],
                    ["ИНН", _VALID_INN_10],
                ]
            ]
        ]
        cand = run_extraction("", tables=tables).additional_fields.get(
            "policyholder_inn"
        )
        assert cand is not None
        assert cand.value == _VALID_INN_10

    def test_strakhovshchik_rows_close_policyholder_range(self):
        tables = [
            [
                [
                    ["Страхователь", 'ООО "Альфа"'],
                    ["Email", "contact@alpha.ru"],
                    ["Страховщик", "ПАО СК Дельта"],
                    ["Email", "support@insurer.ru"],
                ]
            ]
        ]
        cand = run_extraction("", tables=tables).additional_fields.get(
            "policyholder_emails"
        )
        assert cand is not None
        assert "contact@alpha.ru" in (cand.value or [])
        assert "support@insurer.ru" not in (cand.value or [])

    def test_postal_code_from_policyholder_row_only(self):
        tables = [
            [_BROKER_TABLE_PREFIX + [
                ["Страхователь", 'ООО "Альфа"'],
                ["Адрес", "295051, Республика Крым, г. Симферополь"],
            ]]
        ]
        cand = run_extraction("", tables=tables).additional_fields.get(
            "policyholder_postal_code"
        )
        assert cand is not None
        # 140002 is the broker's; must NOT be picked.
        assert cand.value == "295051"


class TestPolicyholderTableRowsHelper:
    """Direct unit tests on the new helper."""

    def test_empty_table_returns_empty(self):
        assert policyholder_table_rows([]) == []
        assert policyholder_table_rows(None) == []

    def test_no_anchor_returns_empty(self):
        table = [["Брокер", "X"], ["Email", "x@y.ru"]]
        assert policyholder_table_rows(table) == []

    def test_returns_rows_from_anchor_to_next_party(self):
        table = [
            ["Брокер", "X"],
            ["Email", "broker@example.com"],
            ["Страхователь", "Y"],
            ["Email", "y@example.com"],
            ["Страховщик", "Z"],
            ["Email", "z@example.com"],
        ]
        result = policyholder_table_rows(table)
        assert len(result) == 2
        assert result[0][0] == "Страхователь"
        assert result[1][1] == "y@example.com"

    def test_returns_to_end_when_no_other_party_after(self):
        table = [
            ["Брокер", "X"],
            ["Страхователь", "Y"],
            ["Email", "y@example.com"],
            ["Адрес", "Москва"],
        ]
        result = policyholder_table_rows(table)
        assert len(result) == 3


class TestNameTableNoBleed:
    """Even with row-grouping enforced, the name parser itself uses
    the row whose first cell matches the Страхователь label — so it
    was already safe. This test guards against future regression.
    """

    def test_name_parser_picks_policyholder_label_row(self):
        tables = [
            [_BROKER_TABLE_PREFIX + [
                ["Страхователь", 'ООО "Альфа"'],
            ]]
        ]
        cand = run_extraction("", tables=tables).additional_fields.get(
            "policyholder_name"
        )
        assert cand is not None
        assert cand.value == 'ООО "Альфа"'
