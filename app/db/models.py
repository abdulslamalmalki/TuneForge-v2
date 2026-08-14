"""SQLAlchemy models for the four tables in Implementation Plan section 9.

Timestamps are UTC ISO-8601 stored as TEXT. The `settings_fts` FTS5 virtual
table is created by the initial migration, not mapped here: it is a disposable
search index, not a source of truth.
"""

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class UserAccount(Base):
    """A curator identity. Accounts are disabled, never deleted, for audit continuity."""

    __tablename__ = "user_accounts"
    __table_args__ = (
        CheckConstraint("role IN ('administrator', 'reviewer')", name="ck_user_account_role"),
        CheckConstraint("status IN ('active', 'disabled')", name="ck_user_account_status"),
        Index("ix_user_accounts_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(Text, unique=True)
    display_name: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text)
    password_hash: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text)
    last_login_at: Mapped[str | None] = mapped_column(Text)


class AuthSession(Base):
    """An opaque browser session; only a SHA-256 token digest is persisted."""

    __tablename__ = "auth_sessions"
    __table_args__ = (
        Index("ix_auth_sessions_user_id", "user_id"),
        Index("ix_auth_sessions_expires_at", "expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user_accounts.id", ondelete="CASCADE")
    )
    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    created_at: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[str] = mapped_column(Text)
    last_seen_at: Mapped[str] = mapped_column(Text)
    revoked_at: Mapped[str | None] = mapped_column(Text)


class AuthEvent(Base):
    """Append-only security history for account and session actions."""

    __tablename__ = "auth_events"
    __table_args__ = (
        Index("ix_auth_events_created_at", "created_at"),
        Index("ix_auth_events_subject_user_id", "subject_user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    action: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(Text)
    subject_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("user_accounts.id", ondelete="RESTRICT")
    )
    detail: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)


class Vendor(Base):
    __tablename__ = "vendors"

    vendor_id: Mapped[str] = mapped_column(Text, primary_key=True)
    canonical_name: Mapped[str] = mapped_column(Text, unique=True)


class VendorAlias(Base):
    __tablename__ = "vendor_aliases"
    __table_args__ = (Index("ix_vendor_aliases_vendor_id", "vendor_id"),)

    normalized_alias: Mapped[str] = mapped_column(Text, primary_key=True)
    vendor_id: Mapped[str] = mapped_column(
        Text, ForeignKey("vendors.vendor_id", ondelete="CASCADE")
    )
    display_alias: Mapped[str] = mapped_column(Text)


class HardwarePlatform(Base):
    __tablename__ = "hardware_platforms"

    platform_id: Mapped[str] = mapped_column(Text, primary_key=True)
    canonical_name: Mapped[str] = mapped_column(Text, unique=True)
    cpu_vendor: Mapped[str] = mapped_column(Text)
    socket_name: Mapped[str] = mapped_column(Text)


class CpuArchitecture(Base):
    __tablename__ = "cpu_architectures"

    architecture_id: Mapped[str] = mapped_column(Text, primary_key=True)
    vendor_name: Mapped[str] = mapped_column(Text)
    canonical_name: Mapped[str] = mapped_column(Text, unique=True)


class CpuFamily(Base):
    __tablename__ = "cpu_families"

    family_id: Mapped[str] = mapped_column(Text, primary_key=True)
    architecture_id: Mapped[str] = mapped_column(
        Text, ForeignKey("cpu_architectures.architecture_id", ondelete="RESTRICT"), index=True
    )
    platform_id: Mapped[str] = mapped_column(
        Text, ForeignKey("hardware_platforms.platform_id", ondelete="RESTRICT"), index=True
    )
    canonical_name: Mapped[str] = mapped_column(Text, unique=True)


class CpuModel(Base):
    __tablename__ = "cpu_models"
    __table_args__ = (CheckConstraint("is_x3d IN (0, 1)", name="ck_cpu_model_is_x3d"),)

    cpu_model_id: Mapped[str] = mapped_column(Text, primary_key=True)
    family_id: Mapped[str] = mapped_column(
        Text, ForeignKey("cpu_families.family_id", ondelete="RESTRICT"), index=True
    )
    architecture_id: Mapped[str] = mapped_column(
        Text, ForeignKey("cpu_architectures.architecture_id", ondelete="RESTRICT"), index=True
    )
    platform_id: Mapped[str] = mapped_column(
        Text, ForeignKey("hardware_platforms.platform_id", ondelete="RESTRICT"), index=True
    )
    canonical_name: Mapped[str] = mapped_column(Text, unique=True)
    is_x3d: Mapped[int] = mapped_column(Integer)


class CpuModelAlias(Base):
    __tablename__ = "cpu_model_aliases"

    normalized_alias: Mapped[str] = mapped_column(Text, primary_key=True)
    cpu_model_id: Mapped[str] = mapped_column(
        Text, ForeignKey("cpu_models.cpu_model_id", ondelete="CASCADE"), index=True
    )
    display_alias: Mapped[str] = mapped_column(Text)


