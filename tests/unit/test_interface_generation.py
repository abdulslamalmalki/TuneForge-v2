"""Provider-neutral generation boundary and deterministic fallback."""

import json
from dataclasses import asdict, replace

import pytest

from app.domain.interface_generation import (
    MODE_DETERMINISTIC,
    MODE_DETERMINISTIC_FALLBACK,
    MODE_PROVIDER_VALIDATED,
    build_expected_draft,
    build_model_request,
    generate_presentation,
    parse_provider_draft,
)
from app.domain.interface_presentation import (
    PresentationCitation,
    PresentationEnvelope,
    PresentationFact,
)
from app.llm.models import ModelProviderError, ModelResponse
from app.llm.providers.fake import FakeModelProvider
from app.observability import metrics


def _envelope(*, supported: bool = True) -> PresentationEnvelope:
    statements = (
        (
            PresentationFact(
                claim_id=10,
                claim_type="behavior",
                applicability_scope="source_scope",
                text="PBO can operate up to motherboard limits.",
                citations=(
                    PresentationCitation(
                        passage_id=101,
                        source_id=1,
                        publisher_name="Advanced Micro Devices, Inc.",
                        source_title="Ryzen Master User Guide",
                        source_url=("https://docs.amd.com/r/en-US/68886-ryzen-master-user-guide"),
                        document_id="68886",
                        document_version="3.1.0",
                        page_number=26,
                        section_heading="Precision Boost Overdrive",
                        text_sha256="a" * 64,
                    ),
                ),
            ),
        )
        if supported
        else ()
    )
    scope_notice = (
        "Accepted facts remain source-scoped."
        if supported
        else "No accepted guidance claims are available."
    )
    return PresentationEnvelope(
        contract_version="1.0",
        outcome="supported" if supported else "refused",
        resolution_status="resolved",
        query="Precision Boost Overdrive",
        canonical_id="precision-boost-overdrive",
        canonical_name="Precision Boost Overdrive",
        canonical_description="AMD automatic boost control.",
        resolved_by="canonical_name",
        matched_alias=None,
        classification=None,
        system_context=None,
        context_notice=None,
        equivalent_vendor_names=(),
        statements=statements,
        scope_notice=scope_notice,
        recommendation_status="not_available",
        recommendation_notice="TuneForge will not invent a tuning plan.",
        refusal_message=None if supported else scope_notice,
        referral_pointers=(),
    )


def _response_for(envelope: PresentationEnvelope, *, provider_id: str = "fake") -> ModelResponse:
    return ModelResponse(
        provider_id=provider_id,
        model_id="fake-model-1",
        output_text=json.dumps(asdict(build_expected_draft(envelope))),
        finish_reason="stop",
    )


def test_no_provider_returns_the_deterministic_core_draft():
    envelope = _envelope()
    result = generate_presentation(envelope)
    assert result.mode == MODE_DETERMINISTIC
    assert result.provider_attempted is False
    assert result.fallback_reason == "provider_not_configured"
    assert result.validation.valid is True
    assert result.draft == build_expected_draft(envelope)


def test_refusal_bypasses_a_configured_provider():
    envelope = _envelope(supported=False)
    provider = FakeModelProvider([_response_for(envelope)])
    result = generate_presentation(envelope, provider)
    assert result.mode == MODE_DETERMINISTIC
    assert result.provider_attempted is False
    assert result.fallback_reason == "refused_envelope_bypasses_provider"
    assert provider.requests == []
    assert result.draft.outcome == "refused"


def test_exact_provider_output_is_accepted_only_after_validation():
    envelope = _envelope()
    provider = FakeModelProvider([_response_for(envelope)])
    result = generate_presentation(envelope, provider)
    assert result.mode == MODE_PROVIDER_VALIDATED
    assert result.provider_attempted is True
    assert result.provider_id == "fake"
    assert result.model_id == "fake-model-1"
    assert result.fallback_reason is None
    assert result.validation.valid is True
    assert result.draft == build_expected_draft(envelope)


