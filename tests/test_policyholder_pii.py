"""Cross-cutting PII invariants for the policyholder feature.

The contract here is what gives integrators safety guarantees: with
``extract_pii=False`` (the default), passport and birth-date data
must NEVER appear on the result, even when the source text plainly
contains them. The other policyholder fields (name/type/INN/contacts)
are unaffected.
"""

from __future__ import annotations

from polis_recognizer import PolicyExtractor


_RICH_TEXT = (
    "Страхователь: Иванов Иван Иванович\n"
    "ИНН 500100732259\n"
    "Адрес: 101000, г. Москва, ул. Ленина, д. 1\n"
    "Тел.: +7 (495) 111-22-33\n"
    "Email: ivan@example.ru\n"
    "Паспорт 12 34 567890 выдан 01.01.2010\n"
    "Дата рождения: 01.01.1980\n"
)


class TestDefaultIsSafe:
    def test_default_extractor_does_not_surface_passport(self):
        result = PolicyExtractor().extract_from_text(_RICH_TEXT)
        assert result.policyholder is not None
        assert result.policyholder["passport"] is None

    def test_default_extractor_does_not_surface_birth_date(self):
        result = PolicyExtractor().extract_from_text(_RICH_TEXT)
        assert result.policyholder is not None
        assert result.policyholder["birth_date"] is None

    def test_default_extractor_still_returns_non_pii_fields(self):
        # Operational data (name, INN, address, phone, email) is NOT
        # PII-gated and must surface regardless of the flag.
        result = PolicyExtractor().extract_from_text(_RICH_TEXT)
        assert result.policyholder is not None
        assert result.policyholder["name"] is not None
        assert result.policyholder["inn"] == "500100732259"
        assert result.policyholder_contacts is not None
        assert "+74951112233" in result.policyholder_contacts["phones"]
        assert "ivan@example.ru" in result.policyholder_contacts["emails"]
        assert result.policyholder_contacts["address"] is not None


class TestOptInExposesSensitive:
    def test_extract_pii_flag_exposes_passport_and_birth_date(self):
        result = PolicyExtractor(extract_pii=True).extract_from_text(
            _RICH_TEXT
        )
        assert result.policyholder is not None
        assert result.policyholder["passport"] == {
            "series": "1234",
            "number": "567890",
        }
        assert result.policyholder["birth_date"] is not None