class EvidenceSource(Base):
    """A provenance-pinned document admitted to the tuning evidence corpus."""

    __tablename__ = "evidence_sources"
    __table_args__ = (
        CheckConstraint(
            "source_type IN ('official_document')",
            name="ck_evidence_source_type",
        ),
        CheckConstraint(
            "trust_tier IN ('primary_official')",
            name="ck_evidence_source_trust_tier",
        ),
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="ck_evidence_source_status",
        ),
        Index("ix_evidence_sources_status", "status"),
        Index("ix_evidence_sources_publisher_id", "publisher_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    publisher_id: Mapped[str] = mapped_column(Text)
    publisher_name: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(Text)
    trust_tier: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(Text)
    document_id: Mapped[str | None] = mapped_column(Text)
    document_version: Mapped[str] = mapped_column(Text)
    published_on: Mapped[str] = mapped_column(Text)
    retrieved_at: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(Text)
    original_filename: Mapped[str] = mapped_column(Text)
    stored_filename: Mapped[str] = mapped_column(Text, unique=True)
    sha256: Mapped[str] = mapped_column(Text, unique=True)
    file_size_bytes: Mapped[int] = mapped_column(Integer)
    page_count: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text)
    reviewer: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[str | None] = mapped_column(Text)
    review_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)


class EvidenceExtractionRun(Base):
    __tablename__ = "evidence_extraction_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed')",
            name="ck_evidence_extraction_run_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("evidence_sources.id", ondelete="CASCADE"),
        unique=True,
    )
    status: Mapped[str] = mapped_column(Text)
    extractor_version: Mapped[str | None] = mapped_column(Text)
    pages_processed: Mapped[int] = mapped_column(Integer, server_default="0")
    passages_found: Mapped[int] = mapped_column(Integer, server_default="0")
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)


class EvidencePassage(Base):
    """Immutable, source-page-scoped evidence extracted after source approval."""

    __tablename__ = "evidence_passages"
    __table_args__ = (
        Index("ux_evidence_passages_source_ordinal", "source_id", "ordinal", unique=True),
        Index("ix_evidence_passages_source_page", "source_id", "page_number"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("evidence_sources.id", ondelete="CASCADE")
    )
    extraction_run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("evidence_extraction_runs.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    page_number: Mapped[int] = mapped_column(Integer)
    section_heading: Mapped[str | None] = mapped_column(Text)
    passage_text: Mapped[str] = mapped_column(Text)
    text_sha256: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)


class EvidenceSourceEvent(Base):
    """Append-only audit history for evidence-source intake and review."""

    __tablename__ = "evidence_source_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("evidence_sources.id", ondelete="CASCADE"), index=True
    )
    action: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(Text)
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)


class GuidanceClaim(Base):
    """A reviewer-controlled factual claim grounded in approved evidence."""

    __tablename__ = "guidance_claims"
    __table_args__ = (
        CheckConstraint(
            "claim_type IN ('definition', 'behavior', 'constraint', 'safety')",
            name="ck_guidance_claim_type",
        ),
        CheckConstraint(
            "status IN ('draft', 'approved', 'rejected')",
            name="ck_guidance_claim_status",
        ),
        CheckConstraint(
            "(applicability_scope = 'source_scope' AND cpu_model_id IS NULL) OR "
            "(applicability_scope = 'cpu_model' AND cpu_model_id IS NOT NULL)",
            name="ck_guidance_claim_applicability",
        ),
        Index("ix_guidance_claims_status", "status"),
        Index("ix_guidance_claims_claim_type", "claim_type"),
        Index("ix_guidance_claims_cpu_model_id", "cpu_model_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    claim_text: Mapped[str] = mapped_column(Text)
    claim_type: Mapped[str] = mapped_column(Text)
    applicability_scope: Mapped[str] = mapped_column(Text)
    cpu_model_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("cpu_models.cpu_model_id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(Text)
    review_note: Mapped[str | None] = mapped_column(Text)
    reviewer: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text)


class GuidanceClaimEvidence(Base):
    __tablename__ = "guidance_claim_evidence"
    __table_args__ = (
        CheckConstraint("relation IN ('supports')", name="ck_guidance_evidence_relation"),
    )

    claim_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("guidance_claims.id", ondelete="CASCADE"),
        primary_key=True,
    )
    passage_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("evidence_passages.id", ondelete="RESTRICT"),
        primary_key=True,
        index=True,
    )
    relation: Mapped[str] = mapped_column(Text)


class GuidanceClaimSetting(Base):
    __tablename__ = "guidance_claim_settings"

    claim_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("guidance_claims.id", ondelete="CASCADE"),
        primary_key=True,
    )
    canonical_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("canonical_settings.canonical_id", ondelete="RESTRICT"),
        primary_key=True,
        index=True,
    )


