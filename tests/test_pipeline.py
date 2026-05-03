"""Pipeline-level + parser-level coverage for the v2 extraction layer.

These tests run ``run_extraction()`` against either focused fragments
or the full production-like КАСКО text and assert end-to-end behavior:
the right value reaches the legacy contract field shape, additional
fields surface for premium/sum_type, confidence reflects the
appropriate pattern strength, and the ranker picks correctly.

Backward-compatibility is asserted separately: the public facade
``ContractFieldExtractor.extract_contract_fields()`` must still produce
``ContractFieldsResult.to_dict()`` with the exact 6-key shape.
"""

from __future__ import annotations

import pytest

from polis_recognizer.contract_field_extractor import ContractFieldExtractor
from polis_recognizer.extraction import run_extraction


REAL_KASKO = """Серия 2022 № 0364420 / 26ТФ от 18.02.2026
Полис страхования транспортного средства

Транспортное средство (ТС)
ПТС/Свидетельство о регистрации: VIN/№ кузова:Серия 77УО № 564607 WDD2050401R414391
Марка, модель: MERCEDES-BENZ C 180 Год выпуска: 2018

Страховая сумма по рискам «Автокаско», «Ущерб»  -  агрегатная - нет
Безусловная франшиза по рискам «Автокаско», «Ущерб», «ДО»
в размере - нет
Урегулирование без справок на особых условиях - нет
Динамическая франшиза  (п. 5.4 Правил) - нет

Срок действия Полиса: с 00:00 11.03.2026 по 23:59 10.03.2027

 Валюта Полиса: рубль экв. долл. США экв. евроСтраховые риски Страховая сумма Страховая премия
Автокаско (Ущерб и Угон) 2000000,00 41245,50
1900,00Гражданская ответственность (ГО) 1000000,00
ИТОГО: 43145,50

Ремонт ТС осуществляется на СТОА официального дилера по направлению Страховщика, за исключением ремонта и замены стекол.

• при подаче заявления на выплату страхового возмещения необходимо предоставить пакет документов (в соответствии с характером
произошедшего события) для рассмотрения в один из офисов урегулирования убытков (подробнее
см. на сайте www.soglasie.ru)
"""


@pytest.fixture(scope="module")
def v2_result():
    return run_extraction(REAL_KASKO)


class TestPolicyPeriodParser:
    def test_label_anchored_pattern_wins_over_generic(self, v2_result):
        cand = v2_result.legacy_fields["policy_period"]
        assert cand.state == "found"
        assert cand.value == {"start": "2026-03-11", "end": "2027-03-10"}
        assert cand.pattern_id == "label_anchored"
        assert cand.confidence >= 0.9


