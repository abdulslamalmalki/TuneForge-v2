"""Provider-neutral interface policy and deterministic draft validation."""

from dataclasses import replace

from app.domain.guidance_read import (
    GuidanceExplanation,
    GuidanceReadCitation,
    GuidanceReadFact,
)
from app.domain.interface_contract import (
    OUTCOME_REFUSED,
    OUTCOME_SUPPORTED,
    PresentationStatement,
    build_deterministic_draft,
    validate_draft,
)
from app.normalization.interface_contract import interface_contract


def _citation(passage_id: int) -> GuidanceReadCitation:
    return GuidanceReadCitation(
        passage_id=passage_id,
        source_id=1,
        publisher_name="Advanced Micro Devices, Inc.",
        source_title="Ryzen Master User Guide",
        source_url="https://docs.amd.com/r/en-US/68886-ryzen-master-user-guide",
        document_id="68886",
        document_version="3.1.0",
        page_number=26,
        section_heading="Precision Boost Overdrive",
        passage_text="Immutable evidence passage.",
        text_sha256="a" * 64,
    )


def _explanation(*, with_facts: bool = True) -> GuidanceExplanation:
    facts = (
        (
            GuidanceReadFact(
                claim_id=10,
                claim_text="PPT represents total socket power.",
                claim_type="definition",
                applicability_scope="source_scope",
                citations=(_citation(100), _citation(101)),
            ),
            GuidanceReadFact(
                claim_id=11,
                claim_text="PBO may operate up to motherboard limits.",
                claim_type="behavior",
                applicability_scope="source_scope",
                citations=(_citation(102),),
            ),
        )
        if with_facts
        else ()
    )
    return GuidanceExplanation(
        canonical_id="precision-boost-overdrive",
        canonical_name="Precision Boost Overdrive",
        canonical_description="Reviewed description.",
        resolved_by="canonical_id",
        matched_input="precision-boost-overdrive",
        matched_alias=None,
        classification=None,
        system_context=None,
        context_notice=None,
        equivalent_vendor_names=(),
        facts=facts,
        answer_mode="accepted_claims_only",
        scope_notice=(
            "Accepted facts remain source-scoped."
            if with_facts
            else "No accepted guidance claims are available."
        ),
        recommendation_status="not_available",
        recommendation_notice="TuneForge will not invent a tuning plan.",
    )


def test_machine_contract_pins_provider_neutral_safety_rules():
    contract = interface_contract()
    assert contract["version"] == "1.0"
    assert contract["statement_policy"]["text"] == "exact_accepted_claim_text"
    assert contract["empty_retrieval_outcome"] == "refused"
    assert contract["referral_pointer_role"] == "navigation_only_not_evidence"
    assert contract["model_provider_status"] == "not_connected"
    assert contract["provider_abstraction_status"] == "available"
    assert contract["connected_provider_ids"] == []
    assert contract["deterministic_fallback_status"] == "required"
    assert "recommendation_generation" in contract["prohibited_capabilities"]


def test_deterministic_supported_packet_passes_unchanged():
    explanation = _explanation()
    draft = build_deterministic_draft(explanation)
    assert draft.outcome == OUTCOME_SUPPORTED
    assert [statement.claim_id for statement in draft.statements] == [10, 11]
    assert draft.statements[0].citation_passage_ids == (100, 101)
    assert validate_draft(explanation, draft).valid is True


def test_validator_rejects_paraphrase_and_incomplete_citations():
    explanation = _explanation()
    draft = build_deterministic_draft(explanation)
    changed = replace(
        draft.statements[0],
        text="PPT is the CPU power limit.",
        citation_passage_ids=(100,),
    )
    result = validate_draft(explanation, replace(draft, statements=(changed, draft.statements[1])))
    assert result.valid is False
    assert {issue.code for issue in result.issues} == {
        "claim_text_changed",
        "citation_set_changed",
    }


def test_validator_rejects_reordering_omission_and_invention():
    explanation = _explanation()
    draft = build_deterministic_draft(explanation)

    reordered = validate_draft(
        explanation,
        replace(draft, statements=tuple(reversed(draft.statements))),
    )
    assert "claim_order_or_identity_changed" in {issue.code for issue in reordered.issues}

    omitted = validate_draft(explanation, replace(draft, statements=draft.statements[:1]))
    assert "statement_count_mismatch" in {issue.code for issue in omitted.issues}

    invented_statement = PresentationStatement(
        claim_id=999,
        claim_type="behavior",
        applicability_scope="source_scope",
        text="Use a Scalar value of 10X.",
        citation_passage_ids=(),
    )
    invented = validate_draft(
        explanation,
        replace(draft, statements=(*draft.statements, invented_statement)),
    )
    assert "invented_statement" in {issue.code for issue in invented.issues}


def test_empty_retrieval_accepts_only_the_exact_refusal_packet():
    explanation = _explanation(with_facts=False)
    draft = build_deterministic_draft(explanation)
    assert draft.outcome == OUTCOME_REFUSED
    assert draft.statements == ()
    assert draft.refusal_message == explanation.scope_notice
    assert validate_draft(explanation, draft).valid is True

    unsafe = replace(
        draft,
        outcome=OUTCOME_SUPPORTED,
        refusal_message="No evidence, but common guidance suggests Auto.",
    )
    result = validate_draft(explanation, unsafe)
    assert result.valid is False
    assert {issue.code for issue in result.issues} == {
        "outcome_mismatch",
        "refusal_message_mismatch",
    }
