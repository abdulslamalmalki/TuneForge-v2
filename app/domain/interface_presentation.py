"""Citation-safe presentation envelopes for a future optional model interface."""

from dataclasses import dataclass

from app.domain.canonical_settings import CanonicalClassificationView, VendorEquivalentName
from app.domain.guidance_read import GuidanceExplanation
from app.domain.interface_contract import (
    OUTCOME_REFUSED,
    OUTCOME_SUPPORTED,
    build_deterministic_draft,
    validate_draft,
)
from app.domain.refusal_gaps import NearestCanonicalMatch, ReferralPointerView
from app.domain.system_context import SystemContext
from app.normalization.interface_contract import INTERFACE_CONTRACT_VERSION


@dataclass(frozen=True)
class PresentationCitation:
    passage_id: int | None
    source_id: int
    publisher_name: str
    source_title: str
    source_url: str
    document_id: str | None
    document_version: str
    page_number: int
    section_heading: str | None
    text_sha256: str
    source_kind: str = "evidence_passage"
    setting_id: int | None = None
    manual_id: int | None = None


@dataclass(frozen=True)
class PresentationHardwareScope:
    target_type: str
    target_name: str
    evidence_level: str
    evidence_basis: str


@dataclass(frozen=True)
class PresentationBiosScope:
    context_name: str
    version_label: str
    comparison_mode: str
    version_notes: str
    evidence_basis: str


@dataclass(frozen=True)
class PresentationFact:
    claim_id: int
    claim_type: str
    applicability_scope: str
    text: str
    citations: tuple[PresentationCitation, ...]
    section_label: str = "Reviewed fact"
    hardware_match_status: str = "unscoped"
    bios_match_status: str = "unscoped"
    hardware_scopes: tuple[PresentationHardwareScope, ...] = ()
    bios_scopes: tuple[PresentationBiosScope, ...] = ()


@dataclass(frozen=True)
class PresentationConflict:
    conflict_id: int
    left_claim_id: int
    right_claim_id: int
    status: str
    hardware_relationship: str
    bios_relationship: str
    reviewer_note: str


@dataclass(frozen=True)
class PresentationReferral:
    pointer_id: int
    publisher_id: str
    publisher_name: str
    title: str
    source_url: str
    document_id: str | None
    document_version: str


@dataclass(frozen=True)
class PresentationSuggestion:
    canonical_id: str
    canonical_name: str
    matched_name: str
    match_kind: str
    score: float


@dataclass(frozen=True)
class PresentationEnvelope:
    contract_version: str
    outcome: str
    resolution_status: str
    query: str
    canonical_id: str | None
    canonical_name: str | None
    canonical_description: str | None
    resolved_by: str | None
    matched_alias: str | None
    classification: CanonicalClassificationView | None
    system_context: SystemContext | None
    context_notice: str | None
    equivalent_vendor_names: tuple[VendorEquivalentName, ...]
    statements: tuple[PresentationFact, ...]
    scope_notice: str
    recommendation_status: str
    recommendation_notice: str
    refusal_message: str | None
    referral_pointers: tuple[PresentationReferral, ...]
    knowledge_conflicts: tuple[PresentationConflict, ...] = ()
    answer_state: str = "No Verified Answer"
    suggestions: tuple[PresentationSuggestion, ...] = ()


def build_resolved_envelope(
    explanation: GuidanceExplanation,
    *,
    referral_pointers: list[ReferralPointerView] | tuple[ReferralPointerView, ...] = (),
) -> PresentationEnvelope:
    """Project retrieved facts without exposing passage text or reviewer audit details."""

    draft = build_deterministic_draft(explanation)
    validation = validate_draft(explanation, draft)
    if not validation.valid:
        raise RuntimeError("Deterministic presentation packet failed its own contract.")
    statements = tuple(
        PresentationFact(
            claim_id=fact.claim_id,
            claim_type=fact.claim_type,
            applicability_scope=fact.applicability_scope,
            text=fact.claim_text,
            citations=tuple(
                PresentationCitation(
                    passage_id=citation.passage_id,
                    source_id=citation.source_id,
                    publisher_name=citation.publisher_name,
                    source_title=citation.source_title,
                    source_url=citation.source_url,
                    document_id=citation.document_id,
                    document_version=citation.document_version,
                    page_number=citation.page_number,
                    section_heading=citation.section_heading,
                    text_sha256=citation.text_sha256,
                    source_kind=citation.source_kind,
                    setting_id=citation.setting_id,
                    manual_id=citation.manual_id,
                )
                for citation in fact.citations
            ),
            section_label={
                "definition": "What it does",
                "behavior": "What changing it does",
                "constraint": "Available modes / constraints",
                "safety": "Risks / cautions",
            }[fact.claim_type],
            hardware_match_status=fact.hardware_match_status,
            bios_match_status=fact.bios_match_status,
            hardware_scopes=tuple(
                PresentationHardwareScope(
                    target_type=target.target_type,
                    target_name=target.canonical_name,
                    evidence_level=target.evidence_level,
                    evidence_basis=target.evidence_basis,
                )
                for target in fact.applicability_targets
            ),
            bios_scopes=tuple(
                PresentationBiosScope(
                    context_name=scope.context_name,
                    version_label=_bios_version_label(scope),
                    comparison_mode=scope.comparison_mode,
                    version_notes=scope.version_notes,
                    evidence_basis=scope.evidence_basis,
                )
                for scope in fact.bios_scopes
            ),
        )
        for fact in explanation.facts
    )
    return PresentationEnvelope(
        contract_version=INTERFACE_CONTRACT_VERSION,
        outcome=OUTCOME_SUPPORTED if statements else OUTCOME_REFUSED,
        resolution_status="resolved",
        answer_state=explanation.answer_state,
        query=explanation.matched_input,
        canonical_id=explanation.canonical_id,
        canonical_name=explanation.canonical_name,
        canonical_description=explanation.canonical_description,
        resolved_by=explanation.resolved_by,
        matched_alias=explanation.matched_alias,
        classification=explanation.classification,
        system_context=explanation.system_context,
        context_notice=explanation.context_notice,
        equivalent_vendor_names=explanation.equivalent_vendor_names,
        statements=statements,
        knowledge_conflicts=tuple(
            PresentationConflict(
                conflict_id=item.conflict.id,
                left_claim_id=item.conflict.left_claim_id,
                right_claim_id=item.conflict.right_claim_id,
                status=item.conflict.status,
                hardware_relationship=item.conflict.hardware_relationship,
                bios_relationship=item.conflict.bios_relationship,
                reviewer_note=item.conflict.reviewer_note or "",
            )
            for item in explanation.knowledge_conflicts
        ),
        scope_notice=explanation.scope_notice,
        recommendation_status=explanation.recommendation_status,
        recommendation_notice=explanation.recommendation_notice,
        refusal_message=None if statements else explanation.scope_notice,
        referral_pointers=_referrals(referral_pointers),
    )


