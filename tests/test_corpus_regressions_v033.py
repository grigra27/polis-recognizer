"""Regression tests for 0.3.3 — address form-mask cleanup.

XLS form-mask polises (АльфаСтрахование and similar) join the address
cell with adjacent form-field cells after pdfplumber column flattening,
producing values like "422774, РТ, Пестречинский район, …, дом No 2
ДАТА РОЖД. 21.02.1966 ПОЛ М ТЕЛ". Same fix idea as the name parser's
table-cell stop in 0.3.1: apply the in-text stop regex to the joined
table value too, and add abbreviated form-mask labels to the stop set.
"""

from __future__ import annotations

from polis_recognizer.extraction import run_extraction


class TestAddressTableCellStop:
    def test_truncates_at_data_rozhd(self):
        tables = [
            [
                [
                    ["Страхователь", "ИП Саакян"],
                    [
                        "АДРЕС:",
                        "422774, РТ, Пестречинский район, с. Богородское, ул. Новая, д. 2 ДАТА РОЖД. 21.02.1966 ПОЛ М ТЕЛ",
                    ],
                ]
            ]
        ]
        cand = run_extraction(
            "Страхователь: ИП Саакян", tables=tables
        ).additional_fields.get("policyholder_address")
        assert cand is not None and cand.state == "found"
        assert "ДАТА РОЖД" not in cand.value
        assert "ПОЛ" not in cand.value
        assert "ТЕЛ" not in cand.value
        assert "Богородское" in cand.value
        assert "ул. Новая" in cand.value

    def test_truncates_at_rezident(self):
        tables = [
            [
                [
                    ["Страхователь", 'ООО "Альфа"'],
                    [
                        "Адрес",
                        "101000, г. Москва, ул. Ленина, д. 1 РЕЗИДЕНТ РФ ДА НЕТ",
                    ],
                ]
            ]
        ]
        cand = run_extraction(
            "Страхователь: ООО Альфа", tables=tables
        ).additional_fields.get("policyholder_address")
        assert cand is not None
        assert "РЕЗИДЕНТ" not in cand.value
        assert "Ленина" in cand.value

    def test_truncates_at_inline_phone_field(self):
        tables = [
            [
                [
                    ["Страхователь", "ИП Петров"],
                    [
                        "Адрес",
                        "190000, г. Санкт-Петербург, Невский пр. 1 ТЕЛ 89211234567",
                    ],
                ]
            ]
        ]
        cand = run_extraction(
            "Страхователь: ИП Петров", tables=tables
        ).additional_fields.get("policyholder_address")
        assert cand is not None
        assert "ТЕЛ" not in cand.value
        assert "89211234567" not in cand.value
        assert "Невский" in cand.value

    def test_clean_table_address_passes_through(self):
        # No form-mask debris → value preserved.
        tables = [
            [
                [
                    ["Страхователь", 'ООО "Гамма"'],
                    ["Адрес", "101000, г. Москва, ул. Тверская, д. 7"],
                ]
            ]
        ]
        cand = run_extraction(
            "Страхователь: ООО Гамма", tables=tables
        ).additional_fields.get("policyholder_address")
        assert cand is not None
        assert cand.value == "101000, г. Москва, ул. Тверская, д. 7"

    def test_in_text_address_with_data_rozhd_label(self):
        # The same stop should work in the in-text path too — abbreviated
        # "ДАТА РОЖД" is added to _ADDRESS_STOP_RE.
        text = (
            "Страхователь: ИП Саакян Самвел Аршакович\n"
            "Адрес: 422774, РТ, с. Богородское ДАТА РОЖД 21.02.1966\n"
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_address"
        )
        assert cand is not None
        assert "ДАТА РОЖД" not in cand.value
        assert "Богородское" in cand.value
