"""Regression tests for 0.3.4 — fixes derived from full corpus run
across digital_pdf/batch_2..7 (621 files).

10 fixes (A-J) bundled into one release. Each test corresponds to a
specific failure pattern reported by per-batch inspector agents.
"""

from __future__ import annotations

from polis_recognizer.extraction import run_extraction
from polis_recognizer.extraction.normalizer import TextNormalizer


# Public, checksum-correct fixtures.
_VALID_INN_10 = "7707083893"
_VALID_INN_12 = "500100732259"


# =========================================================================
# Fix A — soft hyphen U+00AD inside name / address
# =========================================================================

class TestSoftHyphenNormalization:
    def test_mid_word_shy_becomes_regular_hyphen(self):
        # pdfplumber emits "Прогресс\xadТех" where rendered text reads
        # "Прогресс-Тех". After normalisation we want "Прогресс-Тех".
        n = TextNormalizer().normalize("Прогресс­Тех")
        assert n.text == "Прогресс-Тех"

    def test_end_of_line_shy_joins_word(self):
        # SHY at line-break ("Прогресс\xad\nТех") should heal to the
        # single word "ПрогрессТех" — same behaviour as a regular
        # hyphen-broken word "Прогресс-\nТех".
        n = TextNormalizer().normalize("Прогресс­\nТех")
        assert n.text == "ПрогрессТех"

    def test_email_with_shy_becomes_email(self):
        n = TextNormalizer().normalize("e­mail")
        assert n.text == "e-mail"

    def test_extraction_through_shy_name(self):
        text = "Страхователь: ООО Прогресс­Тех\nИНН 7707083893\n"
        cand = run_extraction(text).additional_fields.get(
            "policyholder_name"
        )
        assert cand is not None and cand.state == "found"
        assert "­" not in cand.value
        assert cand.value == "ООО Прогресс-Тех"


# =========================================================================
# Fix B — postal_code only from leading-6 of address content
# =========================================================================

class TestPostalCodeFromAddressOnly:
    def test_vin_tail_no_longer_leaks(self):
        # batch_2 case: address has no leading index, but VIN inside
        # the block contains a 6-digit tail "270155". Previously this
        # tail surfaced as postal_code; now it must not.
        text = (
            "Страхователь: ООО Альфа\n"
            "Адрес: Россия, Приморский край, Владивосток г., Снеговая ул.\n"
            "VIN Z94C241BBSR270155\n"
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_postal_code"
        )
        assert cand is None or cand.state == "not_found"

    def test_legitimate_leading_index_still_extracted(self):
        text = (
            "Страхователь: ООО Альфа\n"
            "Адрес: 101000, г. Москва, ул. Ленина, д. 1\n"
            "VIN Z94C241BBSR270155\n"
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_postal_code"
        )
        assert cand is not None and cand.value == "101000"

    def test_postal_from_table_via_address_row_only(self):
        # postal_code extraction from a table cell — but only when the
        # cell is in an address row, AND the value starts with [1-6]
        # 6-digit run.
        tables = [
            [
                [
                    ["Страхователь", "ИП Иванов"],
                    ["Адрес", "422774, РТ, Пестречинский район"],
                    # VIN row in same table — must NOT yield postal
                    ["VIN", "Z94C241BBSR270155"],
                ]
            ]
        ]
        cand = run_extraction(
            "Страхователь: ИП Иванов", tables=tables
        ).additional_fields.get("policyholder_postal_code")
        assert cand is not None and cand.value == "422774"


# =========================================================================
# Fix C — "Наименование" / "Юридический" / "ФИО гражданина" as label
# =========================================================================

class TestLabelValueSkip:
    def test_naimenovanie_label_skipped(self):
        # SGZA / Согаз template: after "Страхователь:" the next line is
        # the label "Наименование", and the actual name is on the
        # line after that.
        text = (
            "Страхователь:\n"
            "Наименование\n"
            'ООО "МСК 777"\n'
            f"ИНН {_VALID_INN_10}\n"
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_name"
        )
        assert cand is not None and cand.state == "found"
        assert cand.value == 'ООО "МСК 777"'

    def test_polnoe_naimenovanie_label_skipped(self):
        text = (
            "Страхователь:\n"
            "Полное наименование\n"
            'АО "Дельта"\n'
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_name"
        )
        assert cand is not None
        assert cand.value == 'АО "Дельта"'

    def test_yuridicheskiy_label_skipped(self):
        text = (
            "Страхователь:\n"
            "Юридический\n"
            'ООО "Эпсилон"\n'
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_name"
        )
        assert cand is not None
        assert "Юридический" not in cand.value
        assert "Эпсилон" in cand.value


