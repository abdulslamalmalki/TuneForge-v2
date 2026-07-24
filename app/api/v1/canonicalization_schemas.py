"""Protected API schemas for the conservative canonicalization reviewer."""

from pydantic import BaseModel, Field

from app.domain import canonical_identity_review, canonicalization


class CanonicalizationSignalOut(BaseModel):
    key: str
    label: str
    score_basis_points: int
    state: str
    detail: str


class CanonicalizationEntryOut(BaseModel):
    setting_id: int
    effective_name: str
    effective_description: str
    manual_id: int
    manual_title: str
    vendor_id: str | None
    vendor_name: str
    page_number: int
    source_url: str | None
    candidate_canonical_id: str | None
    candidate_canonical_name: str | None
    confidence_basis_points: int
    source_trusted: bool
    automatic_eligible: bool
    reasons: list[str]
    signals: list[CanonicalizationSignalOut]
    support_setting_ids: list[int]
    reviewed_menu_path: str | None
    proposed_new_canonical_id: str | None
    proposed_group_size: int
    proposed_vendor_count: int

    @classmethod
    def from_domain(
        cls,
        value: canonicalization.CanonicalizationEntry,
    ) -> "CanonicalizationEntryOut":
        return cls(
            **{
                key: getattr(value, key)
                for key in (
                    "setting_id",
                    "effective_name",
                    "effective_description",
                    "manual_id",
                    "manual_title",
                    "vendor_id",
                    "vendor_name",
                    "page_number",
                    "source_url",
                    "candidate_canonical_id",
                    "candidate_canonical_name",
                    "confidence_basis_points",
                    "source_trusted",
                    "automatic_eligible",
                    "reviewed_menu_path",
                    "proposed_new_canonical_id",
                    "proposed_group_size",
                    "proposed_vendor_count",
                )
            },
            reasons=list(value.reasons),
            signals=[
                CanonicalizationSignalOut.model_validate(signal, from_attributes=True)
                for signal in value.signals
            ],
            support_setting_ids=list(value.support_setting_ids),
        )


class ProposedCanonicalGroupOut(BaseModel):
    canonical_id: str
    canonical_name: str
    description: str
    setting_ids: list[int]
    manual_ids: list[int]
    vendor_ids: list[str]
    confidence_basis_points: int
    automatic_eligible: bool
    reasons: list[str]
    proposal_id: int | None
    proposal_status: str
    reviewed_menu_paths: list[str]

    @classmethod
    def from_domain(
        cls,
        value: canonicalization.ProposedCanonicalGroup,
    ) -> "ProposedCanonicalGroupOut":
        return cls(
            canonical_id=value.canonical_id,
            canonical_name=value.canonical_name,
            description=value.description,
            setting_ids=list(value.setting_ids),
            manual_ids=list(value.manual_ids),
            vendor_ids=list(value.vendor_ids),
            confidence_basis_points=value.confidence_basis_points,
            automatic_eligible=value.automatic_eligible,
            reasons=list(value.reasons),
            proposal_id=value.proposal_id,
            proposal_status=value.proposal_status,
            reviewed_menu_paths=list(value.reviewed_menu_paths),
        )


class CanonicalizationBacklogOut(BaseModel):
    items: list[CanonicalizationEntryOut]
    total: int
    page: int
    page_size: int
    pages: int
    proposed_groups: list[ProposedCanonicalGroupOut]


class CanonicalizationReportItemOut(BaseModel):
    setting_id: int
    setting_name: str
    canonical_id: str
    canonical_name: str
    confidence_basis_points: int
    reasons: list[str]
    support_setting_ids: list[int]


class CanonicalizationRunReportOut(BaseModel):
    run_id: str
    engine_version: str
    confidence_threshold_basis_points: int
    evaluated_count: int
    auto_approved_mappings: list[CanonicalizationReportItemOut]
    new_canonical_settings: list[ProposedCanonicalGroupOut]
    remaining_manual_review_count: int
    pending_suggestion_count: int
    identity_proposals_created_count: int
    identity_proposals_refreshed_count: int

    @classmethod
    def from_domain(
        cls,
        value: canonicalization.CanonicalizationRunReport,
    ) -> "CanonicalizationRunReportOut":
        return cls(
            run_id=value.run_id,
            engine_version=value.engine_version,
            confidence_threshold_basis_points=value.confidence_threshold_basis_points,
            evaluated_count=value.evaluated_count,
            auto_approved_mappings=[
                CanonicalizationReportItemOut(
                    setting_id=item.setting_id,
                    setting_name=item.setting_name,
                    canonical_id=item.canonical_id,
                    canonical_name=item.canonical_name,
                    confidence_basis_points=item.confidence_basis_points,
                    reasons=list(item.reasons),
                    support_setting_ids=list(item.support_setting_ids),
                )
                for item in value.auto_approved_mappings
            ],
            new_canonical_settings=[
                ProposedCanonicalGroupOut.from_domain(item)
                for item in value.new_canonical_settings
            ],
            remaining_manual_review_count=value.remaining_manual_review_count,
            pending_suggestion_count=value.pending_suggestion_count,
            identity_proposals_created_count=value.identity_proposals_created_count,
            identity_proposals_refreshed_count=value.identity_proposals_refreshed_count,
        )


