"""Response schemas for API v1.

Thin serialization over the domain's objects — every value comes from a
domain function or ORM row; nothing is computed here beyond field mapping.
"""

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import (
    CanonicalMappingEvent,
    ExtractionRun,
    Manual,
    ManualEvent,
    MissedSetting,
    ReviewEvent,
    Setting,
)
from app.domain.acceptance import AcceptanceReport, PageReferenceRow
from app.domain.am5_coverage import Am5CoverageReport
from app.domain.am5_question_acceptance import Am5QuestionAcceptanceReport
from app.domain.batches import BatchOverview
from app.domain.canonical_settings import (
    CanonicalCatalogRow,
    CanonicalClassificationView,
    CanonicalDetail,
    CanonicalMappingView,
    CanonicalSuggestion,
    VendorEquivalentName,
)
from app.domain.canonical_suggestions import SuggestionQueueRow
from app.domain.evidence_sources import EvidenceSourceDetail, EvidenceSourceOverview
from app.domain.guidance_claims import (
    ApplicabilityTargetInput,
    BiosScopeInput,
    GuidanceClaimDetail,
    GuidanceClaimOverview,
)
from app.domain.guidance_conflicts import ConflictDetail, ConflictOverview, ConflictScanReport
from app.domain.guidance_read import GuidanceCoverageRow, GuidanceExplanation
from app.domain.guided_answers import GuidedAnswer, GuidedQuestion
from app.domain.hardware import ApplicabilityView
from app.domain.interface_presentation import PresentationEnvelope
from app.domain.manuals import ManualOverview, missing_provenance_fields
from app.domain.recommendation_claims import (
    RecommendationClaimDetail,
    RecommendationClaimOverview,
)
from app.domain.refusal_gaps import (
    LookupGap,
    LookupGapSummary,
    NearestCanonicalMatch,
    ReferralPointerEventView,
    ReferralPointerView,
)
from app.domain.review import DraftQueueRow, effective_description, effective_name
from app.domain.system_context import ContextApplicabilityMatch, SystemContext
from app.research.trusted_web import TrustedWebResearchView, TrustedWebSourceView
from app.search.query import SearchResult


class ErrorOut(BaseModel):
    """Every API error body: a human-readable detail (404, 400, 500)."""

    detail: str


class NearestCanonicalMatchOut(BaseModel):
    canonical_id: str
    canonical_name: str
    matched_name: str
    match_kind: str
    score: float

    @classmethod
    def from_match(cls, match: NearestCanonicalMatch) -> "NearestCanonicalMatchOut":
        return cls.model_validate(match, from_attributes=True)


class ReferralPointerEventOut(BaseModel):
    id: int
    action: str
    actor: str
    detail: str
    created_at: str

    @classmethod
    def from_view(cls, view: ReferralPointerEventView) -> "ReferralPointerEventOut":
        return cls.model_validate(view, from_attributes=True)


class ReferralPointerOut(BaseModel):
    id: int
    publisher_id: str
    publisher_name: str
    title: str
    source_url: str
    document_id: str | None
    document_version: str
    status: str
    reviewer: str
    review_note: str
    canonical_ids: list[str]
    created_at: str
    retired_at: str | None
    events: list[ReferralPointerEventOut]

    @classmethod
    def from_view(cls, view: ReferralPointerView) -> "ReferralPointerOut":
        return cls.model_validate(view, from_attributes=True)


class ReferralPointerCreateIn(BaseModel):
    publisher_id: str = ""
    title: str = ""
    source_url: str = ""
    document_id: str = ""
    document_version: str = ""
    canonical_ids: list[str] = Field(default_factory=list)
    review_note: str = ""


class ReferralPointerRetireIn(BaseModel):
    review_note: str = ""


class LookupGapOut(BaseModel):
    id: int
    surface: str
    reason: str
    query_text: str
    normalized_query: str
    nearest_matches: list[NearestCanonicalMatchOut]
    context: dict[str, str]
    created_at: str

    @classmethod
    def from_gap(cls, gap: LookupGap) -> "LookupGapOut":
        return cls.model_validate(gap, from_attributes=True)


class LookupGapSummaryOut(BaseModel):
    surface: str
    reason: str
    query_text: str
    normalized_query: str
    occurrence_count: int
    first_seen_at: str
    last_seen_at: str
    nearest_matches: list[NearestCanonicalMatchOut]
    context: dict[str, str]

    @classmethod
    def from_summary(cls, summary: LookupGapSummary) -> "LookupGapSummaryOut":
        return cls.model_validate(summary, from_attributes=True)


class RefusalErrorOut(ErrorOut):
    referral_pointers: list[ReferralPointerOut] = Field(default_factory=list)


class EvidenceSourceReviewIn(BaseModel):
    decision: str = ""
    note: str = ""


class EvidenceSourceSummaryOut(BaseModel):
    id: int
    publisher_id: str
    publisher_name: str
    title: str
    document_id: str | None
    document_version: str
    published_on: str
    status: str
    page_count: int
    extraction_status: str
    passage_count: int

    @classmethod
    def from_overview(cls, overview: EvidenceSourceOverview) -> "EvidenceSourceSummaryOut":
        source = overview.source
        return cls(
            id=source.id,
            publisher_id=source.publisher_id,
            publisher_name=source.publisher_name,
            title=source.title,
            document_id=source.document_id,
            document_version=source.document_version,
            published_on=source.published_on,
            status=source.status,
            page_count=source.page_count,
            extraction_status=overview.extraction_status,
            passage_count=overview.passage_count,
        )


class EvidenceExtractionRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    extractor_version: str | None
    pages_processed: int
    passages_found: int
    error_message: str | None
    started_at: str | None
    finished_at: str | None
    created_at: str


class EvidencePassageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ordinal: int
    page_number: int
    section_heading: str | None
    passage_text: str
    text_sha256: str


class EvidenceSourceEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    action: str
    actor: str
    detail: str | None
    created_at: str


class EvidenceSourceDetailOut(BaseModel):
    id: int
    publisher_id: str
    publisher_name: str
    title: str
    source_type: str
    trust_tier: str
    source_url: str
    document_id: str | None
    document_version: str
    published_on: str
    retrieved_at: str
    language: str
    original_filename: str
    sha256: str
    file_size_bytes: int
    page_count: int
    status: str
    reviewer: str | None
    reviewed_at: str | None
    review_note: str | None
    created_at: str
    extraction_run: EvidenceExtractionRunOut
    passages: list[EvidencePassageOut]
    events: list[EvidenceSourceEventOut]

    @classmethod
    def from_detail(cls, detail: EvidenceSourceDetail) -> "EvidenceSourceDetailOut":
        source = detail.source
        return cls(
            id=source.id,
            publisher_id=source.publisher_id,
            publisher_name=source.publisher_name,
            title=source.title,
            source_type=source.source_type,
            trust_tier=source.trust_tier,
            source_url=source.source_url,
            document_id=source.document_id,
            document_version=source.document_version,
            published_on=source.published_on,
            retrieved_at=source.retrieved_at,
            language=source.language,
            original_filename=source.original_filename,
            sha256=source.sha256,
            file_size_bytes=source.file_size_bytes,
            page_count=source.page_count,
            status=source.status,
            reviewer=source.reviewer,
            reviewed_at=source.reviewed_at,
            review_note=source.review_note,
            created_at=source.created_at,
            extraction_run=EvidenceExtractionRunOut.model_validate(detail.extraction_run),
            passages=[EvidencePassageOut.model_validate(row) for row in detail.passages],
            events=[EvidenceSourceEventOut.model_validate(row) for row in detail.events],
        )


