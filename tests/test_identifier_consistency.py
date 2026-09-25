"""Tests for the cross-field ИНН / ОГРН / КПП plausibility pass.

Identifiers are generated with valid checksums so the tests exercise
the reconciliation logic, not the parsers' checksum gates.
"""

from __future__ import annotations

from polis_recognizer.extraction import run_extraction
from polis_recognizer.extraction.candidates import Candidate, ConfidenceComponents
from polis_recognizer.extraction.consistency import (
    INN,
    KPP,
    OGRN,
    pair_conflicts,
    reconcile_identifiers,
)
from polis_recognizer.extraction.validators import (
    inn_region,
    kpp_region,
    ogrn_region,
    validate_inn_10,
    validate_kpp,
    validate_ogrn_13,
)


def _inn10(region: str, serial: int = 1) -> str:
    body = f"{region}14{serial:05d}"
    weights = (2, 4, 10, 3, 5, 9, 4, 6, 8)
    check = sum(int(body[i]) * weights[i] for i in range(9)) % 11 % 10
    return body + str(check)


def _ogrn13(region: str, serial: int = 1) -> str:
    body = f"102{region}{serial:07d}"
    return body + str(int(body) % 11 % 10)


def _kpp(region: str) -> str:
    return f"{region}1401001"


def _cand(value: str, conf: float = 0.9) -> Candidate:
    return Candidate(
        value=value,
        state="found",
        pattern_id="anchored_text",
        source_fragment="",
        components=ConfidenceComponents(pattern_strength=conf),
    )


class TestGenerators:
    def test_generated_ids_are_valid(self):
        assert validate_inn_10(_inn10("78"))
        assert validate_ogrn_13(_ogrn13("78"))
        assert validate_kpp(_kpp("78"))


class TestValidateKpp:
    def test_accepts_regular_kpp(self):
        assert validate_kpp("770701001")

    def test_accepts_latin_reason_code(self):
        assert validate_kpp("7707AB001")

    def test_rejects_zero_region(self):
        assert not validate_kpp("000101001")

    def test_rejects_zero_reason(self):
        assert not validate_kpp("770700001")

    def test_rejects_wrong_length_or_chars(self):
        assert not validate_kpp("77070100")
        assert not validate_kpp("77О701001")  # Cyrillic О


class TestRegions:
    def test_region_extractors(self):
        assert inn_region(_inn10("47")) == "47"
        assert ogrn_region(_ogrn13("47")) == "47"
        assert kpp_region(_kpp("47")) == "47"

    def test_kind_conflict_inn12_with_kpp(self):
        hard, _ = pair_conflicts(INN, "500100732259", KPP, _kpp("50"))
        assert hard


class TestReconcile:
    def test_majority_swaps_outlier_inn(self):
        wrong, right = _inn10("78"), _inn10("47")
        ogrn, kpp = _cand(_ogrn13("47")), _cand(_kpp("47"))
        inn_wrong, inn_right = _cand(wrong), _cand(right)
        out = reconcile_identifiers(
            {INN: inn_wrong, OGRN: ogrn, KPP: kpp},
            {INN: [inn_wrong, inn_right], OGRN: [ogrn], KPP: [kpp]},
        )
        assert out[INN].value == right
        assert f"consistency_reranked_over:{wrong}" in out[INN].notes

    def test_two_fields_are_annotated_not_swapped(self):
        # Only ИНН + КПП: we can't tell which is wrong, so no swap.
        inn_78, inn_47 = _cand(_inn10("78")), _cand(_inn10("47"))
        kpp = _cand(_kpp("47"))
        out = reconcile_identifiers(
            {INN: inn_78, KPP: kpp},
            {INN: [inn_78, inn_47], KPP: [kpp]},
        )
        assert out[INN] is inn_78
        assert "identifier_region_mismatch:inn/kpp" in inn_78.notes
        assert "identifier_region_mismatch:inn/kpp" in kpp.notes

    def test_no_swap_without_agreeing_alternative(self):
        inn = _cand(_inn10("78"))
        ogrn, kpp = _cand(_ogrn13("47")), _cand(_kpp("47"))
        out = reconcile_identifiers(
            {INN: inn, OGRN: ogrn, KPP: kpp},
            {INN: [inn], OGRN: [ogrn], KPP: [kpp]},
        )
        assert out[INN] is inn
        assert "identifier_region_mismatch:inn/ogrn" in inn.notes

    def test_consistent_triple_untouched(self):
        inn, ogrn, kpp = _cand(_inn10("47")), _cand(_ogrn13("47")), _cand(_kpp("47"))
        out = reconcile_identifiers(
            {INN: inn, OGRN: ogrn, KPP: kpp},
            {INN: [inn], OGRN: [ogrn], KPP: [kpp]},
        )
        assert out == {}
        assert inn.notes == [] and inn.confidence == 0.9

    def test_hard_conflict_lowers_confidence(self):
        inn12, kpp = _cand("500100732259"), _cand(_kpp("50"))
        out = reconcile_identifiers({INN: inn12, KPP: kpp}, {INN: [inn12], KPP: [kpp]})
        assert "identifier_kind_conflict:inn/kpp" in out[INN].notes
        assert out[INN].confidence < 0.9
        assert out[KPP].confidence < 0.9

    def test_single_field_is_noop(self):
        inn = _cand(_inn10("47"))
        assert reconcile_identifiers({INN: inn}, {INN: [inn]}) == {}


class TestPipeline:
    def test_leasing_inn_replaced_by_policyholder_inn(self):
        # Roadmap L2 shape: the lessor's line (no «Лизингодатель»
        # label) sits inside the block and its ИНН comes first. ОГРН +
        # КПП both point at region 47, as does the second ИНН.
        lessor_inn, holder_inn = _inn10("78"), _inn10("47")
        text = (
            "Страхователь: ООО \"Экология-Норд\"\n"
            f"ЗАО «Альянс-Лизинг» ИНН {lessor_inn} РЕЗИДЕНТ РФ ДА НЕТ\n"
            f"ИНН {holder_inn} ОГРН {_ogrn13('47')} КПП {_kpp('47')}\n"
        )
        result = run_extraction(text)
        winner = result.additional_fields[INN]
        assert winner.value == holder_inn
        # The ranker alone picked the lessor's ИНН; the pass overrode it.
        assert f"consistency_reranked_over:{lessor_inn}" in winner.notes
        diag = next(d for d in result.diagnostics if d["field"] == INN)
        assert diag["winner"]["value"] == holder_inn
        assert "consistency_penalty" in diag["winner"]["components"]

    def test_invalid_kpp_is_rejected(self):
        text = "Страхователь: ООО Альфа\nКПП 000101001\n"
        cand = run_extraction(text).additional_fields.get(KPP)
        assert cand is None or cand.state == "not_found"
