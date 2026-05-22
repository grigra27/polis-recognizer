"""Tests for ИНН / ОГРН checksum validators.

The validators are pure functions on strings, so these are unit tests
in the strict sense. Used by the policyholder-type parser (PR #2) and
the ИНН/ОГРН parsers (PR #3 onwards) to filter regex matches.
"""

from __future__ import annotations

from polis_recognizer.extraction.validators import (
    validate_inn,
    validate_inn_10,
    validate_inn_12,
    validate_ogrn,
    validate_ogrn_13,
    validate_ogrn_15,
)


# Known-valid public identifiers used as fixtures. ИНН-10 7707083893
# is СберБанк (public, checksum-correct). ИНН-12 500100732259 is a
# canonical example used in published documentation of the algorithm.
_VALID_INN_10 = "7707083893"
_VALID_INN_12 = "500100732259"


class TestInn10:
    def test_accepts_known_valid_inn_10(self):
        assert validate_inn_10(_VALID_INN_10) is True

    def test_rejects_wrong_check_digit(self):
        # Flip the last digit — checksum must fail.
        bad = _VALID_INN_10[:-1] + ("0" if _VALID_INN_10[-1] != "0" else "1")
        assert validate_inn_10(bad) is False

    def test_rejects_wrong_length(self):
        assert validate_inn_10("770708389") is False  # 9 digits
        assert validate_inn_10("77070838930") is False  # 11 digits

    def test_rejects_non_digits(self):
        assert validate_inn_10("770708389A") is False
        assert validate_inn_10("") is False

    def test_rejects_random_digit_run(self):
        # A typical OCR artefact — straight digit run, not a real INN.
        assert validate_inn_10("1234567890") is False


class TestInn12:
    def test_accepts_known_valid_inn_12(self):
        assert validate_inn_12(_VALID_INN_12) is True

    def test_rejects_wrong_first_check_digit(self):
        bad = _VALID_INN_12[:10] + (
            "0" if _VALID_INN_12[10] != "0" else "1"
        ) + _VALID_INN_12[11]
        assert validate_inn_12(bad) is False

    def test_rejects_wrong_second_check_digit(self):
        bad = _VALID_INN_12[:11] + ("0" if _VALID_INN_12[11] != "0" else "1")
        assert validate_inn_12(bad) is False

    def test_rejects_wrong_length(self):
        assert validate_inn_12("12345678901") is False  # 11 digits
        assert validate_inn_12("1234567890123") is False  # 13 digits

    def test_rejects_non_digits(self):
        assert validate_inn_12("50010073225X") is False


class TestInnDispatch:
    def test_dispatch_to_inn_10(self):
        assert validate_inn(_VALID_INN_10) is True

    def test_dispatch_to_inn_12(self):
        assert validate_inn(_VALID_INN_12) is True

    def test_rejects_unsupported_length(self):
        assert validate_inn("12345") is False
        assert validate_inn("12345678") is False
        assert validate_inn("12345678901234") is False


class TestOgrn13:
    def test_accepts_known_valid_ogrn(self):
        # ОГРН of СберБанк, public; 1027700132195 — checksum-correct.
        assert validate_ogrn_13("1027700132195") is True

    def test_rejects_wrong_check_digit(self):
        assert validate_ogrn_13("1027700132190") is False

    def test_rejects_wrong_length(self):
        assert validate_ogrn_13("102770013219") is False  # 12 digits
        assert validate_ogrn_13("10277001321950") is False  # 14 digits

    def test_rejects_non_digits(self):
        assert validate_ogrn_13("102770013219X") is False


class TestOgrn15:
    def test_accepts_known_valid_ogrnip(self):
        # 304500116000157 — published ОГРНИП example; mod-13-mod-10 valid.
        assert validate_ogrn_15("304500116000157") is True

    def test_rejects_wrong_check_digit(self):
        assert validate_ogrn_15("304500116000150") is False

    def test_rejects_wrong_length(self):
        assert validate_ogrn_15("30450011600015") is False  # 14 digits
        assert validate_ogrn_15("3045001160001570") is False  # 16 digits


class TestOgrnDispatch:
    def test_dispatch_to_ogrn_13(self):
        assert validate_ogrn("1027700132195") is True

    def test_dispatch_to_ogrn_15(self):
        assert validate_ogrn("304500116000157") is True

    def test_rejects_unsupported_length(self):
        assert validate_ogrn("12345") is False