class GuidanceApplicabilityTargetIn(BaseModel):
    target_type: str = ""
    target_id: str = ""
    evidence_basis: str = ""

    def to_domain(self) -> ApplicabilityTargetInput:
        return ApplicabilityTargetInput(
            target_type=self.target_type,
            target_id=self.target_id,
            evidence_basis=self.evidence_basis,
        )


class GuidanceBiosScopeIn(BaseModel):
    context_type: str = ""
    context_id: str = ""
    bios_version_exact: str = ""
    valid_from: str = ""
    valid_until: str = ""
    introduced_in: str = ""
    changed_in: str = ""
    deprecated_in: str = ""
    comparison_mode: str = "opaque"
    version_notes: str = ""
    evidence_basis: str = ""

    def to_domain(self) -> BiosScopeInput:
        return BiosScopeInput(**self.model_dump())


class CreateGuidanceClaimIn(BaseModel):
    claim_text: str = ""
    claim_type: str = ""
    passage_ids: list[int] = Field(default_factory=list)
    source_setting_ids: list[int] = Field(default_factory=list)
    canonical_ids: list[str] = Field(default_factory=list)
    applicability_scope: str = "source_scope"
    cpu_model_id: str | None = None
    applicability_targets: list[GuidanceApplicabilityTargetIn] = Field(default_factory=list)
    bios_scopes: list[GuidanceBiosScopeIn] = Field(default_factory=list)


class EditGuidanceClaimIn(CreateGuidanceClaimIn):
    pass


class ReviewGuidanceClaimIn(BaseModel):
    decision: str = ""
    note: str = ""


class GuidanceClaimSummaryOut(BaseModel):
    id: int
    claim_text: str
    claim_type: str
    applicability_scope: str
    cpu_model_id: str | None
    status: str
    evidence_count: int
    setting_count: int
    created_at: str
    updated_at: str

    @classmethod
    def from_overview(cls, overview: GuidanceClaimOverview) -> "GuidanceClaimSummaryOut":
        claim = overview.claim
        return cls(
            id=claim.id,
            claim_text=claim.claim_text,
            claim_type=claim.claim_type,
            applicability_scope=claim.applicability_scope,
            cpu_model_id=claim.cpu_model_id,
            status=claim.status,
            evidence_count=overview.evidence_count,
            setting_count=overview.setting_count,
            created_at=claim.created_at,
            updated_at=claim.updated_at,
        )


class GuidanceCitationOut(BaseModel):
    passage_id: int
    source_id: int
    source_title: str
    document_id: str | None
    document_version: str
    page_number: int
    section_heading: str | None
    passage_text: str
    text_sha256: str


class GuidanceSourceSettingCitationOut(BaseModel):
    setting_id: int
    manual_id: int
    source_title: str
    source_url: str | None
    document_version: str | None
    page_number: int
    passage_text: str


class GuidanceSettingOut(BaseModel):
    canonical_id: str
    canonical_name: str


class GuidanceApplicabilityTargetOut(BaseModel):
    target_type: str
    target_id: str
    canonical_name: str
    evidence_level: str
    evidence_basis: str


class GuidanceBiosScopeOut(BaseModel):
    context_type: str
    context_id: str
    context_name: str
    bios_version_exact: str | None
    valid_from: str | None
    valid_until: str | None
    introduced_in: str | None
    changed_in: str | None
    deprecated_in: str | None
    comparison_mode: str
    version_notes: str
    evidence_basis: str


class GuidanceClaimEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    action: str
    actor: str
    detail: str | None
    created_at: str


class GuidanceClaimDetailOut(BaseModel):
    id: int
    claim_text: str
    claim_type: str
    applicability_scope: str
    cpu_model_id: str | None
    cpu_model_name: str | None
    status: str
    review_note: str | None
    reviewer: str | None
    reviewed_at: str | None
    created_at: str
    updated_at: str
    evidence: list[GuidanceCitationOut]
    source_settings: list[GuidanceSourceSettingCitationOut]
    settings: list[GuidanceSettingOut]
    applicability_targets: list[GuidanceApplicabilityTargetOut]
    bios_scopes: list[GuidanceBiosScopeOut]
    events: list[GuidanceClaimEventOut]

    @classmethod
    def from_detail(cls, detail: GuidanceClaimDetail) -> "GuidanceClaimDetailOut":
        claim = detail.claim
        return cls(
            id=claim.id,
            claim_text=claim.claim_text,
            claim_type=claim.claim_type,
            applicability_scope=claim.applicability_scope,
            cpu_model_id=claim.cpu_model_id,
            cpu_model_name=detail.cpu_model.canonical_name if detail.cpu_model else None,
            status=claim.status,
            review_note=claim.review_note,
            reviewer=claim.reviewer,
            reviewed_at=claim.reviewed_at,
            created_at=claim.created_at,
            updated_at=claim.updated_at,
            evidence=[
                GuidanceCitationOut(
                    passage_id=citation.passage.id,
                    source_id=citation.source.id,
                    source_title=citation.source.title,
                    document_id=citation.source.document_id,
                    document_version=citation.source.document_version,
                    page_number=citation.passage.page_number,
                    section_heading=citation.passage.section_heading,
                    passage_text=citation.passage.passage_text,
                    text_sha256=citation.passage.text_sha256,
                )
                for citation in detail.evidence
            ],
            source_settings=[
                GuidanceSourceSettingCitationOut(
                    setting_id=citation.setting.id,
                    manual_id=citation.manual.id,
                    source_title=(
                        citation.manual.document_title or citation.manual.original_filename
                    ),
                    source_url=citation.manual.source_url,
                    document_version=citation.manual.document_version,
                    page_number=(
                        citation.setting.edited_page_number or citation.setting.page_number
                    ),
                    passage_text=effective_description(citation.setting),
                )
                for citation in detail.source_settings
            ],
            settings=[
                GuidanceSettingOut(
                    canonical_id=setting.canonical_id,
                    canonical_name=setting.canonical_name,
                )
                for setting in detail.settings
            ],
            applicability_targets=[
                GuidanceApplicabilityTargetOut.model_validate(target, from_attributes=True)
                for target in detail.applicability_targets
            ],
            bios_scopes=[
                GuidanceBiosScopeOut.model_validate(scope, from_attributes=True)
                for scope in detail.bios_scopes
            ],
            events=[GuidanceClaimEventOut.model_validate(event) for event in detail.events],
        )


