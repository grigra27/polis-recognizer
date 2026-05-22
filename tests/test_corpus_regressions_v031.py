"""Regression tests for 0.3.1 — fixes derived from real corpus inspection.

Each test fixes a specific failure observed when running 0.3.0 against
the digital_pdf/batch_1 batch of the training corpus. Test text is
synthesised from the failing block layouts, not lifted from the
corpus directly.
"""

from __future__ import annotations

from polis_recognizer.extraction import run_extraction
from polis_recognizer.extraction.normalizer import TextNormalizer
from polis_recognizer.extraction.policyholder_block import (
    _anchor_label_score,
    locate_policyholder_block,
)


_VALID_INN_10 = "7707083893"
_VALID_OGRN_13 = "1027700132195"


class TestBlockEndStoppersFix:
    """Block must not bleed into СОБСТВЕННИК / ЛИЗИНГОДАТЕЛЬ / ОБРЕМЕНЕНИЕ.

    Real example (batch_1 #13-15): after the policyholder block there's
    a "Собственник ЗАО «Альянс-Лизинг» ..." section. Without an explicit
    stopper, the locator runs past it and the next regex on ИНН/ОГРН
    pulls the lizingodatel's data.
    """

    def test_block_stops_at_sobstvennik(self):
        text = (
            "СТРАХОВАТЕЛЬ: ООО \"ПМК 77\"\n"
            "Адрес регистрации: Москва г, Троицк р-н\n"
            "ИНН: 7751199551 ОГРН: 1217700278630\n"
            "Собственник ЗАО «Альянс-Лизинг»\n"
            "ИНН 7825496985 КПП 781401001\n"
        )
        block = locate_policyholder_block(TextNormalizer().normalize(text))
        assert block is not None
        block_text = text[block[0] : block[1]]
        assert "ПМК 77" in block_text
        assert "Альянс-Лизинг" not in block_text
        assert "7825496985" not in block_text

    def test_block_stops_at_obremenenie(self):
        text = (
            "2. СТРАХОВАТЕЛЬ / ЛИЗИНГОПОЛУЧАТЕЛЬ:\n"
            "ООО Альфа\n"
            "ИНН 7707083893\n"
            "ОБРЕМЕНЕНИЕ ТС: Договор лизинга No 20322-ЛА\n"
            "ИНН 9999999999\n"
        )
        block = locate_policyholder_block(TextNormalizer().normalize(text))
        assert block is not None
        block_text = text[block[0] : block[1]]
        assert "7707083893" in block_text
        assert "9999999999" not in block_text


class TestBankLineGuardFix:
    """ОГРН / КПП on a bank-details line are lizingodatel's, not
    policyholder's. Real example (batch_1 #10):

        р/с 40702810812... в Ф. ОПЕРУ БАНКА ВТБ (ПАО) В САНКТ-ПЕТЕРБУРГЕ,
        К/с 30101810200000000704, БИК 044030704,
        ОГРН 1074705005484, КПП 470501001
    """

    def test_ogrn_on_bank_line_is_rejected(self):
        text = (
            "СТРАХОВАТЕЛЬ: ООО Альфа\n"
            "р/с 40702810812000003807 в Ф. ОПЕРУ БАНКА ВТБ (ПАО), "
            "К/с 30101810200000000704, БИК 044030704, "
            "ОГРН 1074705005484, КПП 470501001\n"
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_ogrn"
        )
        assert cand is None or cand.state == "not_found"

    def test_kpp_on_bank_line_is_rejected(self):
        text = (
            "СТРАХОВАТЕЛЬ: ООО Альфа\n"
            "р/с 40702810812000003807, БИК 044030704, КПП 470501001\n"
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_kpp"
        )
        assert cand is None or cand.state == "not_found"

    def test_ogrn_on_clean_line_still_works(self):
        # The bank-line guard must NOT poison the normal case.
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


