"""Fail-closed orchestration around an optional provider-neutral model."""

import json
from dataclasses import asdict, dataclass
from typing import Any

from app.domain.interface_contract import (
    OUTCOME_REFUSED,
    PresentationDraft,
    PresentationStatement,
    PresentationValidation,
    ValidationIssue,
    validate_expected_draft,
)
from app.domain.interface_presentation import PresentationEnvelope
from app.llm.models import ModelProviderError, ModelRequest
from app.llm.providers.base import ModelProvider

GENERATION_CONTRACT_VERSION = "1.0"
MODE_DETERMINISTIC = "deterministic"
MODE_PROVIDER_VALIDATED = "provider_validated"
MODE_DETERMINISTIC_FALLBACK = "deterministic_fallback"
MAX_PROVIDER_OUTPUT_CHARS = 100_000

_SYSTEM_INSTRUCTION = """You are an optional formatting component inside TuneForge.
Use only the supplied TuneForge evidence. Do not use pretrained BIOS knowledge or browse. Never
add unsupported values, defaults, compatibility, safety limits, or recommendations. Incomplete or
conflicting evidence must remain visibly incomplete or conflicting. Return one JSON object matching
the supplied schema. Copy every supplied statement and notice exactly; do not paraphrase, add,
omit, merge, reorder, rank, infer, or reconcile. Preserve every claim ID and every citation passage
ID in its supplied order. Return JSON only, with no Markdown fences or commentary."""

_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "outcome",
        "statements",
        "refusal_message",
        "scope_notice",
        "recommendation_notice",
    ],
    "properties": {
        "outcome": {"type": "string", "enum": ["supported", "refused"]},
        "statements": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "claim_id",
                    "claim_type",
                    "applicability_scope",
                    "text",
                    "citation_passage_ids",
                ],
                "properties": {
                    "claim_id": {"type": "integer"},
                    "claim_type": {"type": "string"},
                    "applicability_scope": {"type": "string"},
                    "text": {"type": "string"},
                    "citation_passage_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                    },
                },
            },
        },
        "refusal_message": {"type": ["string", "null"]},
        "scope_notice": {"type": "string"},
        "recommendation_notice": {"type": "string"},
    },
}


@dataclass(frozen=True)
class InterfaceGenerationResult:
    mode: str
    draft: PresentationDraft
    provider_attempted: bool
    provider_id: str | None
    model_id: str | None
    fallback_reason: str | None
    validation: PresentationValidation


def build_expected_draft(envelope: PresentationEnvelope) -> PresentationDraft:
    return PresentationDraft(
        outcome=envelope.outcome,
        statements=tuple(
            PresentationStatement(
                claim_id=statement.claim_id,
                claim_type=statement.claim_type,
                applicability_scope=statement.applicability_scope,
                text=statement.text,
                citation_passage_ids=tuple(citation.passage_id for citation in statement.citations),
            )
            for statement in envelope.statements
        ),
        refusal_message=envelope.refusal_message,
        scope_notice=envelope.scope_notice,
        recommendation_notice=envelope.recommendation_notice,
    )


def build_model_request(envelope: PresentationEnvelope) -> ModelRequest:
    """Build a provider-neutral request from the already-safe presentation envelope."""

    payload = {
        "contract_version": envelope.contract_version,
        # Data minimization: the optional provider needs only retrieved reviewed
        # facts and their citation/scope metadata. It never receives the user's
        # query, system profile, aliases, suggestions, or referral pointers.
        "evidence_statements": [asdict(statement) for statement in envelope.statements],
        "required_draft": asdict(build_expected_draft(envelope)),
    }
    return ModelRequest(
        operation="present_citation_first_envelope",
        contract_version=GENERATION_CONTRACT_VERSION,
        system_instruction=_SYSTEM_INSTRUCTION,
        input_json=json.dumps(payload, sort_keys=True, separators=(",", ":")),
        output_schema_json=json.dumps(
            _OUTPUT_SCHEMA,
            sort_keys=True,
            separators=(",", ":"),
        ),
        temperature=0.0,
        max_output_tokens=4096,
    )