class RecommendationClaimIn(BaseModel):
    canonical_id: str = ""
    recommendation_text: str = ""
    recommended_action: str = ""
    exact_value: str = ""
    minimum_value: str = ""
    maximum_value: str = ""
    value_unit: str = ""
    use_case: str = ""
    risk_level: str = ""
    prerequisites: list[str] = Field(default_factory=list)
    confidence_level: str = ""
    conflict_state: str = "not_assessed"
    passage_ids: list[int] = Field(default_factory=list)
    source_setting_ids: list[int] = Field(default_factory=list)
    applicability_targets: list[GuidanceApplicabilityTargetIn] = Field(default_factory=list)
    bios_scopes: list[GuidanceBiosScopeIn] = Field(default_factory=list)

    def domain_values(self) -> dict[str, object]:
        values = self.model_dump(
            exclude={"applicability_targets", "bios_scopes"}
        )
        values["applicability_targets"] = tuple(
            target.to_domain() for target in self.applicability_targets
        )
        values["bios_scopes"] = tuple(scope.to_domain() for scope in self.bios_scopes)
        return values


class ReviewRecommendationClaimIn(BaseModel):
    decision: str = ""
    note: str = ""


class RecommendationClaimSummaryOut(BaseModel):
    id: int
    canonical_id: str
    canonical_name: str
    recommendation_text: str
    recommended_action: str
    value_mode: str
    use_case: str
    risk_level: str
    confidence_level: str
    conflict_state: str
    status: str
    evidence_count: int
    hardware_target_count: int
    bios_scope_count: int
    created_at: str
    updated_at: str

    @classmethod
    def from_overview(
        cls, value: RecommendationClaimOverview
    ) -> "RecommendationClaimSummaryOut":
        claim = value.claim
        return cls(
            id=claim.id,
            canonical_id=claim.canonical_id,
            canonical_name=value.canonical_name,
            recommendation_text=claim.recommendation_text,
            recommended_action=claim.recommended_action,
            value_mode=claim.value_mode,
            use_case=claim.use_case,
            risk_level=claim.risk_level,
            confidence_level=claim.confidence_level,
            conflict_state=claim.conflict_state,
            status=claim.status,
            evidence_count=value.evidence_count,
            hardware_target_count=value.hardware_target_count,
            bios_scope_count=value.bios_scope_count,
            created_at=claim.created_at,
            updated_at=claim.updated_at,
        )


class RecommendationClaimDetailOut(BaseModel):
    id: int
    canonical_id: str
    canonical_name: str
    recommendation_text: str
    recommended_action: str
    value_mode: str
    exact_value: str | None
    minimum_value: str | None
    maximum_value: str | None
    value_unit: str | None
    use_case: str
    risk_level: str
    prerequisites: list[str]
    confidence_level: str
    conflict_state: str
    status: str
    review_note: str | None
    reviewer: str | None
    reviewed_at: str | None
    created_at: str
    updated_at: str
    evidence: list[GuidanceCitationOut]
    source_settings: list[GuidanceSourceSettingCitationOut]
    applicability_targets: list[GuidanceApplicabilityTargetOut]
    bios_scopes: list[GuidanceBiosScopeOut]
    events: list[GuidanceClaimEventOut]

    @classmethod
    def from_detail(
        cls, detail: RecommendationClaimDetail
    ) -> "RecommendationClaimDetailOut":
        claim = detail.claim
        return cls(
            id=claim.id,
            canonical_id=claim.canonical_id,
            canonical_name=detail.canonical_setting.canonical_name,
            recommendation_text=claim.recommendation_text,
            recommended_action=claim.recommended_action,
            value_mode=claim.value_mode,
            exact_value=claim.exact_value,
            minimum_value=claim.minimum_value,
            maximum_value=claim.maximum_value,
            value_unit=claim.value_unit,
            use_case=claim.use_case,
            risk_level=claim.risk_level,
            prerequisites=list(detail.prerequisites),
            confidence_level=claim.confidence_level,
            conflict_state=claim.conflict_state,
            status=claim.status,
            review_note=claim.review_note,
            reviewer=claim.reviewer,
            reviewed_at=claim.reviewed_at,
            created_at=claim.created_at,
            updated_at=claim.updated_at,
            evidence=[
                GuidanceCitationOut(
                    passage_id=item.passage.id,
                    source_id=item.source.id,
                    source_title=item.source.title,
                    document_id=item.source.document_id,
                    document_version=item.source.document_version,
                    page_number=item.passage.page_number,
                    section_heading=item.passage.section_heading,
                    passage_text=item.passage.passage_text,
                    text_sha256=item.passage.text_sha256,
                )
                for item in detail.evidence
            ],
            source_settings=[
                GuidanceSourceSettingCitationOut(
                    setting_id=item.setting.id,
                    manual_id=item.manual.id,
                    source_title=item.manual.document_title or item.manual.original_filename,
                    source_url=item.manual.source_url,
                    document_version=item.manual.document_version,
                    page_number=item.setting.edited_page_number or item.setting.page_number,
                    passage_text=effective_description(item.setting),
                )
                for item in detail.source_settings
            ],
            applicability_targets=[
                GuidanceApplicabilityTargetOut.model_validate(item, from_attributes=True)
                for item in detail.applicability_targets
            ],
            bios_scopes=[
                GuidanceBiosScopeOut.model_validate(item, from_attributes=True)
                for item in detail.bios_scopes
            ],
            events=[GuidanceClaimEventOut.model_validate(item) for item in detail.events],
        )


class RegisterGuidanceConflictIn(BaseModel):
    left_claim_id: int
    right_claim_id: int
    detection_reason: str = ""


class ReviewGuidanceConflictIn(BaseModel):
    decision: str = ""
    note: str = ""


