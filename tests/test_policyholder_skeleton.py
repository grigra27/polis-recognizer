"""PR#1 of the policyholder + contacts roadmap — skeleton signature tests.

PR#1 wires the ``extract_pii`` flag end-to-end through the pipeline and
adds two new fields to :class:`ExtractedPolicy` (``policyholder`` and
``policyholder_contacts``) without registering any policyholder parsers
yet. These tests guard the wiring: the API shape is in place, the new
fields default to ``None``, and existing behaviour is untouched.

Behavioural tests for the actual policyholder parsers arrive in later
PRs of the roadmap (see ``docs/roadmap-policyholder.md``).
"""

from __future__ import annotations

from polis_recognizer import PolicyExtractor
from polis_recognizer.extraction import run_extraction
from polis_recognizer.extraction.layout import LayoutAnalyzer
from polis_recognizer.extraction.negation import NegationContext
from polis_recognizer.extraction.normalizer import TextNormalizer
from polis_recognizer.extraction.parsers import ExtractionContext


class TestExtractedPolicyShape:
    def test_new_fields_present_and_default_to_none(self):
        result = PolicyExtractor().extract_from_text(
            "пустой текст без реквизитов страхователя"
        )
        assert hasattr(result, "policyholder")
        assert hasattr(result, "policyholder_contacts")
        assert result.policyholder is None
        assert result.policyholder_contacts is None

    def test_is_complete_unaffected_by_new_fields(self):
        # is_complete still gates on the legacy seven fields. With an
        # empty text none of them is found, so is_complete is False —
        # but the property itself must not raise even when the new
        # fields are present on the dataclass.
        result = PolicyExtractor().extract_from_text("")
        assert result.is_complete is False


class TestPolicyExtractorConstructor:
    def test_extract_pii_defaults_to_false(self):
        extractor = PolicyExtractor()
        assert extractor._extract_pii is False

    def test_extract_pii_can_be_enabled(self):
        extractor = PolicyExtractor(extract_pii=True)
        assert extractor._extract_pii is True

    def test_extract_pii_does_not_change_legacy_extraction(self):
        # No policyholder parsers exist in PR#1, so flipping the flag
        # must not change any legacy field value.
        text = (
            "Серия 2022 № 0364420 / 26ТФ от 18.02.2026\n"
            "Полис страхования транспортного средства\n"
        )
        off = PolicyExtractor(extract_pii=False).extract_from_text(text)
        on = PolicyExtractor(extract_pii=True).extract_from_text(text)
        assert off.policy_number == on.policy_number
        assert off.policyholder is None
        assert on.policyholder is None
        assert off.policyholder_contacts is None
        assert on.policyholder_contacts is None


class TestPipelineExtractPiiKwarg:
    def test_run_extraction_accepts_extract_pii(self):
        # Smoke — kwarg accepted on both values, no exception, result
        # is structurally well-formed. The kwarg's actual gating of
        # passport / birth-date parsers is exercised in PR #6's tests.
        for flag in (False, True):
            v2 = run_extraction("текст без реквизитов", extract_pii=flag)
            assert hasattr(v2, "additional_fields")
            assert isinstance(v2.additional_fields, dict)

    def test_extraction_context_extract_pii_attribute(self):
        normalized = TextNormalizer().normalize("test")
        ctx_default = ExtractionContext(
            raw="test",
            normalized=normalized,
            layout=LayoutAnalyzer(),
            negation=NegationContext(),
        )
        ctx_on = ExtractionContext(
            raw="test",
            normalized=normalized,
            layout=LayoutAnalyzer(),
            negation=NegationContext(),
            extract_pii=True,
        )
        assert ctx_default.extract_pii is False
        assert ctx_on.extract_pii is True