class GuidanceClaimSourceSetting(Base):
    """An approved manual setting used as direct page-scoped claim evidence."""

    __tablename__ = "guidance_claim_source_settings"
    __table_args__ = (
        CheckConstraint("relation IN ('supports')", name="ck_guidance_source_setting_relation"),
    )

    claim_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("guidance_claims.id", ondelete="CASCADE"),
        primary_key=True,
    )
    setting_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("settings.id", ondelete="RESTRICT"),
        primary_key=True,
        index=True,
    )
    relation: Mapped[str] = mapped_column(Text)


class GuidanceClaimEvent(Base):
    """Append-only history for claim candidates, edits, and decisions."""

    __tablename__ = "guidance_claim_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    claim_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("guidance_claims.id", ondelete="CASCADE"),
        index=True,
    )
    action: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(Text)
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)


class GuidanceCandidate(Base):
    """Automated or edited candidate; never public until separately claim-approved."""

    __tablename__ = "guidance_candidates"
    __table_args__ = (
        CheckConstraint(
            "claim_type IS NULL OR claim_type IN "
            "('definition', 'behavior', 'constraint', 'safety')",
            name="ck_guidance_candidate_claim_type",
        ),
        CheckConstraint(
            "status IN ('pending', 'needs_research', 'uncertain', 'rejected', "
            "'skipped', 'duplicate', 'promoted')",
            name="ck_guidance_candidate_status",
        ),
        CheckConstraint(
            "source_trust IN ('trusted_official_manual', 'incomplete')",
            name="ck_guidance_candidate_source_trust",
        ),
        CheckConstraint(
            "confidence_basis_points BETWEEN 0 AND 10000",
            name="ck_guidance_candidate_confidence",
        ),
        CheckConstraint("possible_conflict IN (0, 1)", name="ck_guidance_candidate_conflict_flag"),
        CheckConstraint("source_page_number > 0", name="ck_guidance_candidate_page_number"),
        Index(
            "ux_guidance_candidate_source_fact",
            "setting_id",
            "claim_type",
            "evidence_sha256",
            unique=True,
        ),
        Index("ix_guidance_candidates_status", "status"),
        Index("ix_guidance_candidates_priority", "status", "priority_score"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    setting_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("settings.id", ondelete="RESTRICT"), index=True
    )
    canonical_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("canonical_settings.canonical_id", ondelete="RESTRICT"),
        index=True,
    )
    claim_text: Mapped[str | None] = mapped_column(Text)
    claim_type: Mapped[str | None] = mapped_column(Text)
    source_excerpt: Mapped[str] = mapped_column(Text)
    source_page_number: Mapped[int] = mapped_column(Integer)
    hardware_scope_json: Mapped[str] = mapped_column(Text, server_default="{}")
    bios_scope_json: Mapped[str] = mapped_column(Text, server_default="{}")
    source_trust: Mapped[str] = mapped_column(Text)
    confidence_basis_points: Mapped[int] = mapped_column(Integer)
    priority_score: Mapped[int] = mapped_column(Integer, server_default="0")
    status: Mapped[str] = mapped_column(Text)
    possible_conflict: Mapped[int] = mapped_column(Integer, server_default="0")
    conflict_detail: Mapped[str | None] = mapped_column(Text)
    duplicate_of_candidate_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("guidance_candidates.id", ondelete="RESTRICT")
    )
    promoted_claim_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("guidance_claims.id", ondelete="RESTRICT"),
        index=True,
    )
    generator_version: Mapped[str] = mapped_column(Text)
    evidence_sha256: Mapped[str] = mapped_column(Text)
    reviewer: Mapped[str | None] = mapped_column(Text)
    review_note: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text)


class GuidanceCandidateEvent(Base):
    """Append-only history for generation and every candidate decision."""

    __tablename__ = "guidance_candidate_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    candidate_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("guidance_candidates.id", ondelete="RESTRICT"),
        index=True,
    )
    action: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(Text)
    detail: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)


class UnansweredLookup(Base):
    """Immutable, privacy-limited record of one citation-first refusal."""

    __tablename__ = "unanswered_lookups"
    __table_args__ = (
        CheckConstraint(
            "surface IN ('search', 'guidance')",
            name="ck_unanswered_lookup_surface",
        ),
        CheckConstraint(
            "reason IN ('no_approved_search_results', 'no_exact_guidance_match', "
            "'no_accepted_guidance_claims')",
            name="ck_unanswered_lookup_reason",
        ),
        CheckConstraint(
            "length(query_text) BETWEEN 1 AND 300",
            name="ck_unanswered_lookup_query_length",
        ),
        Index("ix_unanswered_lookups_created_at", "created_at"),
        Index("ix_unanswered_lookups_normalized_query", "normalized_query"),
        Index("ix_unanswered_lookups_reason", "reason"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    surface: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    query_text: Mapped[str] = mapped_column(Text)
    normalized_query: Mapped[str] = mapped_column(Text)
    nearest_matches_json: Mapped[str] = mapped_column(Text)
    context_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)


class ReferralPointer(Base):
    """Reviewer-registered official document link; it contains no source content."""

    __tablename__ = "referral_pointers"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'retired')",
            name="ck_referral_pointer_status",
        ),
        Index("ix_referral_pointers_status", "status"),
        Index("ix_referral_pointers_publisher_id", "publisher_id"),
        Index(
            "ux_referral_pointer_identity",
            "publisher_id",
            "title",
            "source_url",
            "document_version",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    publisher_id: Mapped[str] = mapped_column(Text)
    publisher_name: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(Text)
    document_id: Mapped[str | None] = mapped_column(Text)
    document_version: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    reviewer: Mapped[str] = mapped_column(Text)
    review_note: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)
    retired_at: Mapped[str | None] = mapped_column(Text)


