"""Checksum validators for Russian organisational identifiers.

ИНН (10 digits — legal entity / 12 digits — individual or ИП), ОГРН
(13 digits — legal entity), ОГРНИП (15 digits — individual
entrepreneur). Algorithms are published by ФНС России.

These validators reject syntactically malformed input quietly (return
``False`` rather than raising). They are used as filters on regex
matches where the "match-but-invalid" case is normal and frequent
(random 10/12-digit runs in OCR output, bank account numbers, etc.).
"""

from __future__ import annotations


_INN_10_WEIGHTS = (2, 4, 10, 3, 5, 9, 4, 6, 8)
_INN_12_WEIGHTS_1 = (7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
_INN_12_WEIGHTS_2 = (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)


def _check_digit(s: str, weights) -> int:
    """Compute the ФНС check digit: ``(Σ d[i]·w[i]) mod 11 mod 10``."""
    return sum(int(s[i]) * weights[i] for i in range(len(weights))) % 11 % 10


def validate_inn_10(s: str) -> bool:
    """Validate a 10-digit ИНН (legal entity)."""
    if len(s) != 10 or not s.isdigit():
        return False
    return _check_digit(s, _INN_10_WEIGHTS) == int(s[9])


def validate_inn_12(s: str) -> bool:
    """Validate a 12-digit ИНН (natural person / ИП).

    Both check digits (positions 11 and 12) must agree with their
    independent weighted sums.
    """
    if len(s) != 12 or not s.isdigit():
        return False
    c1 = _check_digit(s, _INN_12_WEIGHTS_1)
    c2 = _check_digit(s, _INN_12_WEIGHTS_2)
    return c1 == int(s[10]) and c2 == int(s[11])


def validate_inn(s: str) -> bool:
    """Validate an ИНН of either length (10 or 12)."""
    if len(s) == 10:
        return validate_inn_10(s)
    if len(s) == 12:
        return validate_inn_12(s)
    return False


def validate_ogrn_13(s: str) -> bool:
    """Validate a 13-digit ОГРН (legal entity).

    Check: ``int(s[:12]) mod 11 mod 10 == int(s[12])``.
    """
    if len(s) != 13 or not s.isdigit():
        return False
    return int(s[:12]) % 11 % 10 == int(s[12])


def validate_ogrn_15(s: str) -> bool:
    """Validate a 15-digit ОГРНИП (individual entrepreneur).

    Check: ``int(s[:14]) mod 13 mod 10 == int(s[14])``.
    """
    if len(s) != 15 or not s.isdigit():
        return False
    return int(s[:14]) % 13 % 10 == int(s[14])


def validate_ogrn(s: str) -> bool:
    """Validate an ОГРН of either length (13 or 15)."""
    if len(s) == 13:
        return validate_ogrn_13(s)
    if len(s) == 15:
        return validate_ogrn_15(s)
    return False