def test_paraphrase_is_discarded_and_deterministic_draft_is_used():
    metrics.reset()
    envelope = _envelope()
    expected = build_expected_draft(envelope)
    changed_statement = replace(expected.statements[0], text="PBO raises motherboard limits.")
    changed = replace(expected, statements=(changed_statement,))
    provider = FakeModelProvider(
        [
            ModelResponse(
                provider_id="fake",
                model_id="unsafe-model",
                output_text=json.dumps(asdict(changed)),
            )
        ]
    )
    result = generate_presentation(envelope, provider)
    assert result.mode == MODE_DETERMINISTIC_FALLBACK
    assert result.draft == expected
    assert result.fallback_reason == "provider_output_failed_validation"
    assert "claim_text_changed" in {issue.code for issue in result.validation.issues}
    counters = metrics.snapshot()["counters"]
    assert any(row["name"] == "citation_validation_failures_total" for row in counters)
    assert any(
        row["name"] == "provider_presentations_total"
        and row["labels"]["status"] == "fallback"
        for row in counters
    )


@pytest.mark.parametrize(
    "output_text",
    [
        "not json",
        "```json\n{}\n```",
        '{"outcome":"supported","outcome":"refused"}',
        json.dumps({"outcome": "supported"}),
        json.dumps({**asdict(build_expected_draft(_envelope())), "extra": "unsafe"}),
    ],
)
def test_malformed_or_non_exact_json_shape_falls_back(output_text):
    envelope = _envelope()
    provider = FakeModelProvider(
        [ModelResponse(provider_id="fake", model_id="bad-json", output_text=output_text)]
    )
    result = generate_presentation(envelope, provider)
    assert result.mode == MODE_DETERMINISTIC_FALLBACK
    assert result.fallback_reason == "provider_output_invalid"
    assert result.draft == build_expected_draft(envelope)


def test_normalized_provider_failure_and_unexpected_error_both_fail_closed():
    envelope = _envelope()
    unavailable = FakeModelProvider(
        [ModelProviderError("unavailable", "Provider is temporarily unavailable.")]
    )
    result = generate_presentation(envelope, unavailable)
    assert result.fallback_reason == "provider_unavailable"
    assert result.draft == build_expected_draft(envelope)

    class BrokenProvider:
        provider_id = "broken"

        def generate(self, request):
            raise RuntimeError("raw upstream secret must not escape")

    result = generate_presentation(envelope, BrokenProvider())
    assert result.fallback_reason == "provider_unexpected_error"
    assert "secret" not in str(result)


def test_provider_identity_mismatch_fails_closed():
    envelope = _envelope()
    provider = FakeModelProvider([_response_for(envelope, provider_id="another")])
    result = generate_presentation(envelope, provider)
    assert result.mode == MODE_DETERMINISTIC_FALLBACK
    assert result.fallback_reason == "provider_identity_mismatch"
    assert result.model_id is None


def test_request_contains_only_safe_envelope_and_exact_required_draft():
    envelope = _envelope()
    request = build_model_request(envelope)
    payload = json.loads(request.input_json)
    assert request.operation == "present_citation_first_envelope"
    assert request.temperature == 0.0
    assert payload["evidence_statements"][0]["text"] == (
        "PBO can operate up to motherboard limits."
    )
    assert payload["required_draft"]["statements"][0]["citation_passage_ids"] == [101]
    assert "presentation" not in payload
    assert "query" not in request.input_json
    assert "system_context" not in request.input_json
    assert "referral_pointers" not in request.input_json
    assert "passage_text" not in request.input_json
    assert "review_note" not in request.input_json


def test_parser_rejects_boolean_integer_impersonation():
    value = asdict(build_expected_draft(_envelope()))
    value["statements"][0]["claim_id"] = True
    with pytest.raises(ValueError):
        parse_provider_draft(json.dumps(value))


def test_provider_error_codes_are_closed_and_safe():
    with pytest.raises(ValueError, match="Unknown normalized provider error code"):
        ModelProviderError("raw-upstream-message", "unsafe")