class TestStrictAnchorFix:
    """Anchor in document prose (e.g. "Страхователь подтверждает, что
    Правила страхования получил…") must NOT win over a labeled anchor
    elsewhere in the same document.

    Real example (batch_1 #9, #11): a leading prose sentence has the
    word "Страхователь" before the labeled "Страхователь:" field
    appears. With the original first-match rule we extracted
    "подтверждает, что Правила страхования" as the name.
    """

    def test_label_anchor_wins_over_prose(self):
        text = (
            "Заключая настоящий Полис, Страхователь подтверждает, "
            "что Правила страхования получил.\n"
            "\n"
            "Страхователь: ООО Альфа\n"
            f"ИНН {_VALID_INN_10}\n"
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_name"
        )
        assert cand is not None and cand.state == "found"
        assert cand.value == "ООО Альфа"
        assert "подтверждает" not in cand.value

    def test_numbered_list_anchor_wins_over_prose(self):
        text = (
            "Страхователь подтверждает, что Правила получил.\n"
            "1. СТРАХОВАТЕЛЬ ООО Бета\n"
            f"ИНН {_VALID_INN_10}\n"
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_name"
        )
        assert cand is not None and cand.state == "found"
        assert cand.value == "ООО Бета"

    def test_prose_only_anchor_still_returns_something(self):
        # Fallback path: if NO labeled anchor exists, we still take the
        # prose match — better to extract a low-confidence candidate
        # than nothing, and the ranker can downweight it.
        text = "Заключая полис, Страхователь подтверждает, что условия понятны."
        # Anchor score should be 0 (prose), but block should still
        # locate.
        block = locate_policyholder_block(TextNormalizer().normalize(text))
        assert block is not None

    def test_label_score_assignments(self):
        # Direct unit-level checks on the scoring helper.
        n = TextNormalizer().normalize(
            "Страхователь: ООО Альфа\n"
            "Заключая полис, Страхователь подтверждает\n"
            "1. Страхователь ООО Бета\n"
        )
        # Line 1 anchor — at start of line.
        idx_a = n.text.index("Страхователь")
        # Line 2 anchor — in prose.
        idx_b = n.text.index("Страхователь", idx_a + 1)
        # Line 3 anchor — after "1. " numbered prefix.
        idx_c = n.text.index("Страхователь", idx_b + 1)
        assert _anchor_label_score(n.text, idx_a) == 2
        assert _anchor_label_score(n.text, idx_b) == 0
        assert _anchor_label_score(n.text, idx_c) == 2


class TestNameTableCellStopFix:
    """pdfplumber XLS form-mask polises join cells like
    ("ИП Саакян Самвел Аршакович", "ИНН 163400896388",
     "РЕЗИДЕНТ РФ", "ДА", "НЕТ") into a single value string.
    The name parser must truncate at the first known subfield label.

    Real example (batch_1 #4):
        name = 'ИП Саакян Самвел Аршакович ИНН 163400896388 РЕЗИДЕНТ РФ ДА НЕТ'
    """

    def test_table_cell_value_truncates_at_inn_label(self):
        tables = [
            [
                [
                    [
                        "Страхователь",
                        "ИП Саакян Самвел Аршакович ИНН 163400896388 РЕЗИДЕНТ РФ ДА НЕТ",
                    ]
                ]
            ]
        ]
        cand = run_extraction("", tables=tables).additional_fields.get(
            "policyholder_name"
        )
        assert cand is not None and cand.state == "found"
        assert cand.value == "ИП Саакян Самвел Аршакович"
        assert "ИНН" not in cand.value
        assert "РЕЗИДЕНТ" not in cand.value

    def test_table_cell_value_truncates_at_kpp_label(self):
        tables = [
            [
                [
                    [
                        "Страхователь",
                        'ООО "ПЛОДООБЪЕДИНЕНИЕ" КПП 263001001',
                    ]
                ]
            ]
        ]
        cand = run_extraction("", tables=tables).additional_fields.get(
            "policyholder_name"
        )
        assert cand is not None
        assert "КПП" not in cand.value
        assert "ПЛОДООБЪЕДИНЕНИЕ" in cand.value

    def test_clean_table_cell_value_passes_through(self):
        # No subfield label inside → value preserved unchanged.
        tables = [[[["Страхователь", 'ООО "Альфа"']]]]
        cand = run_extraction("", tables=tables).additional_fields.get(
            "policyholder_name"
        )
        assert cand is not None
        assert cand.value == 'ООО "Альфа"'


class TestPostalCodeFromTablesFix:
    """When the address only lives in a table (XLS form-mask polises),
    the block text often has no postal index. The postal parser must
    fall back to anchored-table scanning.
    """

    def test_postal_extracted_from_anchored_table(self):
        tables = [
            [
                [
                    ["Страхователь", 'ИП Саакян'],
                    [
                        "Адрес",
                        "422774, РТ, Пестречинский район, с. Богородское",
                    ],
                ]
            ]
        ]
        cand = run_extraction(
            "Страхователь: ИП Саакян Самвел Аршакович",
            tables=tables,
        ).additional_fields.get("policyholder_postal_code")
        assert cand is not None and cand.state == "found"
        assert cand.value == "422774"

    def test_table_postal_ignored_without_policyholder_anchor(self):
        tables = [
            [
                [
                    ["Страховщик", "ПАО СК Дельта"],
                    ["Адрес", "119992, г. Москва"],
                ]
            ]
        ]
        cand = run_extraction("", tables=tables).additional_fields.get(
            "policyholder_postal_code"
        )
        assert cand is None or cand.state == "not_found"