class ReferralPointerSetting(Base):
    __tablename__ = "referral_pointer_settings"

    pointer_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("referral_pointers.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    canonical_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("canonical_settings.canonical_id", ondelete="RESTRICT"),
        primary_key=True,
        index=True,
    )


class ReferralPointerEvent(Base):
    """Append-only history for official referral registration and retirement."""

    __tablename__ = "referral_pointer_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pointer_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("referral_pointers.id", ondelete="RESTRICT"),
        index=True,
    )
    action: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(Text)
    detail: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)


class TrustedWebResearchRun(Base):
    """One immutable-query, append-audited live official-web research attempt."""

    __tablename__ = "trusted_web_research_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'succeeded', 'refused', 'failed')",
            name="ck_trusted_web_research_run_status",
        ),
        CheckConstraint(
            "length(query_text) BETWEEN 1 AND 300",
            name="ck_trusted_web_research_query_length",
        ),
        Index("ix_trusted_web_research_runs_created_at", "created_at"),
        Index("ix_trusted_web_research_runs_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    query_text: Mapped[str] = mapped_column(Text)
    normalized_query: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    policy_version: Mapped[str] = mapped_column(Text)
    result_count: Mapped[int] = mapped_column(Integer, server_default="0")
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)
    finished_at: Mapped[str | None] = mapped_column(Text)


class TrustedWebResearchResult(Base):
    """Source snapshot from an official domain; never reviewed KB evidence."""

    __tablename__ = "trusted_web_research_results"
    __table_args__ = (
        CheckConstraint(
            "evidence_kind IN ('official_page_excerpt', 'official_link_only')",
            name="ck_trusted_web_research_result_kind",
        ),
        Index(
            "ux_trusted_web_research_results_run_ordinal",
            "run_id",
            "ordinal",
            unique=True,
        ),
        Index("ix_trusted_web_research_results_publisher_id", "publisher_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("trusted_web_research_runs.id", ondelete="RESTRICT"),
        index=True,
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    publisher_id: Mapped[str] = mapped_column(Text)
    publisher_name: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(Text)
    evidence_kind: Mapped[str] = mapped_column(Text)
    excerpt_text: Mapped[str | None] = mapped_column(Text)
    content_sha256: Mapped[str | None] = mapped_column(Text)
    retrieved_at: Mapped[str] = mapped_column(Text)


class TrustedWebResearchEvent(Base):
    """Append-only lifecycle history for live research."""

    __tablename__ = "trusted_web_research_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("trusted_web_research_runs.id", ondelete="RESTRICT"),
        index=True,
    )
    action: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(Text)
    detail: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)


class ResearchEvidenceIntake(Base):
    """Reviewer state between a live research lead and approved evidence."""

    __tablename__ = "research_evidence_intakes"
    __table_args__ = (
        CheckConstraint(
            "status IN ('evidence_intake', 'needs_corroboration', 'low_quality', "
            "'rejected', 'archived', 'evidence_linked', 'claim_drafted')",
            name="ck_research_evidence_intake_status",
        ),
        CheckConstraint(
            "proposed_claim_type IS NULL OR proposed_claim_type IN "
            "('definition', 'behavior', 'constraint', 'safety')",
            name="ck_research_evidence_intake_claim_type",
        ),
        CheckConstraint(
            "(evidence_source_id IS NULL AND evidence_passage_id IS NULL) OR "
            "(evidence_source_id IS NOT NULL AND evidence_passage_id IS NOT NULL)",
            name="ck_research_evidence_intake_evidence_pair",
        ),
        Index("ix_research_evidence_intakes_status", "status"),
    )

    result_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("trusted_web_research_results.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    status: Mapped[str] = mapped_column(Text)
    setting_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("settings.id", ondelete="RESTRICT"), index=True
    )
    canonical_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("canonical_settings.canonical_id", ondelete="RESTRICT"), index=True
    )
    hardware_scope_json: Mapped[str] = mapped_column(Text, server_default="{}")
    bios_scope_json: Mapped[str] = mapped_column(Text, server_default="{}")
    proposed_claim_text: Mapped[str | None] = mapped_column(Text)
    proposed_claim_type: Mapped[str | None] = mapped_column(Text)
    evidence_source_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("evidence_sources.id", ondelete="RESTRICT")
    )
    evidence_passage_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("evidence_passages.id", ondelete="RESTRICT")
    )
    draft_claim_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("guidance_claims.id", ondelete="RESTRICT")
    )
    reviewer: Mapped[str] = mapped_column(Text)
    review_note: Mapped[str] = mapped_column(Text)
    reviewed_at: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text)