class TestFranchiseParser:
    def test_recognizes_v_razmere_net_as_absent(self, v2_result):
        cand = v2_result.legacy_fields["franchise"]
        assert cand.state == "absent"
        assert cand.value["absent"] is True
        assert cand.value["value"] == 0
        assert cand.pattern_id == "absent_v_razmere_net"
        assert cand.confidence >= 0.9

    def test_alfa_kasko_polnoe_inline_zero_treated_as_absent(self):
        # АльфаСтрахование XLS form: КАСКО ПОЛНОЕ row with three columns
        # (sum / franchise=0 / premium). The literal "0,00" in the
        # franchise column means "no franchise" — same semantic as the
        # existing 0-руб patterns, just stripped of the "руб" suffix.
        text = (
            "Полис №\n"
            "1. КАСКО ПОЛНОЕ (ПОВРЕЖДЕНИЕ, ХИЩЕНИЕ) 5 525 000,00 0,00 220 000,00\n"
        )
        result = run_extraction(text)
        cand = result.legacy_fields["franchise"]
        assert cand.state == "absent"
        assert cand.value["absent"] is True
        assert cand.pattern_id.startswith("alfa_kasko_polnoe_inline")

    def test_alfa_kasko_polnoe_inline_positive_value(self):
        text = (
            "Полис №\n"
            "1. КАСКО ПОЛНОЕ (ПОВРЕЖДЕНИЕ, ХИЩЕНИЕ) 12 060 000,00 50 000,00 256 878,00\n"
        )
        result = run_extraction(text)
        cand = result.legacy_fields["franchise"]
        assert cand.state == "found"
        assert cand.value == {"value": 50000.0, "currency": "RUB"}
        assert cand.pattern_id == "alfa_kasko_polnoe_inline"

    def test_alfa_kasko_polnoe_inline_ne_ustanovlena_is_absent(self):
        text = (
            "с 1. КАСКО ПОЛНОЕ (ПОВРЕЖДЕНИЕ, ХИЩЕНИЕ) "
            "5 700 000.00 Не установлена 136 800.00\n"
        )
        result = run_extraction(text)
        cand = result.legacy_fields["franchise"]
        assert cand.state == "absent"
        assert cand.pattern_id == "alfa_kasko_polnoe_inline:placeholder_as_absent"

    def test_alfa_kasko_polnoe_inline_two_column_does_not_misread_premium(self):
        # 2-column variant (sum + premium, no franchise column). The
        # atomic-group anchor must reject this so we don't mis-read the
        # 7-figure premium as a giant franchise.
        text = (
            "1. КАСКО ПОЛНОЕ (ПОВРЕЖДЕНИЕ, ХИЩЕНИЕ) 6 500 000.00 220 000.00\n"
        )
        result = run_extraction(text)
        cand = result.legacy_fields["franchise"]
        assert cand.state == "not_found"

    def test_alfa_kasko_polnoe_table_franchise_zero_is_absent(self):
        # pdfplumber-shaped table input: per-page list of tables, each
        # table is rows×cells. The first non-text cells on the КАСКО
        # ПОЛНОЕ row are sum/franchise/premium.
        tables = [[[
            ["·", "1. КАСКО ПОЛНОЕ", "·", "5 525 000,00", "·", "0,00", "·", "220 000,00"],
        ]]]
        result = run_extraction("Полис №\n", tables=tables)
        cand = result.legacy_fields["franchise"]
        assert cand.state == "absent"
        assert cand.pattern_id == "alfa_kasko_polnoe_table:zero_as_absent"

    def test_alfa_kasko_polnoe_table_franchise_value(self):
        tables = [[[
            ["·", "1. КАСКО ПОЛНОЕ", "·", "12 060 000,00", "·", "50 000,00", "·", "256 878,00"],
        ]]]
        result = run_extraction("Полис №\n", tables=tables)
        cand = result.legacy_fields["franchise"]
        assert cand.state == "found"
        assert cand.value == {"value": 50000.0, "currency": "RUB"}
        assert cand.pattern_id == "alfa_kasko_polnoe_table"

    def test_alfa_kasko_polnoe_table_franchise_placeholder(self):
        tables = [[[
            ["·", "1. КАСКО ПОЛНОЕ", "·", "5 700 000.00", "·", "Не установлена", "·", "136 800.00"],
        ]]]
        result = run_extraction("Полис №\n", tables=tables)
        cand = result.legacy_fields["franchise"]
        assert cand.state == "absent"
        assert cand.pattern_id == "alfa_kasko_polnoe_table:placeholder_as_absent"


class TestLimitParser:
    def test_uses_header_classified_autocasco_row(self, v2_result):
        cand = v2_result.legacy_fields["limit"]
        assert cand.state == "found"
        assert cand.value["value"] == 2_000_000
        assert cand.value["currency"] == "RUB"
        assert "row_autocasco" in cand.pattern_id
        assert "header_classified" in cand.pattern_id

    def test_does_not_pick_premium_or_total(self, v2_result):
        cand = v2_result.legacy_fields["limit"]
        assert cand.value["value"] not in (41245.5, 43145.5, 1_000_000)