class CanonicalizationReverseIn(BaseModel):
    reason: str


class CanonicalizationReverseOut(BaseModel):
    setting_id: int
    reversed: bool


class MenuContextCitationIn(BaseModel):
    page_number: int
    evidence_excerpt: str


class MenuContextReviewIn(BaseModel):
    menu_path: str
    source_page_number: int
    evidence_excerpt: str
    supporting_citations: list[MenuContextCitationIn] = Field(default_factory=list)
    note: str = ""


class MenuContextRevokeIn(BaseModel):
    reason: str


class MenuContextCitationOut(BaseModel):
    page_number: int
    evidence_excerpt: str


class MenuContextOut(BaseModel):
    setting_id: int
    menu_path: str
    normalized_menu_path: str
    source_page_number: int
    evidence_excerpt: str
    status: str
    actor: str
    reviewed_at: str
    updated_at: str
    manual_id: int
    manual_title: str
    source_url: str | None
    citations: list[MenuContextCitationOut]

    @classmethod
    def from_domain(
        cls,
        value: canonical_identity_review.MenuContextView,
    ) -> "MenuContextOut":
        return cls(
            **{
                key: getattr(value, key)
                for key in (
                    "setting_id",
                    "menu_path",
                    "normalized_menu_path",
                    "source_page_number",
                    "evidence_excerpt",
                    "status",
                    "actor",
                    "reviewed_at",
                    "updated_at",
                    "manual_id",
                    "manual_title",
                    "source_url",
                )
            },
            citations=[
                MenuContextCitationOut.model_validate(citation, from_attributes=True)
                for citation in value.citations
            ],
        )


class IdentityProposalSourceOut(BaseModel):
    setting_id: int
    setting_name: str
    manual_id: int
    manual_title: str
    vendor_id: str | None
    vendor_name: str
    page_number: int
    menu_context: MenuContextOut | None


class IdentityProposalOut(BaseModel):
    proposal_id: int
    canonical_id: str
    canonical_name: str
    description: str
    status: str
    confidence_basis_points: int
    engine_version: str
    review_note: str | None
    actor: str | None
    approved_canonical_id: str | None
    created_at: str
    updated_at: str
    reviewed_at: str | None
    sources: list[IdentityProposalSourceOut]
    approval_ready: bool
    readiness_reasons: list[str]

    @classmethod
    def from_domain(
        cls,
        value: canonical_identity_review.IdentityProposalView,
    ) -> "IdentityProposalOut":
        return cls(
            **{
                key: getattr(value, key)
                for key in (
                    "proposal_id",
                    "canonical_id",
                    "canonical_name",
                    "description",
                    "status",
                    "confidence_basis_points",
                    "engine_version",
                    "review_note",
                    "actor",
                    "approved_canonical_id",
                    "created_at",
                    "updated_at",
                    "reviewed_at",
                    "approval_ready",
                )
            },
            sources=[
                IdentityProposalSourceOut(
                    setting_id=source.setting_id,
                    setting_name=source.setting_name,
                    manual_id=source.manual_id,
                    manual_title=source.manual_title,
                    vendor_id=source.vendor_id,
                    vendor_name=source.vendor_name,
                    page_number=source.page_number,
                    menu_context=(
                        MenuContextOut.from_domain(source.menu_context)
                        if source.menu_context
                        else None
                    ),
                )
                for source in value.sources
            ],
            readiness_reasons=list(value.readiness_reasons),
        )


class IdentityProposalReviewIn(BaseModel):
    note: str = ""
    reason: str = ""


class CanonicalIdentityStatusIn(BaseModel):
    reason: str


class CanonicalIdentityStatusOut(BaseModel):
    canonical_id: str
    status: str