class ResearchEvidenceIntakeEvent(Base):
    """Append-only history for every live-research triage and promotion step."""

    __tablename__ = "research_evidence_intake_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    result_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("research_evidence_intakes.result_id", ondelete="RESTRICT"),
        index=True,
    )
    action: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(Text)
    detail: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)


class ChipsetFamily(Base):
    __tablename__ = "chipset_families"

    family_id: Mapped[str] = mapped_column(Text, primary_key=True)
    canonical_name: Mapped[str] = mapped_column(Text, unique=True)
    platform_id: Mapped[str] = mapped_column(
        Text, ForeignKey("hardware_platforms.platform_id", ondelete="RESTRICT"), index=True
    )


class Chipset(Base):
    __tablename__ = "chipsets"

    chipset_id: Mapped[str] = mapped_column(Text, primary_key=True)
    canonical_name: Mapped[str] = mapped_column(Text, unique=True)
    family_id: Mapped[str] = mapped_column(
        Text, ForeignKey("chipset_families.family_id", ondelete="RESTRICT"), index=True
    )
    platform_id: Mapped[str] = mapped_column(
        Text, ForeignKey("hardware_platforms.platform_id", ondelete="RESTRICT"), index=True
    )


class MotherboardModel(Base):
    __tablename__ = "motherboard_models"

    model_id: Mapped[str] = mapped_column(Text, primary_key=True)
    vendor_id: Mapped[str] = mapped_column(
        Text, ForeignKey("vendors.vendor_id", ondelete="RESTRICT"), index=True
    )
    canonical_name: Mapped[str] = mapped_column(Text)
    chipset_id: Mapped[str] = mapped_column(
        Text, ForeignKey("chipsets.chipset_id", ondelete="RESTRICT"), index=True
    )
    platform_id: Mapped[str] = mapped_column(
        Text, ForeignKey("hardware_platforms.platform_id", ondelete="RESTRICT"), index=True
    )


class Manual(Base):
    __tablename__ = "manuals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    original_filename: Mapped[str] = mapped_column(Text)
    stored_filename: Mapped[str] = mapped_column(Text, unique=True)
    sha256: Mapped[str] = mapped_column(Text, unique=True)
    file_size_bytes: Mapped[int] = mapped_column(Integer)
    page_count: Mapped[int] = mapped_column(Integer)
    vendor: Mapped[str | None] = mapped_column(Text)
    vendor_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("vendors.vendor_id", ondelete="RESTRICT"), index=True
    )
    model_name: Mapped[str | None] = mapped_column(Text)
    document_title: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    document_version: Mapped[str | None] = mapped_column(Text)
    published_on: Mapped[str | None] = mapped_column(Text)
    retrieved_at: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(Text)
    board_revision: Mapped[str | None] = mapped_column(Text)
    chipset: Mapped[str | None] = mapped_column(Text)
    document_type: Mapped[str | None] = mapped_column(Text)
    eligibility_status: Mapped[str] = mapped_column(Text, server_default="pending")
    eligibility_confidence: Mapped[int | None] = mapped_column(Integer)
    eligibility_reasons: Mapped[str | None] = mapped_column(Text)
    eligibility_reviewed_at: Mapped[str | None] = mapped_column(Text)
    eligibility_reviewer: Mapped[str | None] = mapped_column(Text)
    eligibility_note: Mapped[str | None] = mapped_column(Text)
    acceptance_reviewed_at: Mapped[str | None] = mapped_column(Text)
    acceptance_reviewer: Mapped[str | None] = mapped_column(Text)
    acceptance_notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)


class ManualApplicability(Base):
    __tablename__ = "manual_applicability"

    manual_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("manuals.id", ondelete="CASCADE"), primary_key=True
    )
    scope: Mapped[str] = mapped_column(Text, index=True)
    normalization_version: Mapped[str] = mapped_column(Text)
    model_status: Mapped[str] = mapped_column(Text)
    detected_model_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("motherboard_models.model_id", ondelete="RESTRICT"), index=True
    )
    warnings_json: Mapped[str] = mapped_column(Text, server_default="[]")
    normalized_at: Mapped[str] = mapped_column(Text)


