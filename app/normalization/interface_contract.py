"""Versioned, machine-readable policy for an optional presentation model."""

INTERFACE_CONTRACT_VERSION = "1.0"

OUTCOMES = ("supported", "refused")
STATEMENT_POLICY = {
    "text": "exact_accepted_claim_text",
    "claim_cardinality": "one_statement_per_claim",
    "citation_policy": "complete_retrieved_passage_id_set",
    "ordering": "claim_type_then_claim_id",
}
REQUIRED_NOTICES = (
    "scope_notice",
    "recommendation_notice",
)
PROHIBITED_CAPABILITIES = (
    "model_memory_facts",
    "live_web_retrieval",
    "claim_paraphrase",
    "claim_merging",
    "evidence_ranking",
    "disagreement_reconciliation",
    "recommendation_generation",
    "exact_hardware_inference",
    "bios_version_inference",
)
VALIDATION_FAILURE_ACTION = "discard_model_draft_use_deterministic_packet"


def interface_contract(
    connected_provider_ids: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "version": INTERFACE_CONTRACT_VERSION,
        "outcomes": list(OUTCOMES),
        "statement_policy": dict(STATEMENT_POLICY),
        "required_notices": list(REQUIRED_NOTICES),
        "prohibited_capabilities": list(PROHIBITED_CAPABILITIES),
        "empty_retrieval_outcome": "refused",
        "referral_pointer_role": "navigation_only_not_evidence",
        "validation_failure_action": VALIDATION_FAILURE_ACTION,
        "natural_language_routing_status": "limited_deterministic",
        "model_provider_status": "connected" if connected_provider_ids else "not_connected",
        "provider_abstraction_status": "available",
        "connected_provider_ids": list(connected_provider_ids),
        "deterministic_fallback_status": "required",
    }