def build_unresolved_envelope(
    *,
    query: str,
    refusal_message: str,
    referral_pointers: list[ReferralPointerView] | tuple[ReferralPointerView, ...] = (),
    suggestions: tuple[NearestCanonicalMatch, ...] = (),
) -> PresentationEnvelope:
    return PresentationEnvelope(
        contract_version=INTERFACE_CONTRACT_VERSION,
        outcome=OUTCOME_REFUSED,
        resolution_status="unresolved",
        answer_state="No Verified Answer",
        query=" ".join(query.split()),
        canonical_id=None,
        canonical_name=None,
        canonical_description=None,
        resolved_by=None,
        matched_alias=None,
        classification=None,
        system_context=None,
        context_notice=None,
        equivalent_vendor_names=(),
        statements=(),
        knowledge_conflicts=(),
        scope_notice="No reviewed canonical identity was resolved for this lookup.",
        recommendation_status="not_available",
        recommendation_notice=(
            "TuneForge will not use model memory, unreviewed sources, or referral links "
            "to manufacture an answer."
        ),
        refusal_message=refusal_message,
        referral_pointers=_referrals(referral_pointers),
        suggestions=tuple(
            PresentationSuggestion(
                canonical_id=item.canonical_id,
                canonical_name=item.canonical_name,
                matched_name=item.matched_name,
                match_kind=item.match_kind,
                score=item.score,
            )
            for item in suggestions
        ),
    )


def build_capability_refusal_envelope(
    *,
    query: str,
    refusal_message: str,
    canonical_id: str | None = None,
    canonical_name: str | None = None,
    canonical_description: str | None = None,
    classification: CanonicalClassificationView | None = None,
    system_context: SystemContext | None = None,
    context_notice: str | None = None,
    equivalent_vendor_names: tuple[VendorEquivalentName, ...] = (),
    referral_pointers: list[ReferralPointerView] | tuple[ReferralPointerView, ...] = (),
) -> PresentationEnvelope:
    """Refuse a known question whose reviewed product capability is unavailable."""

    return PresentationEnvelope(
        contract_version=INTERFACE_CONTRACT_VERSION,
        outcome=OUTCOME_REFUSED,
        resolution_status="capability_unavailable",
        answer_state="No Verified Answer",
        query=" ".join(query.split()),
        canonical_id=canonical_id,
        canonical_name=canonical_name,
        canonical_description=canonical_description,
        resolved_by="guided_question" if canonical_id else None,
        matched_alias=None,
        classification=classification,
        system_context=system_context,
        context_notice=context_notice,
        equivalent_vendor_names=equivalent_vendor_names,
        statements=(),
        knowledge_conflicts=(),
        scope_notice=refusal_message,
        recommendation_status="not_available",
        recommendation_notice=(
            "TuneForge will not infer an unavailable recommendation, exact-system, or "
            "BIOS-version capability from related accepted facts."
        ),
        refusal_message=refusal_message,
        referral_pointers=_referrals(referral_pointers),
    )


def _referrals(
    pointers: list[ReferralPointerView] | tuple[ReferralPointerView, ...],
) -> tuple[PresentationReferral, ...]:
    return tuple(
        PresentationReferral(
            pointer_id=pointer.id,
            publisher_id=pointer.publisher_id,
            publisher_name=pointer.publisher_name,
            title=pointer.title,
            source_url=pointer.source_url,
            document_id=pointer.document_id,
            document_version=pointer.document_version,
        )
        for pointer in pointers
    )


def _bios_version_label(scope) -> str:
    if scope.bios_version_exact:
        return f"exact {scope.bios_version_exact}"
    if scope.valid_from or scope.valid_until:
        return f"{scope.valid_from or 'open'} through {scope.valid_until or 'open'}"
    markers = []
    if scope.introduced_in:
        markers.append(f"introduced {scope.introduced_in}")
    if scope.changed_in:
        markers.append(f"changed {scope.changed_in}")
    if scope.deprecated_in:
        markers.append(f"deprecated {scope.deprecated_in}")
    return "; ".join(markers)
