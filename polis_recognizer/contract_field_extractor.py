"""
Contract Field Extraction Service.

Public surface: ``ContractFieldExtractor.extract_contract_fields(text)``
returns a ``ContractFieldsResult`` whose ``to_dict()`` shape is part of
the API contract (consumers: ``PreIngestJob.contract_fields``,
``PolicyContextBuilder``, admin renderers, downstream tests).

The implementation delegates to the deterministic v2 pipeline under
``apps.analyses.services.extraction``. The v2 pipeline produces typed
``Candidate`` objects with explicit confidence components and a
diagnostic trace; this module re-shapes them into the legacy dataclass
contract so existing consumers see no schema change. New v2-only
fields (premium, sum_type) live on
``ContractFieldsResult.additional_fields`` and surface through
``to_diagnostics_payload()`` without altering ``to_dict()``.

C4 removed the legacy v1 ``_extract_*`` private helpers (~1050 lines)
and their direct unit tests. v1 was dead in production for a while;
the file kept it alive only for tests. Corpus-level coverage of the
end-to-end behaviour stays in
``test_contract_field_extractor.py::TestPartialExtraction``,
``TestAdditionalDeterministicFields`` and
``test_contract_field_extractor_real_kasko.py``.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .extraction import (
    Candidate,
    run_extraction,
)
from .extraction.parsers import ADDITIONAL_PARSERS

logger = logging.getLogger(__name__)


_LEGACY_FIELD_DIAGNOSTIC_MESSAGES = {
    "extracted": "Поле извлечено по детерминированному правилу.",
    "absent_recognized": "Полис явно указывает отсутствие поля.",
    "missing": "Подходящий паттерн для поля не найден.",
}


def _legacy_status_and_reason(candidate, field_name: str) -> tuple[str, str, str]:
    """Map a v2 Candidate state to the legacy {stage, status, reason_code, message}."""
    if candidate is None:
        return ("missing", "no_pattern_match",
                f"Подходящий паттерн для поля '{field_name}' не найден.")
    if candidate.state == "found":
        return ("extracted", candidate.pattern_id or "pattern_matched",
                "Поле извлечено по детерминированному правилу.")
    if candidate.state == "absent":
        return ("extracted", candidate.pattern_id or "absent_recognized",
                "Полис явно указывает отсутствие поля.")
    return ("missing", candidate.pattern_id or "no_pattern_match",
            f"Подходящий паттерн для поля '{field_name}' не найден.")


def _map_v2_to_legacy(v2_result, extractor: "ContractFieldExtractor") -> dict:
    """Convert v2 ExtractionV2Result Candidates into legacy dataclasses.

    Also records per-field diagnostics on the extractor instance so the
    legacy ``extraction_status`` / ``warning_codes`` semantics stay
    intact for the surrounding tasks.py orchestration.
    """
    legacy = v2_result.legacy_fields

    def _record(field_name: str, candidate):
        status, reason, message = _legacy_status_and_reason(candidate, field_name)
        extractor._record_field_diagnostic(
            field_name,
            status,
            reason,
            message,
            issue_warning=(status == "missing" and field_name in extractor.SUPPORTED_FIELD_NAMES),
        )

    # policy_period — dict {start, end} or None
    pp_cand = legacy.get("policy_period")
    _record("policy_period", pp_cand)
    if pp_cand and pp_cand.state == "found" and isinstance(pp_cand.value, dict):
        policy_period = PolicyPeriodField(
            start=pp_cand.value.get("start"),
            end=pp_cand.value.get("end"),
            confidence=pp_cand.confidence,
            source_fragment=pp_cand.source_fragment or None,
        )
    else:
        policy_period = PolicyPeriodField(
            start=None, end=None, confidence=0.0, source_fragment=None
        )

    # franchise — MonetaryField, possibly absent=True
    fr_cand = legacy.get("franchise")
    _record("franchise", fr_cand)
    franchise = _candidate_to_monetary(fr_cand)

    # limit — MonetaryField (always non-absent path)
    lim_cand = legacy.get("limit")
    _record("limit", lim_cand)
    limit = _candidate_to_monetary(lim_cand)

    # repair_mode — TextField with .value as plain string ("dealer"/"service"/"cash")
    rm_cand = legacy.get("repair_mode")
    _record("repair_mode", rm_cand)
    if rm_cand and rm_cand.state == "found":
        repair_mode = TextField(
            value=rm_cand.value if isinstance(rm_cand.value, str) else None,
            confidence=rm_cand.confidence,
            source_fragment=rm_cand.source_fragment or None,
        )
    else:
        repair_mode = TextField(value=None, confidence=0.0, source_fragment=None)

    # Additional v2 fields — passed through as plain dicts so the
    # rest of the system can consume them without a new import.
    additional_fields = {}
    for parser_name, candidate in v2_result.additional_fields.items():
        if candidate is None:
            additional_fields[parser_name] = None
        else:
            additional_fields[parser_name] = candidate.to_dict()

    fields_found_flags = {
        "policy_period": policy_period.start is not None and policy_period.end is not None,
        "franchise": (franchise.value is not None) or franchise.absent,
        "limit": limit.value is not None,
        "repair_mode": bool(repair_mode.value),
    }
    return {
        "policy_period": policy_period,
        "franchise": franchise,
        "limit": limit,
        "repair_mode": repair_mode,
        "additional_fields": additional_fields,
        "fields_found_flags": fields_found_flags,
    }


def _candidate_to_monetary(candidate) -> "MonetaryField":
    if candidate is None or candidate.state == "not_found" or not isinstance(candidate.value, dict):
        return MonetaryField(
            value=None, currency=None, confidence=0.0, source_fragment=None,
        )
    value = candidate.value.get("value")
    currency = candidate.value.get("currency")
    is_absent = bool(candidate.value.get("absent")) or candidate.state == "absent"
    return MonetaryField(
        value=0 if is_absent else value,
        currency=currency or ("RUB" if is_absent else None),
        confidence=candidate.confidence,
        source_fragment=candidate.source_fragment or None,
        absent=is_absent,
    )


@dataclass
class PolicyPeriodField:
    """Policy period with start and end dates."""
    start: Optional[str]  # ISO format YYYY-MM-DD
    end: Optional[str]    # ISO format YYYY-MM-DD
    confidence: float     # 0.0 to 1.0
    source_fragment: Optional[str]  # Text fragment where found (max 200 chars)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            'start': self.start,
            'end': self.end,
            'confidence': self.confidence,
            'source_fragment': self.source_fragment
        }


@dataclass
class MonetaryField:
    """Monetary value with currency.

    `absent=True` means the policy explicitly states the field does NOT
    apply (e.g. "франшиза - нет"). It is qualitatively different from a
    null value, which means extraction did not find anything. Absent
    fields render as "не предусмотрена" downstream, not as
    "[данные не распознаны автоматически]".
    """
    value: Optional[float]
    currency: Optional[str]  # RUB, USD, EUR, or None
    confidence: float        # 0.0 to 1.0
    source_fragment: Optional[str]  # Text fragment where found (max 200 chars)
    absent: bool = False

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization.

        `absent` is included only when True so the default contract on
        existing consumers stays unchanged.
        """
        payload = {
            'value': self.value,
            'currency': self.currency,
            'confidence': self.confidence,
            'source_fragment': self.source_fragment,
        }
        if self.absent:
            payload['absent'] = True
        return payload