class ManualApplicabilityTarget(Base):
    __tablename__ = "manual_applicability_targets"
    __table_args__ = (
        CheckConstraint(
            "(platform_id IS NOT NULL) + (chipset_family_id IS NOT NULL) + "
            "(chipset_id IS NOT NULL) + (motherboard_model_id IS NOT NULL) = 1",
            name="ck_manual_applicability_target_exactly_one",
        ),
        Index("ix_manual_applicability_targets_platform", "platform_id"),
        Index("ix_manual_applicability_targets_family", "chipset_family_id"),
        Index("ix_manual_applicability_targets_chipset", "chipset_id"),
        Index("ix_manual_applicability_targets_model", "motherboard_model_id"),
    )

    manual_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("manual_applicability.manual_id", ondelete="CASCADE"),
        primary_key=True,
    )
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("hardware_platforms.platform_id", ondelete="RESTRICT")
    )
    chipset_family_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("chipset_families.family_id", ondelete="RESTRICT")
    )
    chipset_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("chipsets.chipset_id", ondelete="RESTRICT")
    )
    motherboard_model_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("motherboard_models.model_id", ondelete="RESTRICT")
    )


class ManualEvent(Base):
    """Append-only audit history for source/provenance metadata."""

    __tablename__ = "manual_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    manual_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("manuals.id", ondelete="CASCADE"), index=True
    )
    action: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(Text)
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)


class IngestionBatch(Base):
    __tablename__ = "ingestion_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(Text)
    total_count: Mapped[int] = mapped_column(Integer)
    accepted_count: Mapped[int] = mapped_column(Integer)
    failed_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(Text)
    finished_at: Mapped[str | None] = mapped_column(Text)


class IngestionBatchItem(Base):
    __tablename__ = "ingestion_batch_items"
    __table_args__ = (Index("ix_ingestion_batch_items_batch_id", "batch_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ingestion_batches.id", ondelete="CASCADE")
    )
    manual_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("manuals.id", ondelete="SET NULL"), index=True
    )
    original_filename: Mapped[str] = mapped_column(Text)
    intake_status: Mapped[str] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)


class ExtractionRun(Base):
    __tablename__ = "extraction_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Unique: exactly one run per manual in v0.1 (FR-12).
    manual_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("manuals.id", ondelete="CASCADE"), unique=True
    )
    status: Mapped[str] = mapped_column(Text)  # pending | running | succeeded | failed
    error_message: Mapped[str | None] = mapped_column(Text)
    pages_processed: Mapped[int] = mapped_column(Integer, server_default="0")
    settings_found: Mapped[int] = mapped_column(Integer, server_default="0")
    # NULL until a parser actually processes the manual; set when parsing runs.
    parser_version: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)


class Setting(Base):
    __tablename__ = "settings"
    __table_args__ = (
        Index("ix_settings_status", "status"),
        Index("ix_settings_manual_id_status", "manual_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    manual_id: Mapped[int] = mapped_column(Integer, ForeignKey("manuals.id", ondelete="CASCADE"))
    extraction_run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("extraction_runs.id", ondelete="CASCADE"), index=True
    )
    extracted_name: Mapped[str] = mapped_column(Text)  # immutable after insert (FR-16)
    extracted_description: Mapped[str] = mapped_column(Text)  # immutable; may be empty
    edited_name: Mapped[str | None] = mapped_column(Text)
    edited_description: Mapped[str | None] = mapped_column(Text)
    page_number: Mapped[int] = mapped_column(Integer)  # immutable extracted PDF page
    edited_page_number: Mapped[int | None] = mapped_column(Integer)
    page_review_status: Mapped[str] = mapped_column(Text, server_default="pending")
    status: Mapped[str] = mapped_column(Text, server_default="draft")  # draft | approved | rejected
    review_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text)


class CanonicalSetting(Base):
    __tablename__ = "canonical_settings"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'deprecated')",
            name="ck_canonical_setting_status",
        ),
        Index("ix_canonical_settings_status", "status"),
    )

    canonical_id: Mapped[str] = mapped_column(Text, primary_key=True)
    canonical_name: Mapped[str] = mapped_column(Text, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="active")
    created_at: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text)


class CanonicalIdentityEvent(Base):
    __tablename__ = "canonical_identity_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("canonical_settings.canonical_id", ondelete="CASCADE"),
        index=True,
    )
    action: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(Text)
    detail: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)


class SettingMenuContext(Base):
    """Current reviewed menu-path projection for one approved source setting."""

    __tablename__ = "setting_menu_contexts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'revoked')",
            name="ck_setting_menu_context_status",
        ),
        Index("ix_setting_menu_contexts_status", "status"),
    )

    setting_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("settings.id", ondelete="CASCADE"),
        primary_key=True,
    )
    menu_path: Mapped[str] = mapped_column(Text)
    normalized_menu_path: Mapped[str] = mapped_column(Text)
    source_page_number: Mapped[int] = mapped_column(Integer)
    evidence_excerpt: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    current_event_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("setting_menu_context_events.id", ondelete="RESTRICT"),
        unique=True,
    )
    actor: Mapped[str] = mapped_column(Text)
    reviewed_at: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text)


class SettingMenuContextEvent(Base):
    """Append-only audit history for reviewed menu-path evidence."""

    __tablename__ = "setting_menu_context_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    setting_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("settings.id", ondelete="CASCADE"),
        index=True,
    )
    action: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(Text)
    detail: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)