class GuidanceConflictOverviewOut(BaseModel):
    id: int
    left_claim_id: int
    left_claim_text: str
    right_claim_id: int
    right_claim_text: str
    canonical_ids: list[str]
    detection_reason: str
    hardware_relationship: str
    bios_relationship: str
    left_evidence_strength: str
    right_evidence_strength: str
    status: str
    reviewer_note: str | None
    reviewer: str | None
    reviewed_at: str | None
    created_at: str
    updated_at: str

    @classmethod
    def from_overview(cls, value: ConflictOverview) -> "GuidanceConflictOverviewOut":
        row = value.conflict
        return cls(
            id=row.id,
            left_claim_id=row.left_claim_id,
            left_claim_text=value.left_claim.claim_text,
            right_claim_id=row.right_claim_id,
            right_claim_text=value.right_claim.claim_text,
            canonical_ids=list(value.canonical_ids),
            detection_reason=row.detection_reason,
            hardware_relationship=row.hardware_relationship,
            bios_relationship=row.bios_relationship,
            left_evidence_strength=row.left_evidence_strength,
            right_evidence_strength=row.right_evidence_strength,
            status=row.status,
            reviewer_note=row.reviewer_note,
            reviewer=row.reviewer,
            reviewed_at=row.reviewed_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class GuidanceConflictEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    action: str
    actor: str
    detail: str
    created_at: str


class GuidanceConflictDetailOut(GuidanceConflictOverviewOut):
    left_hardware_targets: list[GuidanceApplicabilityTargetOut]
    right_hardware_targets: list[GuidanceApplicabilityTargetOut]
    left_bios_scopes: list[GuidanceBiosScopeOut]
    right_bios_scopes: list[GuidanceBiosScopeOut]
    events: list[GuidanceConflictEventOut]

    @classmethod
    def from_detail(cls, value: ConflictDetail) -> "GuidanceConflictDetailOut":
        overview = GuidanceConflictOverviewOut.from_overview(
            ConflictOverview(
                value.conflict,
                value.left_claim,
                value.right_claim,
                value.canonical_ids,
            )
        ).model_dump()
        return cls(
            **overview,
            left_hardware_targets=[
                GuidanceApplicabilityTargetOut.model_validate(item, from_attributes=True)
                for item in value.left_hardware_targets
            ],
            right_hardware_targets=[
                GuidanceApplicabilityTargetOut.model_validate(item, from_attributes=True)
                for item in value.right_hardware_targets
            ],
            left_bios_scopes=[
                GuidanceBiosScopeOut.model_validate(item, from_attributes=True)
                for item in value.left_bios_scopes
            ],
            right_bios_scopes=[
                GuidanceBiosScopeOut.model_validate(item, from_attributes=True)
                for item in value.right_bios_scopes
            ],
            events=[GuidanceConflictEventOut.model_validate(item) for item in value.events],
        )


class GuidanceConflictScanOut(BaseModel):
    compared_pairs: int
    detected_pairs: int
    existing_pairs: int

    @classmethod
    def from_report(cls, value: ConflictScanReport) -> "GuidanceConflictScanOut":
        return cls.model_validate(value, from_attributes=True)


class CitationFirstCitationOut(BaseModel):
    passage_id: int | None
    source_id: int
    publisher_name: str
    source_title: str
    source_url: str
    document_id: str | None
    document_version: str
    page_number: int
    section_heading: str | None
    passage_text: str
    text_sha256: str
    source_kind: str = "evidence_passage"
    setting_id: int | None = None
    manual_id: int | None = None


class CitationFirstFactOut(BaseModel):
    claim_id: int
    claim_text: str
    claim_type: str
    applicability_scope: str
    applicability_targets: list[GuidanceApplicabilityTargetOut]
    bios_scopes: list[GuidanceBiosScopeOut]
    bios_match_status: str
    hardware_match_status: str
    citations: list[CitationFirstCitationOut]


class KnowledgeConflictOut(BaseModel):
    id: int
    left_claim_id: int
    right_claim_id: int
    status: str
    detection_reason: str
    hardware_relationship: str
    bios_relationship: str
    left_evidence_strength: str
    right_evidence_strength: str
    reviewer_note: str


class CitationFirstClassificationOut(BaseModel):
    category_id: str
    category_name: str
    risk_level: str
    risk_rationale: str


class SystemContextOut(BaseModel):
    platform_id: str
    platform_name: str
    vendor_id: str
    vendor_name: str
    cpu_vendor_id: str
    cpu_vendor_name: str
    motherboard_model_id: str
    motherboard_model_name: str
    motherboard_family_id: str | None
    motherboard_family_name: str | None
    chipset_id: str
    chipset_name: str
    chipset_family_id: str
    chipset_family_name: str
    cpu_model_id: str
    cpu_model_name: str
    cpu_family_id: str
    cpu_family_name: str
    cpu_architecture_id: str
    cpu_architecture_name: str
    bios_version: str | None
    bios_version_status: str
    use_case: str | None
    use_case_label: str | None
    use_case_status: str
    coverage_status: str
    warnings: list[str]

    @classmethod
    def from_context(cls, context: SystemContext) -> "SystemContextOut":
        return cls.model_validate(context, from_attributes=True)


class ContextApplicabilityOut(BaseModel):
    status: str
    label: str
    reason: str

    @classmethod
    def from_match(cls, match: ContextApplicabilityMatch) -> "ContextApplicabilityOut":
        return cls.model_validate(match, from_attributes=True)


class Am5ManualCoverageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    manual_id: int
    vendor_id: str
    vendor_name: str
    document_title: str
    model_name: str
    applicability_scope: str
    applicability_target_ids: list[str]
    acceptance_certified: bool
    candidate_count: int
    approved_setting_count: int
    rejected_setting_count: int
    draft_setting_count: int
    mapped_setting_count: int
    canonical_setting_count: int


class Am5VendorCoverageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    vendor_id: str
    vendor_name: str
    selectable_model_count: int
    manual_count: int
    certified_manual_count: int
    approved_setting_count: int
    mapped_setting_count: int
    canonical_setting_count: int


class Am5ModelCoverageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    model_id: str
    vendor_id: str
    vendor_name: str
    model_name: str
    chipset_id: str
    chipset_name: str
    compatible_manual_count: int
    exact_manual_count: int
    compatible_approved_setting_count: int
    exact_approved_setting_count: int
    evidence_level: str
    evidence_note: str


class Am5CanonicalCoverageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    canonical_id: str
    canonical_name: str
    mapped_vendor_ids: list[str]
    mapped_vendor_names: list[str]
    mapped_setting_count: int
    approved_claim_count: int
    guidance_status: str


class Am5ExpansionGateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    gate_id: str
    label: str
    passed: bool
    required_for_expansion: bool
    detail: str


class Am5CoverageSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    major_vendor_count: int
    vendors_with_manuals: int
    vendors_with_selectable_models: int
    selectable_model_count: int
    manual_count: int
    certified_manual_count: int
    approved_setting_count: int
    draft_setting_count: int
    mapped_setting_count: int
    observed_canonical_setting_count: int
    total_canonical_catalog_count: int
    observed_canonical_with_guidance_count: int
    models_with_compatible_evidence: int
    models_with_exact_evidence: int
    expansion_status: str


class Am5CoverageReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    report_version: str
    platform_id: str
    platform_name: str
    scope_notice: str
    summary: Am5CoverageSummaryOut
    gates: list[Am5ExpansionGateOut]
    vendors: list[Am5VendorCoverageOut]
    models: list[Am5ModelCoverageOut]
    manuals: list[Am5ManualCoverageOut]
    canonical_settings: list[Am5CanonicalCoverageOut]
    next_actions: list[str]

    @classmethod
    def from_report(cls, report: Am5CoverageReport) -> "Am5CoverageReportOut":
        return cls.model_validate(report, from_attributes=True)


class Am5QuestionResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    question_id: str
    question: str
    category: str
    capability: str
    canonical_id: str | None
    canonical_name: str | None
    required_claim_types: list[str]
    available_claim_types: list[str]
    approved_claim_count: int
    expected_outcome: str
    actual_outcome: str
    passed: bool
    reason: str


class Am5QuestionAcceptanceSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    total_question_count: int
    expected_supported_count: int
    expected_refusal_count: int
    actual_supported_count: int
    actual_refusal_count: int
    passing_question_count: int
    unexpected_gap_count: int
    acceptance_status: str


class Am5QuestionAcceptanceReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    report_version: str
    platform_id: str
    platform_name: str
    scope_notice: str
    summary: Am5QuestionAcceptanceSummaryOut
    questions: list[Am5QuestionResultOut]
    next_actions: list[str]

    @classmethod
    def from_report(
        cls,
        report: Am5QuestionAcceptanceReport,
    ) -> "Am5QuestionAcceptanceReportOut":
        return cls.model_validate(report, from_attributes=True)


class EquivalentNameCitationOut(BaseModel):
    setting_id: int
    manual_id: int
    manual_title: str
    manual_label: str
    source_url: str | None
    page_number: int
    menu_path: str | None
    menu_path_page_number: int | None


class VendorEquivalentNameOut(BaseModel):
    vendor_id: str | None
    vendor_name: str
    setting_name: str
    citations: list[EquivalentNameCitationOut]

    @classmethod
    def from_view(cls, view: VendorEquivalentName) -> "VendorEquivalentNameOut":
        return cls(
            vendor_id=view.vendor_id,
            vendor_name=view.vendor_name,
            setting_name=view.setting_name,
            citations=[
                EquivalentNameCitationOut.model_validate(citation, from_attributes=True)
                for citation in view.citations
            ],
        )


class CitationFirstExplanationOut(BaseModel):
    canonical_id: str
    canonical_name: str
    canonical_description: str | None
    resolved_by: str
    matched_input: str
    matched_alias: str | None
    classification: CitationFirstClassificationOut | None
    system_context: SystemContextOut | None
    context_notice: str | None
    equivalent_vendor_names: list[VendorEquivalentNameOut]
    facts: list[CitationFirstFactOut]
    knowledge_conflicts: list[KnowledgeConflictOut]
    answer_state: str
    excluded_fact_count: int
    answer_mode: str
    scope_notice: str
    recommendation_status: str
    recommendation_notice: str
    referral_pointers: list[ReferralPointerOut] = Field(default_factory=list)

    @classmethod
    def from_explanation(
        cls,
        explanation: GuidanceExplanation,
    ) -> "CitationFirstExplanationOut":
        classification = explanation.classification
        return cls(
            canonical_id=explanation.canonical_id,
            canonical_name=explanation.canonical_name,
            canonical_description=explanation.canonical_description,
            resolved_by=explanation.resolved_by,
            matched_input=explanation.matched_input,
            matched_alias=explanation.matched_alias,
            classification=(
                CitationFirstClassificationOut(
                    category_id=classification.category_id,
                    category_name=classification.category_name,
                    risk_level=classification.risk_level,
                    risk_rationale=classification.risk_rationale,
                )
                if classification
                else None
            ),
            system_context=(
                SystemContextOut.from_context(explanation.system_context)
                if explanation.system_context
                else None
            ),
            context_notice=explanation.context_notice,
            equivalent_vendor_names=[
                VendorEquivalentNameOut.from_view(item)
                for item in explanation.equivalent_vendor_names
            ],
            facts=[
                CitationFirstFactOut(
                    claim_id=fact.claim_id,
                    claim_text=fact.claim_text,
                    claim_type=fact.claim_type,
                    applicability_scope=fact.applicability_scope,
                    applicability_targets=[
                        GuidanceApplicabilityTargetOut.model_validate(target, from_attributes=True)
                        for target in fact.applicability_targets
                    ],
                    bios_scopes=[
                        GuidanceBiosScopeOut.model_validate(scope, from_attributes=True)
                        for scope in fact.bios_scopes
                    ],
                    bios_match_status=fact.bios_match_status,
                    hardware_match_status=fact.hardware_match_status,
                    citations=[
                        CitationFirstCitationOut.model_validate(citation, from_attributes=True)
                        for citation in fact.citations
                    ],
                )
                for fact in explanation.facts
            ],
            knowledge_conflicts=[
                KnowledgeConflictOut(
                    id=item.conflict.id,
                    left_claim_id=item.conflict.left_claim_id,
                    right_claim_id=item.conflict.right_claim_id,
                    status=item.conflict.status,
                    detection_reason=item.conflict.detection_reason,
                    hardware_relationship=item.conflict.hardware_relationship,
                    bios_relationship=item.conflict.bios_relationship,
                    left_evidence_strength=item.conflict.left_evidence_strength,
                    right_evidence_strength=item.conflict.right_evidence_strength,
                    reviewer_note=item.conflict.reviewer_note or "",
                )
                for item in explanation.knowledge_conflicts
            ],
            answer_state=explanation.answer_state,
            excluded_fact_count=explanation.excluded_fact_count,
            answer_mode=explanation.answer_mode,
            scope_notice=explanation.scope_notice,
            recommendation_status=explanation.recommendation_status,
            recommendation_notice=explanation.recommendation_notice,
        )


class GuidanceCoverageOut(BaseModel):
    canonical_id: str
    canonical_name: str
    approved_claim_count: int

    @classmethod
    def from_row(cls, row: GuidanceCoverageRow) -> "GuidanceCoverageOut":
        return cls.model_validate(row, from_attributes=True)


class InterfaceCitationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

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
    source_kind: str
    setting_id: int | None
    manual_id: int | None


class InterfaceHardwareScopeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    target_type: str
    target_name: str
    evidence_level: str
    evidence_basis: str


class InterfaceBiosScopeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    context_name: str
    version_label: str
    comparison_mode: str
    version_notes: str
    evidence_basis: str


class InterfaceStatementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    claim_id: int
    claim_type: str
    applicability_scope: str
    text: str
    citations: list[InterfaceCitationOut]
    section_label: str
    hardware_match_status: str
    bios_match_status: str
    hardware_scopes: list[InterfaceHardwareScopeOut]
    bios_scopes: list[InterfaceBiosScopeOut]


class InterfaceConflictOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    conflict_id: int
    left_claim_id: int
    right_claim_id: int
    status: str
    hardware_relationship: str
    bios_relationship: str
    reviewer_note: str


class InterfaceReferralOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    pointer_id: int
    publisher_id: str
    publisher_name: str
    title: str
    source_url: str
    document_id: str | None
    document_version: str


class InterfaceSuggestionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    canonical_id: str
    canonical_name: str
    matched_name: str
    match_kind: str
    score: float


class InterfacePresentationOut(BaseModel):
    contract_version: str
    outcome: str
    resolution_status: str
    answer_state: str
    query: str
    canonical_id: str | None
    canonical_name: str | None
    canonical_description: str | None
    resolved_by: str | None
    matched_alias: str | None
    classification: CitationFirstClassificationOut | None
    system_context: SystemContextOut | None
    context_notice: str | None
    equivalent_vendor_names: list[VendorEquivalentNameOut]
    statements: list[InterfaceStatementOut]
    knowledge_conflicts: list[InterfaceConflictOut]
    scope_notice: str
    recommendation_status: str
    recommendation_notice: str
    refusal_message: str | None
    referral_pointers: list[InterfaceReferralOut]
    suggestions: list[InterfaceSuggestionOut]

    @classmethod
    def from_envelope(cls, envelope: PresentationEnvelope) -> "InterfacePresentationOut":
        return cls(
            contract_version=envelope.contract_version,
            outcome=envelope.outcome,
            resolution_status=envelope.resolution_status,
            answer_state=envelope.answer_state,
            query=envelope.query,
            canonical_id=envelope.canonical_id,
            canonical_name=envelope.canonical_name,
            canonical_description=envelope.canonical_description,
            resolved_by=envelope.resolved_by,
            matched_alias=envelope.matched_alias,
            classification=(
                CitationFirstClassificationOut.model_validate(
                    envelope.classification, from_attributes=True
                )
                if envelope.classification
                else None
            ),
            system_context=(
                SystemContextOut.from_context(envelope.system_context)
                if envelope.system_context
                else None
            ),
            context_notice=envelope.context_notice,
            equivalent_vendor_names=[
                VendorEquivalentNameOut.from_view(item) for item in envelope.equivalent_vendor_names
            ],
            statements=[
                InterfaceStatementOut.model_validate(item, from_attributes=True)
                for item in envelope.statements
            ],
            knowledge_conflicts=[
                InterfaceConflictOut.model_validate(item, from_attributes=True)
                for item in envelope.knowledge_conflicts
            ],
            scope_notice=envelope.scope_notice,
            recommendation_status=envelope.recommendation_status,
            recommendation_notice=envelope.recommendation_notice,
            refusal_message=envelope.refusal_message,
            referral_pointers=[
                InterfaceReferralOut.model_validate(item, from_attributes=True)
                for item in envelope.referral_pointers
            ],
            suggestions=[
                InterfaceSuggestionOut.model_validate(item, from_attributes=True)
                for item in envelope.suggestions
            ],
        )


class TrustedWebSourceOut(BaseModel):
    publisher_id: str
    publisher_name: str
    title: str
    source_url: str
    evidence_kind: str
    excerpt_text: str | None
    content_sha256: str | None
    retrieved_at: str

    @classmethod
    def from_view(cls, view: TrustedWebSourceView) -> "TrustedWebSourceOut":
        return cls.model_validate(view, from_attributes=True)


class TrustedWebResearchOut(BaseModel):
    run_id: int
    query: str
    status: str
    notice: str
    sources: list[TrustedWebSourceOut]

    @classmethod
    def from_view(cls, view: TrustedWebResearchView) -> "TrustedWebResearchOut":
        return cls(
            run_id=view.run_id,
            query=view.query,
            status=view.status,
            notice=view.notice,
            sources=[TrustedWebSourceOut.from_view(source) for source in view.sources],
        )


class GuidedQuestionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    question_id: str
    question: str
    category: str
    capability: str
    canonical_id: str | None
    canonical_name: str | None
    required_claim_types: list[str]
    outcome: str
    reason: str

    @classmethod
    def from_question(cls, question: GuidedQuestion) -> "GuidedQuestionOut":
        return cls.model_validate(question, from_attributes=True)


class GuidedAnswerOut(BaseModel):
    question: GuidedQuestionOut
    presentation: InterfacePresentationOut

    @classmethod
    def from_answer(cls, answer: GuidedAnswer) -> "GuidedAnswerOut":
        return cls(
            question=GuidedQuestionOut.from_question(answer.question),
            presentation=InterfacePresentationOut.from_envelope(answer.presentation),
        )


class DuplicateUploadDetail(BaseModel):
    message: str
    existing_manual_id: int


class DuplicateUploadErrorOut(BaseModel):
    """The 409 duplicate-upload body: detail carries the existing manual."""

    detail: DuplicateUploadDetail


class HealthOut(BaseModel):
    status: str
    app_version: str
    parser_version: str


class ReadinessOut(BaseModel):
    status: str
    app_version: str
    database_revision: str | None
    authentication_initialized: bool
    checks: dict[str, bool]


class ReleaseCheckOut(BaseModel):
    check_id: str
    group: str
    label: str
    passed: bool
    required: bool
    detail: str
    href: str | None


class ReleaseReadinessOut(BaseModel):
    report_version: str
    app_version: str
    product_status: str
    deployment_status: str
    product_passed_count: int
    product_required_count: int
    deployment_passed_count: int
    deployment_required_count: int
    checks: list[ReleaseCheckOut]


class InterfaceStatementPolicyOut(BaseModel):
    text: str
    claim_cardinality: str
    citation_policy: str
    ordering: str


class QuestionRouteOut(BaseModel):
    version: str
    kind: str
    original_query: str
    routed_value: str
    rule: str
    question_id: str | None
    transformed: bool


class InterfaceContractOut(BaseModel):
    version: str
    outcomes: list[str]
    statement_policy: InterfaceStatementPolicyOut
    required_notices: list[str]
    prohibited_capabilities: list[str]
    empty_retrieval_outcome: str
    referral_pointer_role: str
    validation_failure_action: str
    natural_language_routing_status: str
    model_provider_status: str
    provider_abstraction_status: str
    connected_provider_ids: list[str]
    deterministic_fallback_status: str


class BatchSummaryOut(BaseModel):
    id: int
    status: str
    total_count: int
    accepted_count: int
    failed_count: int
    created_at: str
    finished_at: str | None


class BatchItemOut(BaseModel):
    id: int
    original_filename: str
    intake_status: str
    workflow_status: str
    manual_id: int | None
    document_type: str | None
    eligibility_status: str | None
    run_status: str | None
    draft_count: int
    error_message: str | None


class BatchDetailOut(BaseModel):
    batch: BatchSummaryOut
    items: list[BatchItemOut]

    @classmethod
    def from_overview(cls, overview: BatchOverview) -> "BatchDetailOut":
        return cls(
            batch=BatchSummaryOut.model_validate(overview.batch, from_attributes=True),
            items=[
                BatchItemOut(
                    id=row.item.id,
                    original_filename=row.item.original_filename,
                    intake_status=row.item.intake_status,
                    workflow_status=row.workflow_status,
                    manual_id=row.item.manual_id,
                    document_type=row.manual.document_type if row.manual else None,
                    eligibility_status=row.manual.eligibility_status if row.manual else None,
                    run_status=row.run_status,
                    draft_count=row.draft_count,
                    error_message=row.item.error_message,
                )
                for row in overview.items
            ],
        )


class EditSettingIn(BaseModel):
    """Draft edit: values default empty so the domain rules (non-empty name,
    draft-only) stay the single validator, exactly as the HTML form."""

    name: str = ""
    description: str = ""


class ApproveIn(BaseModel):
    note: str = ""


class RejectIn(BaseModel):
    reason: str = ""


class CreateCanonicalSettingIn(BaseModel):
    name: str = ""
    description: str = ""
    aliases: list[str] = []
    canonical_id: str = ""


class MapCanonicalSettingIn(BaseModel):
    canonical_id: str = ""
    note: str = ""


class UnmapCanonicalSettingIn(BaseModel):
    reason: str = ""


class ClassifyCanonicalSettingIn(BaseModel):
    category_id: str = ""
    risk_level: str = ""
    risk_rationale: str = ""


class CanonicalSuggestionReviewIn(BaseModel):
    note: str = ""
    reason: str = ""


class ManualProvenanceIn(BaseModel):
    vendor: str = ""
    model_name: str = ""
    document_title: str = ""
    source_url: str = ""
    document_version: str = ""
    published_on: str = ""
    language: str = "en"
    board_revision: str = ""
    chipset: str = ""


class ManualEligibilityIn(BaseModel):
    document_type: str = "unknown"
    decision: str = ""
    note: str = ""


class AddMissedSettingIn(BaseModel):
    name: str = ""
    page_number: int = 0
    description: str = ""
    note: str = ""


class DismissMissedSettingIn(BaseModel):
    reason: str = ""


class CertifyAcceptanceIn(BaseModel):
    notes: str = ""


class PageReferenceReviewIn(BaseModel):
    page_number: int = 0


class AcceptanceReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    manual_id: int
    run_status: str | None
    parser_version: str | None
    total_candidates: int
    draft_count: int
    approved_count: int
    rejected_count: int
    reviewed_count: int
    corrected_count: int
    empty_description_count: int
    page_reviewed_count: int
    page_pending_count: int
    page_correct_count: int
    page_corrected_count: int
    page_accuracy: float | None
    missed_count: int
    review_completion: float
    precision: float | None
    estimated_recall: float | None
    provenance_complete: bool
    ground_truth_reviewed: bool
    acceptance_complete: bool


class MissedSettingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    manual_id: int
    name: str
    page_number: int
    description: str | None
    note: str | None
    status: str
    actor: str
    created_at: str
    dismissed_at: str | None
    dismissal_reason: str | None


class PageReferenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    setting_id: int
    name: str
    extracted_page_number: int
    effective_page_number: int
    review_status: str


class AcceptanceDetailOut(BaseModel):
    report: AcceptanceReportOut
    missed_settings: list[MissedSettingOut]
    page_references: list[PageReferenceOut]

    @classmethod
    def from_domain(
        cls,
        report: AcceptanceReport,
        missed_settings: list[MissedSetting],
        page_references: list[PageReferenceRow],
    ) -> "AcceptanceDetailOut":
        return cls(
            report=AcceptanceReportOut.model_validate(report),
            missed_settings=[MissedSettingOut.model_validate(item) for item in missed_settings],
            page_references=[PageReferenceOut.model_validate(item) for item in page_references],
        )


class ExtractionRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    error_message: str | None
    pages_processed: int
    settings_found: int
    parser_version: str | None
    started_at: str | None
    finished_at: str | None
    created_at: str


class ApplicabilityTargetOut(BaseModel):
    target_type: str
    target_id: str
    canonical_name: str


class ManualApplicabilityOut(BaseModel):
    scope: str
    normalization_version: str
    model_status: str
    detected_model_id: str | None
    target_ids: list[str]
    targets: list[ApplicabilityTargetOut]
    warnings: list[str]

    @classmethod
    def from_view(cls, view: ApplicabilityView) -> "ManualApplicabilityOut":
        return cls(
            scope=view.scope,
            normalization_version=view.normalization_version,
            model_status=view.model_status,
            detected_model_id=view.detected_model_id,
            target_ids=list(view.target_ids),
            targets=[
                ApplicabilityTargetOut(
                    target_type=target.target_type,
                    target_id=target.target_id,
                    canonical_name=target.canonical_name,
                )
                for target in view.targets
            ],
            warnings=list(view.warnings),
        )


class ManualSummaryOut(BaseModel):
    id: int
    original_filename: str
    vendor: str | None
    vendor_id: str | None
    model_name: str | None
    document_title: str
    source_url: str | None
    provenance_complete: bool
    document_type: str | None
    eligibility_status: str
    eligibility_confidence: int | None
    page_count: int
    file_size_bytes: int
    created_at: str
    run_status: str | None
    draft_count: int
    approved_count: int
    rejected_count: int
    applicability: ManualApplicabilityOut | None

    @classmethod
    def from_overview(cls, overview: ManualOverview) -> "ManualSummaryOut":
        manual = overview.manual
        return cls(
            id=manual.id,
            original_filename=manual.original_filename,
            vendor=manual.vendor,
            vendor_id=manual.vendor_id,
            model_name=manual.model_name,
            document_title=manual.document_title,
            source_url=manual.source_url,
            provenance_complete=not missing_provenance_fields(manual),
            document_type=manual.document_type,
            eligibility_status=manual.eligibility_status,
            eligibility_confidence=manual.eligibility_confidence,
            page_count=manual.page_count,
            file_size_bytes=manual.file_size_bytes,
            created_at=manual.created_at,
            run_status=overview.run_status,
            draft_count=overview.draft_count,
            approved_count=overview.approved_count,
            rejected_count=overview.rejected_count,
            applicability=(
                ManualApplicabilityOut.from_view(overview.applicability)
                if overview.applicability
                else None
            ),
        )


class ManualEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    action: str
    actor: str
    detail: str | None
    created_at: str


class ManualDetailOut(BaseModel):
    id: int
    original_filename: str
    sha256: str
    vendor: str | None
    vendor_id: str | None
    model_name: str | None
    document_title: str
    source_url: str | None
    document_version: str | None
    published_on: str | None
    retrieved_at: str
    language: str
    board_revision: str | None
    chipset: str | None
    provenance_complete: bool
    missing_provenance_fields: list[str]
    document_type: str | None
    eligibility_status: str
    eligibility_confidence: int | None
    eligibility_reasons: list[str]
    eligibility_reviewed_at: str | None
    eligibility_reviewer: str | None
    eligibility_note: str | None
    page_count: int
    file_size_bytes: int
    created_at: str
    extraction_run: ExtractionRunOut | None
    events: list[ManualEventOut]
    applicability: ManualApplicabilityOut | None

    @classmethod
    def from_manual(
        cls,
        manual: Manual,
        run: ExtractionRun | None,
        events: list[ManualEvent],
        applicability: ApplicabilityView | None = None,
    ) -> "ManualDetailOut":
        missing = missing_provenance_fields(manual)
        from app.domain.eligibility import eligibility_snapshot

        eligibility = eligibility_snapshot(manual)
        return cls(
            id=manual.id,
            original_filename=manual.original_filename,
            sha256=manual.sha256,
            vendor=manual.vendor,
            vendor_id=manual.vendor_id,
            model_name=manual.model_name,
            document_title=manual.document_title,
            source_url=manual.source_url,
            document_version=manual.document_version,
            published_on=manual.published_on,
            retrieved_at=manual.retrieved_at,
            language=manual.language,
            board_revision=manual.board_revision,
            chipset=manual.chipset,
            provenance_complete=not missing,
            missing_provenance_fields=missing,
            document_type=manual.document_type,
            eligibility_status=manual.eligibility_status,
            eligibility_confidence=manual.eligibility_confidence,
            eligibility_reasons=list(eligibility["eligibility_reasons"]),
            eligibility_reviewed_at=manual.eligibility_reviewed_at,
            eligibility_reviewer=manual.eligibility_reviewer,
            eligibility_note=manual.eligibility_note,
            page_count=manual.page_count,
            file_size_bytes=manual.file_size_bytes,
            created_at=manual.created_at,
            extraction_run=ExtractionRunOut.model_validate(run) if run else None,
            events=[ManualEventOut.model_validate(event) for event in events],
            applicability=(
                ManualApplicabilityOut.from_view(applicability) if applicability else None
            ),
        )


class ReviewEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    action: str
    actor: str
    detail: str | None
    created_at: str


class DraftQueueItemOut(BaseModel):
    setting_id: int
    manual_id: int
    manual_label: str
    effective_name: str
    effective_description: str
    page_number: int

    @classmethod
    def from_row(cls, row: DraftQueueRow) -> "DraftQueueItemOut":
        manual = row.manual
        return cls(
            setting_id=row.setting.id,
            manual_id=manual.id,
            manual_label=(f"{manual.vendor or ''} {manual.model_name or ''}").strip()
            or manual.original_filename,
            effective_name=row.effective_name,
            effective_description=row.effective_description,
            page_number=row.setting.page_number,
        )


class CanonicalMappingOut(BaseModel):
    canonical_id: str
    canonical_name: str
    description: str | None
    mapping_method: str
    note: str | None
    actor: str
    created_at: str
    updated_at: str

    @classmethod
    def from_view(cls, view: CanonicalMappingView) -> "CanonicalMappingOut":
        return cls.model_validate(view, from_attributes=True)


class CanonicalSuggestionOut(BaseModel):
    canonical_id: str
    canonical_name: str
    matched_alias: str


class CanonicalMappingEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    setting_id: int
    action: str
    actor: str
    detail: str | None
    created_at: str


class CanonicalSettingSummaryOut(BaseModel):
    canonical_id: str
    canonical_name: str
    description: str | None
    mapping_count: int
    vendor_count: int
    category_id: str | None
    category_name: str | None
    risk_level: str | None

    @classmethod
    def from_row(cls, row: CanonicalCatalogRow) -> "CanonicalSettingSummaryOut":
        return cls.model_validate(row, from_attributes=True)


class CanonicalAliasOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    normalized_alias: str
    display_alias: str
    vendor_id: str | None


class CanonicalMappedSettingOut(BaseModel):
    setting_id: int
    effective_name: str
    manual_id: int
    manual_label: str
    vendor_id: str | None
    page_number: int


class CanonicalCategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    category_id: str
    canonical_name: str
    description: str
    ordinal: int


class CanonicalClassificationOut(BaseModel):
    category_id: str
    category_name: str
    risk_level: str
    risk_rationale: str
    actor: str
    updated_at: str

    @classmethod
    def from_view(
        cls,
        view: CanonicalClassificationView,
    ) -> "CanonicalClassificationOut":
        return cls.model_validate(view, from_attributes=True)


class CanonicalClassificationEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    canonical_id: str
    action: str
    actor: str
    detail: str
    created_at: str


class CanonicalSettingDetailOut(BaseModel):
    canonical_id: str
    canonical_name: str
    description: str | None
    aliases: list[CanonicalAliasOut]
    mappings: list[CanonicalMappedSettingOut]
    classification: CanonicalClassificationOut | None
    classification_events: list[CanonicalClassificationEventOut]

    @classmethod
    def from_detail(cls, detail: CanonicalDetail) -> "CanonicalSettingDetailOut":
        return cls(
            canonical_id=detail.identity.canonical_id,
            canonical_name=detail.identity.canonical_name,
            description=detail.identity.description,
            aliases=[CanonicalAliasOut.model_validate(row) for row in detail.aliases],
            mappings=[
                CanonicalMappedSettingOut.model_validate(row, from_attributes=True)
                for row in detail.mappings
            ],
            classification=(
                CanonicalClassificationOut.from_view(detail.classification)
                if detail.classification
                else None
            ),
            classification_events=[
                CanonicalClassificationEventOut.model_validate(event)
                for event in detail.classification_events
            ],
        )


class CanonicalSuggestionQueueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    suggestion_id: int
    setting_id: int
    effective_name: str
    manual_id: int
    manual_label: str
    vendor_id: str | None
    page_number: int
    canonical_id: str
    canonical_name: str
    matched_alias: str
    match_type: str
    confidence_basis_points: int
    evidence: dict[str, object]
    engine_version: str
    category_id: str | None
    category_name: str | None
    risk_level: str | None
    created_at: str

    @classmethod
    def from_row(cls, row: SuggestionQueueRow) -> "CanonicalSuggestionQueueOut":
        return cls.model_validate(row, from_attributes=True)


class CanonicalSuggestionDecisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    setting_id: int
    canonical_id: str
    match_type: str
    matched_alias: str
    status: str
    confidence_basis_points: int
    evidence_json: str
    engine_version: str
    review_note: str | None
    actor: str | None
    created_at: str
    reviewed_at: str | None


class CanonicalSuggestionRefreshOut(BaseModel):
    created: int
    counts: dict[str, int]


class SettingDetailOut(BaseModel):
    id: int
    manual_id: int
    extraction_run_id: int
    page_number: int
    edited_page_number: int | None
    effective_page_number: int
    page_review_status: str
    status: str
    extracted_name: str
    extracted_description: str
    edited_name: str | None
    edited_description: str | None
    effective_name: str
    effective_description: str
    review_note: str | None
    created_at: str
    updated_at: str
    events: list[ReviewEventOut]
    canonical_mapping: CanonicalMappingOut | None
    canonical_suggestions: list[CanonicalSuggestionOut]
    canonical_events: list[CanonicalMappingEventOut]

    @classmethod
    def from_setting(
        cls,
        setting: Setting,
        events: list[ReviewEvent],
        canonical_mapping: CanonicalMappingView | None,
        canonical_suggestions: list[CanonicalSuggestion],
        canonical_events: list[CanonicalMappingEvent],
    ) -> "SettingDetailOut":
        return cls(
            id=setting.id,
            manual_id=setting.manual_id,
            extraction_run_id=setting.extraction_run_id,
            page_number=setting.page_number,
            edited_page_number=setting.edited_page_number,
            effective_page_number=setting.edited_page_number or setting.page_number,
            page_review_status=setting.page_review_status,
            status=setting.status,
            extracted_name=setting.extracted_name,
            extracted_description=setting.extracted_description,
            edited_name=setting.edited_name,
            edited_description=setting.edited_description,
            effective_name=effective_name(setting),
            effective_description=effective_description(setting),
            review_note=setting.review_note,
            created_at=setting.created_at,
            updated_at=setting.updated_at,
            events=[ReviewEventOut.model_validate(event) for event in events],
            canonical_mapping=(
                CanonicalMappingOut.from_view(canonical_mapping)
                if canonical_mapping is not None
                else None
            ),
            canonical_suggestions=[
                CanonicalSuggestionOut.model_validate(item, from_attributes=True)
                for item in canonical_suggestions
            ],
            canonical_events=[
                CanonicalMappingEventOut.model_validate(event) for event in canonical_events
            ],
        )


class SearchResultOut(BaseModel):
    setting_id: int
    effective_name: str
    snippet: str  # HTML-escaped description snippet with <mark> highlights
    manual_label: str
    vendor_id: str | None
    manual_id: int
    applicability_scope: str | None
    applicability_target_ids: list[str]
    context_match: ContextApplicabilityOut | None
    canonical_id: str | None
    canonical_name: str | None
    equivalent_vendor_names: list[VendorEquivalentNameOut]
    category_id: str | None
    category_name: str | None
    risk_level: str | None
    page_number: int

    @classmethod
    def from_result(cls, result: SearchResult) -> "SearchResultOut":
        return cls(
            setting_id=result.setting_id,
            effective_name=result.effective_name,
            snippet=str(result.snippet),
            manual_label=result.manual_label,
            vendor_id=result.vendor_id,
            manual_id=result.manual_id,
            applicability_scope=result.applicability_scope,
            applicability_target_ids=list(result.applicability_target_ids),
            context_match=(
                ContextApplicabilityOut.from_match(result.context_match)
                if result.context_match
                else None
            ),
            canonical_id=result.canonical_id,
            canonical_name=result.canonical_name,
            equivalent_vendor_names=[
                VendorEquivalentNameOut.from_view(item) for item in result.equivalent_vendor_names
            ],
            category_id=result.category_id,
            category_name=result.category_name,
            risk_level=result.risk_level,
            page_number=result.page_number,
        )