class TestRepairModeParser:
    def test_recognizes_dealer_with_intervening_word(self, v2_result):
        cand = v2_result.legacy_fields["repair_mode"]
        assert cand.state == "found"
        assert cand.value == "dealer"
        # Even with a "ТС" between "Ремонт" and "осуществляется", the
        # explicit pattern should match (post pattern relaxation).
        assert "dealer" in cand.pattern_id

    def test_negation_only_match_does_not_promote_dealer(self):
        """Synthetic test: dealer is mentioned only inside a carve-out."""
        text = (
            "Возмещение производится в форме денежной выплаты, "
            "за исключением ремонта на СТОА официального дилера."
        )
        result = run_extraction(text)
        cand = result.legacy_fields["repair_mode"]
        # Either cash wins (correct) or dealer is so heavily penalised
        # that it has noticeably lower confidence than cash.
        assert cand.value == "cash"

    def test_recognizes_inflected_remont_without_verb(self):
        # АльфаСтрахование phrasing: "ремонта повреждённого ТС на СТОА"
        # — noun in genitive, no verb. The original regex matched only
        # "ремонт" and required \s+ around an optional verb, which
        # collapsed to "two spaces required" when the verb was absent.
        text = (
            "Страховщик возмещает путём организации и оплаты ремонта "
            "повреждённого ТС на СТОА, имеющей договорные отношения "
            "со Страховщиком."
        )
        result = run_extraction(text)
        cand = result.legacy_fields["repair_mode"]
        assert cand.state == "found"
        assert cand.value == "service"
        assert cand.pattern_id == "service_remont_stoa"

    def test_recognizes_dealer_inflected_remont_no_stoa_word(self):
        text = "путём организации ремонта у официального дилера"
        result = run_extraction(text)
        cand = result.legacy_fields["repair_mode"]
        assert cand.state == "found"
        assert cand.value == "dealer"
        assert cand.pattern_id == "dealer_explicit"