class SettingMenuContextCitation(Base):
    """Append-only official-manual page citation attached to one review event."""

    __tablename__ = "setting_menu_context_citations"
    __table_args__ = (
        Index(
            "ux_setting_menu_context_citations_event_page",
            "event_id",
            "page_number",
            unique=True,
        ),
        Index("ix_setting_menu_context_citations_setting_id", "setting_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    setting_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("settings.id", ondelete="CASCADE"),
    )
    event_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("setting_menu_context_events.id", ondelete="CASCADE"),
    )
    page_number: Mapped[int] = mapped_column(Integer)
    evidence_excerpt: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)


class CanonicalSettingCategory(Base):
    __tablename__ = "canonical_setting_categories"

    category_id: Mapped[str] = mapped_column(Text, primary_key=True)
    canonical_name: Mapped[str] = mapped_column(Text, unique=True)
    description: Mapped[str] = mapped_column(Text)
    ordinal: Mapped[int] = mapped_column(Integer, unique=True)


class CanonicalSettingClassification(Base):
    __tablename__ = "canonical_setting_classifications"
    __table_args__ = (
        CheckConstraint(
            "risk_level IN ('unclassified', 'low', 'moderate', 'high', 'critical')",
            name="ck_canonical_setting_classification_risk",
        ),
        Index("ix_canonical_setting_classifications_category_id", "category_id"),
        Index("ix_canonical_setting_classifications_risk_level", "risk_level"),
    )

    canonical_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("canonical_settings.canonical_id", ondelete="CASCADE"),
        primary_key=True,
    )
    category_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("canonical_setting_categories.category_id", ondelete="RESTRICT"),
    )
    risk_level: Mapped[str] = mapped_column(Text)
    risk_rationale: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text)


class CanonicalClassificationEvent(Base):
    __tablename__ = "canonical_classification_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("canonical_settings.canonical_id", ondelete="CASCADE"),
        index=True,
    )
    action: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(Text)
    detail: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)


class CanonicalSettingAlias(Base):
    __tablename__ = "canonical_setting_aliases"
    __table_args__ = (
        Index("ix_canonical_setting_aliases_canonical_id", "canonical_id"),
        Index("ix_canonical_setting_aliases_vendor_id", "vendor_id"),
    )

    normalized_alias: Mapped[str] = mapped_column(Text, primary_key=True)
    canonical_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("canonical_settings.canonical_id", ondelete="CASCADE"),
    )
    display_alias: Mapped[str] = mapped_column(Text)
    vendor_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("vendors.vendor_id", ondelete="RESTRICT"),
    )


class SettingCanonicalMapping(Base):
    __tablename__ = "setting_canonical_mappings"
    __table_args__ = (
        CheckConstraint(
            "mapping_method IN ('reviewer', 'seed', 'automatic')",
            name="ck_setting_canonical_mapping_method",
        ),
        Index("ix_setting_canonical_mappings_canonical_id", "canonical_id"),
    )

    setting_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("settings.id", ondelete="CASCADE"),
        primary_key=True,
    )
    canonical_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("canonical_settings.canonical_id", ondelete="RESTRICT"),
    )
    mapping_method: Mapped[str] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text)


class CanonicalMappingEvent(Base):
    __tablename__ = "canonical_mapping_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    setting_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("settings.id", ondelete="CASCADE"),
        index=True,
    )
    action: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(Text)
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)


class CanonicalMappingSuggestion(Base):
    __tablename__ = "canonical_mapping_suggestions"
    __table_args__ = (
        CheckConstraint(
            "match_type IN ('exact_alias', 'contextual_evidence')",
            name="ck_canonical_mapping_suggestion_match_type",
        ),
        CheckConstraint(
            "status IN ('pending', 'accepted', 'dismissed')",
            name="ck_canonical_mapping_suggestion_status",
        ),
        CheckConstraint(
            "confidence_basis_points BETWEEN 0 AND 10000",
            name="ck_canonical_mapping_suggestion_confidence",
        ),
        Index(
            "ux_canonical_mapping_suggestions_setting_canonical",
            "setting_id",
            "canonical_id",
            unique=True,
        ),
        Index("ix_canonical_mapping_suggestions_status", "status"),
        Index("ix_canonical_mapping_suggestions_canonical_id", "canonical_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    setting_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("settings.id", ondelete="CASCADE"),
    )
    canonical_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("canonical_settings.canonical_id", ondelete="CASCADE"),
    )
    match_type: Mapped[str] = mapped_column(Text)
    matched_alias: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    confidence_basis_points: Mapped[int] = mapped_column(Integer, server_default="0")
    evidence_json: Mapped[str] = mapped_column(Text, server_default="{}")
    engine_version: Mapped[str] = mapped_column(Text, server_default="legacy")
    review_note: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)
    reviewed_at: Mapped[str | None] = mapped_column(Text)