@dataclass
class TextField:
    """Scalar text field with confidence and source fragment."""
    value: Optional[str]
    confidence: float
    source_fragment: Optional[str]

    def to_dict(self) -> dict:
        return {
            'value': self.value,
            'confidence': self.confidence,
            'source_fragment': self.source_fragment
        }


@dataclass
class FieldDiagnostic:
    """Safe per-stage diagnostic entry for extraction telemetry."""
    stage: str
    status: str
    reason_code: str
    message: str

    def to_dict(self) -> dict:
        return {
            'stage': self.stage,
            'status': self.status,
            'reason_code': self.reason_code,
            'message': self.message,
        }


@dataclass
class ContractFieldsResult:
    """Complete result of field extraction.

    ``to_dict()`` shape is part of the public contract — the legacy
    fields extracted from the polis itself. New v2-only fields land in
    ``additional_fields`` and surface through
    ``to_diagnostics_payload()`` instead, so ``contract_fields`` JSON
    in the database keeps the same keys consumers know about.

    Task 1.9 (POLICY_RECOGNITION_QUALITY_PLAN): ``notice_deadline`` and
    ``documents_from_policy`` were removed because that data is in the
    Rules document, not in the polis. Old payloads that still carry
    those keys are tolerated by readers (forward-compat).
    """
    policy_period: PolicyPeriodField
    franchise: MonetaryField
    limit: MonetaryField
    repair_mode: TextField
    processing_time_ms: float
    extraction_status: str = 'done'
    diagnostics: list[dict] = field(default_factory=list)
    warning_codes: list[str] = field(default_factory=list)
    additional_fields: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """
        Convert to dictionary matching contract_context_json schema.
        
        Returns:
            {
              "policy_period": {
                "start": "YYYY-MM-DD|null",
                "end": "YYYY-MM-DD|null",
                "confidence": 0.0-1.0,
                "source_fragment": "string|null"
              },
              "franchise": {
                "value": number|null,
                "currency": "RUB|USD|EUR|null",
                "confidence": 0.0-1.0,
                "source_fragment": "string|null"
              },
              "limit": {
                "value": number|null,
                "currency": "RUB|USD|EUR|null",
                "confidence": 0.0-1.0,
                "source_fragment": "string|null"
              }
            }
        """
        return {
            'policy_period': self.policy_period.to_dict(),
            'franchise': self.franchise.to_dict(),
            'limit': self.limit.to_dict(),
            'repair_mode': self.repair_mode.to_dict(),
        }

    def to_diagnostics_payload(self) -> dict:
        """Return safe diagnostics for task-level telemetry surfaces."""
        payload = {
            'status': self.extraction_status,
            'processing_time_ms': round(float(self.processing_time_ms), 2),
            'warning_codes': list(self.warning_codes),
            'diagnostics': list(self.diagnostics),
        }
        if self.additional_fields:
            payload['additional_fields'] = dict(self.additional_fields)
        return payload