# =========================================================================
# Fix D — address stoppers extended
# =========================================================================

class TestAddressStoppers:
    def test_stops_at_fakticheskiy_adres(self):
        text = (
            "Страхователь: ООО Альфа\n"
            "Юридический адрес: 119234, Москва г, "
            "Ленинские Горы, д. 1 Фактический адрес: 119234, Москва\n"
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_address"
        )
        assert cand is not None
        assert "Фактический адрес" not in cand.value
        assert "Ленинские Горы" in cand.value

    def test_stops_at_vygodopriobretateli(self):
        text = (
            "Страхователь: ООО Тайфун\n"
            "Адрес: 420127, Казань, Дементьева ул, дом 1 "
            "Выгодоприобретатели: - по рискам «хищение»\n"
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_address"
        )
        assert cand is not None
        assert "Выгодоприобретатели" not in cand.value
        assert "Дементьева" in cand.value

    def test_stops_at_bankovskie_rekvizity(self):
        text = (
            "Страхователь: ООО Эпсилон\n"
            "Адрес: 101000, Москва, Тверская д. 1 "
            "Банковские реквизиты р/с 40701810500160000472\n"
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_address"
        )
        assert cand is not None
        assert "Банковские" not in cand.value
        assert "40701810500160000472" not in cand.value

    def test_stops_at_vin(self):
        text = (
            "Страхователь: ИП Петров\n"
            "Адрес: 295051, Симферополь, Гоголя 68 "
            "VIN Z94C241BBSR270155\n"
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_address"
        )
        assert cand is not None
        assert "VIN" not in cand.value
        assert "Z94C241BBSR270155" not in cand.value

    def test_stops_at_mob_tel(self):
        text = (
            "Страхователь: ИП Тоноян\n"
            "Адрес: 352631, Краснодарский край, Белореченск Моб.тел. +79261112233\n"
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_address"
        )
        assert cand is not None
        assert "Моб" not in cand.value
        assert "Белореченск" in cand.value


# =========================================================================
# Fix E — placeholder phone numbers rejected
# =========================================================================

class TestPlaceholderPhonesRejected:
    def test_all_ones_rejected(self):
        text = "Страхователь: ООО Альфа\nТел.: +71111111111\n"
        cand = run_extraction(text).additional_fields.get(
            "policyholder_phones"
        )
        assert cand is None or cand.state == "not_found"

    def test_all_zeros_rejected(self):
        text = "Страхователь: ООО Альфа\nТел.: +70000000000\n"
        cand = run_extraction(text).additional_fields.get(
            "policyholder_phones"
        )
        assert cand is None or cand.state == "not_found"

    def test_all_nines_rejected(self):
        text = "Страхователь: ООО Альфа\nТел.: 9999999999\n"
        cand = run_extraction(text).additional_fields.get(
            "policyholder_phones"
        )
        assert cand is None or cand.state == "not_found"

    def test_real_phone_still_accepted(self):
        text = "Страхователь: ООО Альфа\nТел.: +79261112233\n"
        cand = run_extraction(text).additional_fields.get(
            "policyholder_phones"
        )
        assert cand is not None
        assert cand.value == ["+79261112233"]


# =========================================================================
# Fix F — ИП / Индивидуальный предприниматель → individual
# =========================================================================

class TestIPClassifiedAsIndividual:
    def test_ip_abbreviation_is_individual(self):
        text = "Страхователь: ИП Саакян Самвел Аршакович\nИНН 163400896388\n"
        cand = run_extraction(text).additional_fields.get(
            "policyholder_type"
        )
        assert cand is not None and cand.value == "individual"
        assert cand.pattern_id == "ip_prefix"

    def test_full_form_individualnyy_is_individual(self):
        text = "Страхователь: Индивидуальный предприниматель Еремин Илья\n"
        cand = run_extraction(text).additional_fields.get(
            "policyholder_type"
        )
        assert cand is not None and cand.value == "individual"
        assert cand.pattern_id == "ip_prefix"

    def test_ip_does_not_flip_ooo_classification(self):
        text = 'Страхователь: ООО "Альфа"\n'
        cand = run_extraction(text).additional_fields.get(
            "policyholder_type"
        )
        assert cand is not None and cand.value == "legal_entity"


# =========================================================================
# Fix G — disclaimer prose rejected (extension of 0.3.3 prose-anchor logic)
# =========================================================================