class CanonicalSuggestionEvent(Base):
    __tablename__ = "canonical_suggestion_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    suggestion_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("canonical_mapping_suggestions.id", ondelete="CASCADE"),
        index=True,
    )
    action: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(Text)
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)


class CanonicalBacklogAssessment(Base):
    """Current classified state for one investigated canonicalization record."""

    __tablename__ = "canonical_backlog_assessments"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('verified_exact_mapping', 'strong_candidate', "
            "'likely_equivalent', 'partial_equivalence', 'related_not_equivalent', "
            "'vendor_specific', 'needs_human_review', 'needs_research', "
            "'insufficient_evidence', 'conflict')",
            name="ck_canonical_backlog_assessment_outcome",
        ),
        CheckConstraint(
            "confidence_basis_points BETWEEN 0 AND 10000",
            name="ck_canonical_backlog_assessment_confidence",
        ),
        CheckConstraint(
            "assessment_method IN ('deterministic_triage', 'human_review', 'external_research')",
            name="ck_canonical_backlog_assessment_method",
        ),
        CheckConstraint(
            "review_status IN ('system_assessed', 'human_confirmed')",
            name="ck_canonical_backlog_assessment_review_status",
        ),
        Index("ix_canonical_backlog_assessments_outcome", "outcome"),
        Index("ix_canonical_backlog_assessments_review_status", "review_status"),
    )

    setting_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("settings.id", ondelete="RESTRICT"), primary_key=True
    )
    outcome: Mapped[str] = mapped_column(Text)
    candidate_canonical_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("canonical_settings.canonical_id", ondelete="RESTRICT"),
        index=True,
    )
    confidence_basis_points: Mapped[int] = mapped_column(Integer)
    evidence_json: Mapped[str] = mapped_column(Text)
    engine_version: Mapped[str] = mapped_column(Text)
    assessment_method: Mapped[str] = mapped_column(Text)
    review_status: Mapped[str] = mapped_column(Text)
    reviewer: Mapped[str | None] = mapped_column(Text)
    review_note: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text)


class CanonicalBacklogAssessmentEvent(Base):
    """Append-only history for automated triage and reviewer conclusions."""

    __tablename__ = "canonical_backlog_assessment_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    setting_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("canonical_backlog_assessments.setting_id", ondelete="RESTRICT"),
        index=True,
    )
    action: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(Text)
    detail: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)


class CanonicalIdentityProposal(Base):
    """Durable reviewer queue item for a proposed new canonical identity."""

    __tablename__ = "canonical_identity_proposals"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="ck_canonical_identity_proposal_status",
        ),
        CheckConstraint(
            "confidence_basis_points BETWEEN 0 AND 10000",
            name="ck_canonical_identity_proposal_confidence",
        ),
        Index("ux_canonical_identity_proposals_canonical_id", "canonical_id", unique=True),
        Index("ix_canonical_identity_proposals_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_id: Mapped[str] = mapped_column(Text)
    canonical_name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    confidence_basis_points: Mapped[int] = mapped_column(Integer)
    evidence_json: Mapped[str] = mapped_column(Text)
    engine_version: Mapped[str] = mapped_column(Text)
    review_note: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str | None] = mapped_column(Text)
    approved_canonical_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("canonical_settings.canonical_id", ondelete="RESTRICT"),
    )
    created_at: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text)
    reviewed_at: Mapped[str | None] = mapped_column(Text)


class CanonicalIdentityProposalSetting(Base):
    __tablename__ = "canonical_identity_proposal_settings"
    __table_args__ = (
        Index(
            "ux_canonical_identity_proposal_settings",
            "proposal_id",
            "setting_id",
            unique=True,
        ),
        Index("ix_canonical_identity_proposal_settings_setting_id", "setting_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    proposal_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("canonical_identity_proposals.id", ondelete="CASCADE"),
    )
    setting_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("settings.id", ondelete="RESTRICT"),
    )
    created_at: Mapped[str] = mapped_column(Text)


class CanonicalIdentityProposalEvent(Base):
    __tablename__ = "canonical_identity_proposal_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    proposal_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("canonical_identity_proposals.id", ondelete="CASCADE"),
        index=True,
    )
    action: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(Text)
    detail: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)


class ReviewEvent(Base):
    __tablename__ = "review_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    setting_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("settings.id", ondelete="CASCADE"), index=True
    )
    action: Mapped[str] = mapped_column(Text)  # edited | approved | rejected | reopened
    actor: Mapped[str] = mapped_column(Text)
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)


class MissedSetting(Base):
    """Curator-recorded ground truth for a setting the parser did not emit."""

    __tablename__ = "missed_settings"
    __table_args__ = (Index("ix_missed_settings_manual_status", "manual_id", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    manual_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("manuals.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(Text)
    page_number: Mapped[int] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="active")
    actor: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)
    dismissed_at: Mapped[str | None] = mapped_column(Text)
    dismissal_reason: Mapped[str | None] = mapped_column(Text)