def generate_presentation(
    envelope: PresentationEnvelope,
    provider: ModelProvider | None = None,
) -> InterfaceGenerationResult:
    """Accept only a contract-valid provider draft; otherwise return the core draft."""

    expected = build_expected_draft(envelope)
    expected_validation = validate_expected_draft(expected, expected)
    if provider is None:
        return InterfaceGenerationResult(
            mode=MODE_DETERMINISTIC,
            draft=expected,
            provider_attempted=False,
            provider_id=None,
            model_id=None,
            fallback_reason="provider_not_configured",
            validation=expected_validation,
        )
    if envelope.outcome == OUTCOME_REFUSED:
        return InterfaceGenerationResult(
            mode=MODE_DETERMINISTIC,
            draft=expected,
            provider_attempted=False,
            provider_id=provider.provider_id,
            model_id=None,
            fallback_reason="refused_envelope_bypasses_provider",
            validation=expected_validation,
        )

    request = build_model_request(envelope)
    try:
        response = provider.generate(request)
    except ModelProviderError as error:
        return _fallback(expected, provider.provider_id, f"provider_{error.code}")
    except Exception:
        return _fallback(expected, provider.provider_id, "provider_unexpected_error")

    if response.provider_id != provider.provider_id:
        return _fallback(expected, provider.provider_id, "provider_identity_mismatch")
    try:
        draft = parse_provider_draft(response.output_text)
    except ValueError:
        return _fallback(
            expected,
            provider.provider_id,
            "provider_output_invalid",
            model_id=response.model_id,
        )
    validation = validate_expected_draft(expected, draft)
    if not validation.valid:
        return InterfaceGenerationResult(
            mode=MODE_DETERMINISTIC_FALLBACK,
            draft=expected,
            provider_attempted=True,
            provider_id=provider.provider_id,
            model_id=response.model_id,
            fallback_reason="provider_output_failed_validation",
            validation=validation,
        )
    return InterfaceGenerationResult(
        mode=MODE_PROVIDER_VALIDATED,
        draft=draft,
        provider_attempted=True,
        provider_id=provider.provider_id,
        model_id=response.model_id,
        fallback_reason=None,
        validation=validation,
    )


def parse_provider_draft(output_text: str) -> PresentationDraft:
    if not output_text or len(output_text) > MAX_PROVIDER_OUTPUT_CHARS:
        raise ValueError("Provider output is empty or exceeds the safe size limit.")
    try:
        value = json.loads(output_text, object_pairs_hook=_object_without_duplicate_keys)
    except json.JSONDecodeError:
        raise ValueError("Provider output is not valid JSON.") from None
    _require_exact_keys(
        value,
        {
            "outcome",
            "statements",
            "refusal_message",
            "scope_notice",
            "recommendation_notice",
        },
    )
    outcome = _require_string(value["outcome"])
    statements_value = value["statements"]
    if not isinstance(statements_value, list):
        raise ValueError("Statements must be a JSON array.")
    statements: list[PresentationStatement] = []
    for statement in statements_value:
        _require_exact_keys(
            statement,
            {
                "claim_id",
                "claim_type",
                "applicability_scope",
                "text",
                "citation_passage_ids",
            },
        )
        passage_ids = statement["citation_passage_ids"]
        if not isinstance(passage_ids, list) or not all(_is_integer(row) for row in passage_ids):
            raise ValueError("Citation passage IDs must be an integer array.")
        if not _is_integer(statement["claim_id"]):
            raise ValueError("Claim ID must be an integer.")
        statements.append(
            PresentationStatement(
                claim_id=statement["claim_id"],
                claim_type=_require_string(statement["claim_type"]),
                applicability_scope=_require_string(statement["applicability_scope"]),
                text=_require_string(statement["text"]),
                citation_passage_ids=tuple(passage_ids),
            )
        )
    refusal_message = value["refusal_message"]
    if refusal_message is not None and not isinstance(refusal_message, str):
        raise ValueError("Refusal message must be a string or null.")
    return PresentationDraft(
        outcome=outcome,
        statements=tuple(statements),
        refusal_message=refusal_message,
        scope_notice=_require_string(value["scope_notice"]),
        recommendation_notice=_require_string(value["recommendation_notice"]),
    )


def _fallback(
    expected: PresentationDraft,
    provider_id: str,
    reason: str,
    *,
    model_id: str | None = None,
) -> InterfaceGenerationResult:
    return InterfaceGenerationResult(
        mode=MODE_DETERMINISTIC_FALLBACK,
        draft=expected,
        provider_attempted=True,
        provider_id=provider_id,
        model_id=model_id,
        fallback_reason=reason,
        validation=PresentationValidation(
            contract_version=GENERATION_CONTRACT_VERSION,
            valid=False,
            issues=(
                ValidationIssue(
                    code=reason,
                    message="Provider output was discarded; deterministic presentation used.",
                ),
            ),
        ),
    )


def _require_exact_keys(value: object, keys: set[str]) -> None:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("Provider output does not match the required object shape.")


def _require_string(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Provider output field must be a string.")
    return value


def _is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _object_without_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Provider output contains a duplicate object key.")
        value[key] = item
    return value
