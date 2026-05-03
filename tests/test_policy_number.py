"""Stage D — policy_number parser tests."""

from __future__ import annotations

import pytest

from polis_recognizer.extraction import run_extraction


KASKO_HEADER = """Серия 2022 № 0364420 / 26ТФ от 18.02.2026
Полис страхования транспортного средства
ПТС/Свидетельство о регистрации: VIN/№ кузова:Серия 77УО № 564607 WDD2050401R414391
"""


class TestPolicyNumberParser:
    def test_series_number_pattern_extracts_structured_payload(self):
        result = run_extraction(KASKO_HEADER)
        cand = result.additional_fields.get("policy_number")
        assert cand is not None
        assert cand.state == "found"
        assert cand.pattern_id == "series_number"
        assert cand.value == {
            "display": "2022 / 0364420 / 26ТФ",
            "series": "2022",
            "number": "0364420",
            "suffix": "26ТФ",
        }
        assert cand.confidence >= 0.9

    def test_pts_series_with_cyrillic_letters_is_ignored(self):
        # The KASKO header carries both:
        #   "Серия 2022 № 0364420" — polis identifier (4-digit series)
        #   "Серия 77УО № 564607"  — PTS serial (digits + cyrillic)
        # The strong pattern requires 4 digits in the series field so
        # the PTS line must NOT match.
        result = run_extraction("Серия 77УО № 564607 WDD2050401R414391")
        cand = result.additional_fields.get("policy_number")
        assert cand is None or cand.state == "not_found"

    def test_polis_keyword_pattern_fallback(self):
        text = "Полис страхования № AB-1234567"
        result = run_extraction(text)
        cand = result.additional_fields.get("policy_number")
        assert cand is not None
        assert cand.state == "found"
        # Either the verbose or short polis-keyword pattern is acceptable.
        assert cand.pattern_id in ("polis_keyword", "polis_short")
        assert "1234567" in cand.value["number"] or "1234567" in cand.value.get("display", "")

    def test_no_match_returns_not_found(self):
        result = run_extraction("Этот текст не содержит идентификатор полиса.")
        cand = result.additional_fields.get("policy_number")
        assert cand is None or cand.state == "not_found"

    def test_rejects_cyrillic_word_after_polis_keyword(self):
        # Without the digit guard the case-insensitive [A-ZА-Я] class
        # accepts any Cyrillic word — that's how "Полис № страхования"
        # used to land as the polis number on a real Ingosstrakh polis
        # in production.
        result = run_extraction("Полис № страхования транспортного средства")
        cand = result.additional_fields.get("policy_number")
        assert cand is None or cand.state == "not_found"

    def test_alfa_xls_canonical_5_3_5_2_form(self):
        # The original АльфаСтрахование XLS form with 5/3/5/2 digit
        # segments. Existed before the batch_5 relaxation; included here
        # as a regression guard.
        result = run_extraction("Полис № 71717/046/00402/25 КАСКО полное")
        cand = result.additional_fields.get("policy_number")
        assert cand is not None and cand.state == "found"
        assert "71717/046/00402/25" in cand.value.get("display", "")

    def test_alfa_xls_5_3_7_2_long_third_segment(self):
        # batch_5 brought АльфаСтрахование printouts where the asset-id
        # segment is 7 digits, not the original 5 (49297/046/0000424/25).
        # Without the relaxation the keyword fallback grabbed "218-ФЗ"
        # from the federal-law disclaimer instead.
        text = (
            "Договор страхования средств наземного транспорта\n"
            "5330ec63-6e5b-442e-a6dd-73f07325ff11 17.09.2024 СТРАХОВАТЕЛЬ\n"
            "Согласно ФЗ от 30.12.2004 № 218-ФЗ «О кредитных историях»\n"
            "офис 372\n№ 49297/046/0000424/25\nАДРЕС: 420083, Татарстан"
        )
        result = run_extraction(text)
        cand = result.additional_fields.get("policy_number")
        assert cand is not None and cand.state == "found"
        # Must beat the false-positive 218-ФЗ keyword match by confidence.
        assert "49297/046/0000424/25" in cand.value.get("display", "")

    def test_alfa_xls_letter_branch_in_first_segment(self):
        # АльфаСтрахование branch-letter form with no space between
        # the № marker and the body: pypdf glues "No8991R/046/...".
        text = "Правил страхования\nNo8991R/046/0000340/25\n163011, Архангельская"
        result = run_extraction(text)
        cand = result.additional_fields.get("policy_number")
        assert cand is not None and cand.state == "found"
        assert "8991R/046/0000340/25" in cand.value.get("display", "")

    def test_chulpan_polis_with_ocr_pipe_separator(self):
        # Чулпан КАСКО scanned forms render the polis-number row as
        # "ПОЛИС № | 1211/27-0000185-2 | <description>" — the pipe is
        # an OCR artifact of the table-cell ruling, not real syntax.
        # The keyword-short pattern must tolerate it as an optional
        # separator.
        text = 'ПОЛИС № | 1211/27-0000185-2 | "Добровольное страхование"'
        result = run_extraction(text)
        cand = result.additional_fields.get("policy_number")
        assert cand is not None and cand.state == "found"
        assert "1211/27-0000185-2" in cand.value.get("display", "")