class TestAdditionalFields:
    def test_premium_extracted_from_risk_row(self, v2_result):
        cand = v2_result.additional_fields["premium"]
        assert cand.state == "found"
        assert cand.value["value"] == 41245.5
        assert cand.value["currency"] == "RUB"

    def test_premium_alfa_kasko_polnoe_inline_third_column(self):
        text = (
            "Полис №\n"
            "1. КАСКО ПОЛНОЕ (ПОВРЕЖДЕНИЕ, ХИЩЕНИЕ) "
            "5 525 000,00 0,00 220 000,00\n"
        )
        result = run_extraction(text)
        cand = result.additional_fields["premium"]
        assert cand.state == "found"
        assert cand.value["value"] == 220000.0
        assert cand.pattern_id == "alfa_kasko_polnoe_inline"

    def test_premium_alfa_kasko_polnoe_inline_after_placeholder(self):
        text = (
            "с 1. КАСКО ПОЛНОЕ (ПОВРЕЖДЕНИЕ, ХИЩЕНИЕ) "
            "5 700 000.00 Не установлена 136 800.00\n"
        )
        result = run_extraction(text)
        cand = result.additional_fields["premium"]
        assert cand.state == "found"
        assert cand.value["value"] == 136800.0
        assert cand.pattern_id == "alfa_kasko_polnoe_inline"

    def test_premium_alfa_kasko_polnoe_two_column_does_not_match_inline(self):
        # 2-column row (sum + premium, no franchise column). The inline
        # 3-column anchor requires THREE numbers; this row has two, so
        # alfa_kasko_polnoe_inline must not fire. The keyword "ИТОГО"
        # / "страховая премия" path may still pick something up — we
        # only assert that the specific inline pattern stays silent.
        text = (
            "1. КАСКО ПОЛНОЕ (ПОВРЕЖДЕНИЕ, ХИЩЕНИЕ) 6 500 000.00 220 000.00\n"
        )
        result = run_extraction(text)
        cand = result.additional_fields["premium"]
        # Either not found at all, or found via a non-inline pattern.
        if cand.state == "found":
            assert cand.pattern_id != "alfa_kasko_polnoe_inline"

    def test_sum_type_recognized_as_non_aggregate(self, v2_result):
        cand = v2_result.additional_fields["sum_type"]
        assert cand.state == "found"
        assert cand.value == "non_aggregate"
        assert cand.pattern_id == "agg_label_negated"

    def test_sum_type_alfa_bez_umensheniya_non_aggregate(self):
        # АльфаСтрахование doesn't say "агрегатная/неагрегатная" — it
        # spells the semantics: "БЕЗ УМЕНЬШЕНИЯ ... ВЫПЛАЧЕННОГО ...
        # ВОЗМЕЩЕНИЯ" means the sum stays full = non-aggregate.
        text = (
            "СТРАХОВАЯ СУММА по пп. 1, 3 и 5 БЕЗ УМЕНЬШЕНИЯ НА РАЗМЕР "
            "ВЫПЛАЧЕННОГО СТРАХОВОГО ВОЗМЕЩЕНИЯ"
        )
        result = run_extraction(text)
        cand = result.additional_fields["sum_type"]
        assert cand.state == "found"
        assert cand.value == "non_aggregate"
        assert cand.pattern_id == "alfa_bez_umensheniya"

    def test_sum_type_alfa_s_umensheniem_aggregate(self):
        text = (
            "СТРАХОВАЯ СУММА по пп. 2, 4 С УМЕНЬШЕНИЕМ НА РАЗМЕР "
            "ВЫПЛАЧЕННОГО СТРАХОВОГО ВОЗМЕЩЕНИЯ"
        )
        result = run_extraction(text)
        cand = result.additional_fields["sum_type"]
        assert cand.state == "found"
        assert cand.value == "aggregate"
        assert cand.pattern_id == "alfa_s_umensheniem"

    def test_sum_type_alfa_mixed_prefers_non_aggregate(self):
        # Real Альфа forms list both clauses for different risk points.
        # When both fire the parser should pick non_aggregate (КАСКО
        # полное standard) — encoded by the slightly higher pattern
        # strength on alfa_bez_umensheniya.
        text = (
            "СТРАХОВАЯ СУММА по пп. 2, 4 С УМЕНЬШЕНИЕМ НА РАЗМЕР "
            "ВЫПЛАЧЕННОГО СТРАХОВОГО ВОЗМЕЩЕНИЯ\n"
            "СТРАХОВАЯ СУММА по пп. 1, 3 и 5 БЕЗ УМЕНЬШЕНИЯ НА РАЗМЕР "
            "ВЫПЛАЧЕННОГО СТРАХОВОГО ВОЗМЕЩЕНИЯ"
        )
        result = run_extraction(text)
        cand = result.additional_fields["sum_type"]
        assert cand.state == "found"
        assert cand.value == "non_aggregate"


class TestContractFieldExtractorFacade:
    """The legacy public API still exposes the same to_dict() shape."""

    def test_to_dict_keeps_legacy_keys(self):
        result = ContractFieldExtractor().extract_contract_fields(REAL_KASKO)
        d = result.to_dict()
        assert set(d.keys()) == {
            "policy_period",
            "franchise",
            "limit",
            "repair_mode",
        }

    def test_franchise_absent_flag_round_trips(self):
        result = ContractFieldExtractor().extract_contract_fields(REAL_KASKO)
        franchise = result.to_dict()["franchise"]
        assert franchise.get("absent") is True
        assert franchise["value"] == 0
        assert franchise["currency"] == "RUB"

    def test_additional_fields_surface_via_diagnostics(self):
        result = ContractFieldExtractor().extract_contract_fields(REAL_KASKO)
        diag = result.to_diagnostics_payload()
        assert "additional_fields" in diag
        assert "premium" in diag["additional_fields"]
        assert "sum_type" in diag["additional_fields"]
        assert diag["additional_fields"]["premium"]["value"]["value"] == 41245.5

    def test_legacy_to_dict_still_callable_with_minimal_text(self):
        result = ContractFieldExtractor().extract_contract_fields("hello world")
        d = result.to_dict()
        # Empty text still produces all six keys with null/null/0.0/null payload.
        assert d["policy_period"] == {
            "start": None, "end": None, "confidence": 0.0, "source_fragment": None,
        }