class TestDisclaimerProseRejected:
    def test_informatsiya_proverena_rejected(self):
        # batch_7 / batch_5 / batch_6 case — labeled "Страхователь:"
        # anchor at the end of a disclaimer block, captured value is
        # the disclaimer text.
        text = (
            "Заключая полис, Страхователь подтверждает, что Правила страхования получил.\n"
            "Страхователь:\n"
            "Информация, указанная в Полисе, проверена и подтверждается. "
            "Страхователь и его представитель ознакомлены.\n"
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_name"
        )
        assert cand is None or cand.state == "not_found"

    def test_proinformirovan_substring_rejected(self):
        text = (
            "Страхователь:\n"
            "получил, полностью проинформирован об условиях страхования.\n"
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_name"
        )
        assert cand is None or cand.state == "not_found"


# =========================================================================
# Fix H — signature / footer rejected
# =========================================================================

class TestSignatureFooterRejected:
    def test_podpis_rejected(self):
        text = (
            "Страхователь:\n"
            'Подпись ООО «АЛЬЯНС-М»\n'
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_name"
        )
        assert cand is None or cand.state == "not_found"

    def test_identifikator_dokumenta_rejected(self):
        text = (
            "Страхователь:\n"
            "Идентификатор документа d08fd776-aaaa-bbbb-cccc-dddddddddddd\n"
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_name"
        )
        assert cand is None or cand.state == "not_found"

    def test_signature_lead_in_slash_rejected(self):
        text = (
            "Страхователь:\n"
            "/ Губин Ю.И. / «16» апреля 2024 г.\n"
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_name"
        )
        assert cand is None or cand.state == "not_found"

    def test_policy_number_in_name_slot_rejected(self):
        text = (
            "Страхователь:\n"
            "No 2037207-1036257/24ИМЮЛ от «24» апреля 2024г\n"
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_name"
        )
        assert cand is None or cand.state == "not_found"


# =========================================================================
# Fix I — multi-line legal name continuation
# =========================================================================

class TestMultiLineLegalName:
    def test_continues_past_oo_with_quoted_brand_on_next_line(self):
        # batch_5 case: pdfplumber emits the form on one line and the
        # quoted brand on the next. Both belong to the same name.
        text = (
            "Страхователь: ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ\n"
            '"ИНФОКАР"\n'
            f"ИНН {_VALID_INN_10}\n"
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_name"
        )
        assert cand is not None and cand.state == "found"
        assert "ИНФОКАР" in cand.value
        assert "ОБЩЕСТВО" in cand.value

    def test_continues_when_form_without_brand(self):
        # No quoted brand yet — next line carries the brand name.
        text = (
            "Страхователь: Общество с ограниченной\n"
            'ответственностью "ТехноСнабСервис"\n'
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_name"
        )
        assert cand is not None
        assert "ТехноСнабСервис" in cand.value

    def test_continues_on_unbalanced_quote(self):
        # Opening quote without closing — actual closing quote on the
        # next line.
        text = (
            'Страхователь: ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ "СПЕЦИАЛИЗИРОВАННОЕ\n'
            'СТРОИТЕЛЬНОЕ УПРАВЛЕНИЕ-4"\n'
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_name"
        )
        assert cand is not None
        assert "СПЕЦИАЛИЗИРОВАННОЕ" in cand.value
        assert "СТРОИТЕЛЬНОЕ" in cand.value

    def test_does_not_continue_when_name_complete(self):
        # Properly closed quotes — no continuation should happen.
        text = (
            'Страхователь: ООО "Альфа"\n'
            f"ИНН {_VALID_INN_10}\n"
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_name"
        )
        assert cand is not None
        assert cand.value == 'ООО "Альфа"'
        # ИНН line must NOT have been pulled into the name.
        assert "ИНН" not in cand.value


# =========================================================================
# Fix J — "Адрес страхователя:" anchor recognised as full label
# =========================================================================

class TestAdresStrakhovatelyaAnchor:
    def test_strakhovatelya_not_captured_as_address_value(self):
        text = (
            "Страхователь: ООО Консерв-трейд\n"
            "Адрес страхователя: 455000, Челябинская обл, Магнитогорск\n"
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_address"
        )
        assert cand is not None
        assert cand.value != "страхователя"
        assert "Магнитогорск" in cand.value

    def test_adres_mesta_nakhozhdeniya_anchor(self):
        text = (
            "Страхователь: ООО Дельта\n"
            "Адрес места нахождения: 180016, Псковская обл\n"
        )
        cand = run_extraction(text).additional_fields.get(
            "policyholder_address"
        )
        assert cand is not None
        assert "180016" in cand.value
        assert "Псковская" in cand.value