class ContractFieldExtractor:
    """
    Service for extracting contract fields from policy text.
    
    Extracts fields using deterministic pattern matching:
    - policy_period (start/end dates)
    - franchise (deductible amount and currency)
    - limit (insurance sum and currency)
    - repair_mode (dealer/service/cash)

    All extraction is rule-based without LLM usage.
    """

    def __init__(self):
        self._correlation_id: Optional[str] = None
        self._field_diagnostics: dict[str, FieldDiagnostic] = {}
        self._warning_codes: list[str] = []
    
    # Configuration constants
    MAX_TEXT_LENGTH = 100_000  # Process first 100k chars
    TIMEOUT_MS = 500  # Maximum processing time
    SOURCE_FRAGMENT_MAX_LENGTH = 200  # Max length for logged fragments
    SUPPORTED_FIELD_NAMES = (
        'policy_period',
        'franchise',
        'limit',
        'repair_mode',
    )

    # NOTE: All `_extract_*` per-field helpers and their pattern lists
    # used to live here (~1050 lines). C4 removed them — extraction is
    # delegated to `apps.analyses.services.extraction` (the v2 pipeline)
    # and its result is mapped back into the dataclass shapes below by
    # `_map_v2_to_legacy`. The legacy methods were dead in production
    # for a while; only direct unit tests kept them alive. Those tests
    # were dropped together with the methods (corpus regressions live
    # in `test_contract_field_extractor.py::TestExtractContractFields`,
    # `TestPartialExtraction`, `TestAdditionalDeterministicFields` and
    # `test_contract_field_extractor_real_kasko.py`).

    @classmethod
    def empty_contract_fields_payload(cls) -> dict:
        """Return stable null structure for contract_fields contract."""
        return cls()._create_null_result(0.0).to_dict()

    def _reset_run_state(self, correlation_id: Optional[str] = None) -> None:
        self._correlation_id = correlation_id
        self._field_diagnostics = {}
        self._warning_codes = []

    def _append_warning_code(self, code: Optional[str]) -> None:
        if not code or code in self._warning_codes:
            return
        self._warning_codes.append(code)

    def _serialize_diagnostics(self) -> list[dict]:
        diagnostics = []
        for field_name in self.SUPPORTED_FIELD_NAMES:
            diagnostic = self._field_diagnostics.get(field_name)
            if diagnostic is not None:
                diagnostics.append(diagnostic.to_dict())

        for stage_name, diagnostic in self._field_diagnostics.items():
            if stage_name in self.SUPPORTED_FIELD_NAMES:
                continue
            diagnostics.append(diagnostic.to_dict())

        return diagnostics

    def _log_stage_issue(
        self,
        stage: str,
        reason_code: str,
        message: str,
        *,
        level: str = 'warning',
        exc_info: bool = False,
        extra: Optional[dict] = None,
    ) -> None:
        log_extra = {
            'stage': stage,
            'reason_code': reason_code,
        }
        if self._correlation_id:
            log_extra['correlation_id'] = self._correlation_id
        if extra:
            log_extra.update(extra)
        getattr(logger, level)(message, extra=log_extra, exc_info=exc_info)

    def _record_field_diagnostic(
        self,
        field_name: str,
        status: str,
        reason_code: str,
        message: str,
        *,
        issue_warning: bool = False,
        log_level: Optional[str] = None,
        exc_info: bool = False,
    ) -> None:
        self._field_diagnostics[field_name] = FieldDiagnostic(
            stage=field_name,
            status=status,
            reason_code=reason_code,
            message=message,
        )
        if issue_warning:
            self._append_warning_code('contract_field_extraction_partial')
            self._append_warning_code(f'field_{field_name}_{reason_code}')
        if log_level:
            self._log_stage_issue(
                field_name,
                reason_code,
                message,
                level=log_level,
                exc_info=exc_info,
            )


    def extract_contract_fields(
        self,
        text: str,
        correlation_id: Optional[str] = None,
        *,
        tables: Optional[list] = None,
        extract_pii: bool = False,
    ) -> ContractFieldsResult:
        """Extract contract fields via the deterministic v2 pipeline.

        The body of this method is intentionally tiny: all the heavy
        lifting (normalization, layout analysis, per-field parsers,
        candidate ranking) lives in
        ``apps.analyses.services.extraction``. We translate the
        pipeline's ``Candidate`` objects back into the legacy dataclass
        shapes that the rest of the system already understands.

        Never raises — pipeline-level exceptions surface as a
        ``failed`` ContractFieldsResult with nulls.
        """
        start_time = time.time()
        self._reset_run_state(correlation_id)

        try:
            if not text:
                logger.info(
                    "Empty or null text provided, returning null structure",
                    extra={'correlation_id': correlation_id} if correlation_id else None,
                )
                for field_name in self.SUPPORTED_FIELD_NAMES:
                    self._record_field_diagnostic(
                        field_name,
                        'skipped',
                        'empty_input',
                        'Извлеченный текст пустой, поле не анализировалось.',
                    )
                return self._create_null_result(0.0, extraction_status='skipped')

            if len(text) > self.MAX_TEXT_LENGTH:
                logger.warning(
                    f"Text length {len(text)} exceeds MAX_TEXT_LENGTH {self.MAX_TEXT_LENGTH}, truncating"
                )
                text = text[:self.MAX_TEXT_LENGTH]

            v2_result = run_extraction(
                text, correlation_id=correlation_id, tables=tables,
                extract_pii=extract_pii,
            )
            mapped = _map_v2_to_legacy(v2_result, self)
            processing_time_ms = (time.time() - start_time) * 1000

            fields_found = [
                name for name, present in mapped["fields_found_flags"].items() if present
            ]
            logger.info(
                "Contract fields extracted via v2 pipeline: %d fields found",
                len(fields_found),
                extra={
                    "fields_found": fields_found,
                    "processing_time_ms": processing_time_ms,
                    "v2_elapsed_ms": v2_result.elapsed_ms,
                    "correlation_id": correlation_id,
                    "warning_codes": list(self._warning_codes),
                },
            )

            return ContractFieldsResult(
                policy_period=mapped["policy_period"],
                franchise=mapped["franchise"],
                limit=mapped["limit"],
                repair_mode=mapped["repair_mode"],
                processing_time_ms=processing_time_ms,
                extraction_status='partial' if self._warning_codes else 'done',
                diagnostics=self._serialize_diagnostics(),
                warning_codes=list(self._warning_codes),
                additional_fields=mapped["additional_fields"],
            )

        except Exception as exc:
            processing_time_ms = (time.time() - start_time) * 1000
            self._append_warning_code('contract_field_extraction_failed')
            self._field_diagnostics['field_extraction'] = FieldDiagnostic(
                stage='field_extraction',
                status='failed',
                reason_code='unexpected_exception',
                message='Этап извлечения полей договора завершился с ошибкой.',
            )
            logger.error(
                f"Error during field extraction: {exc}",
                extra={
                    "processing_time_ms": processing_time_ms,
                    "correlation_id": correlation_id,
                    "reason_code": "unexpected_exception",
                },
                exc_info=True,
            )
            return self._create_null_result(processing_time_ms, extraction_status='failed')

    def _create_null_result(
        self,
        processing_time_ms: float,
        extraction_status: str = 'done',
    ) -> ContractFieldsResult:
        """Create a result with all fields set to null."""
        return ContractFieldsResult(
            policy_period=PolicyPeriodField(
                start=None,
                end=None,
                confidence=0.0,
                source_fragment=None
            ),
            franchise=MonetaryField(
                value=None,
                currency=None,
                confidence=0.0,
                source_fragment=None
            ),
            limit=MonetaryField(
                value=None,
                currency=None,
                confidence=0.0,
                source_fragment=None
            ),
            repair_mode=TextField(
                value=None,
                confidence=0.0,
                source_fragment=None
            ),
            processing_time_ms=processing_time_ms,
            extraction_status=extraction_status,
            diagnostics=self._serialize_diagnostics(),
            warning_codes=list(self._warning_codes),
        )
