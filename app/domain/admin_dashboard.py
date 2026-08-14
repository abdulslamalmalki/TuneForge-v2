"""Read-only operational projection for the TuneForge admin dashboard."""

from dataclasses import dataclass

from sqlalchemy import desc, distinct, func, select
from sqlalchemy.orm import Session

from app.db.models import (
    CanonicalBacklogAssessment,
    CanonicalBacklogAssessmentEvent,
    CanonicalMappingEvent,
    CanonicalMappingSuggestion,
    CanonicalSetting,
    EvidenceExtractionRun,
    EvidenceSource,
    EvidenceSourceEvent,
    ExtractionRun,
    GuidanceCandidate,
    GuidanceCandidateEvent,
    GuidanceClaim,
    GuidanceClaimApplicabilityTarget,
    GuidanceClaimBiosScope,
    GuidanceClaimConflict,
    GuidanceClaimEvent,
    GuidanceClaimSetting,
    GuidanceClaimSourceSetting,
    Manual,
    ManualApplicabilityTarget,
    ManualEvent,
    RecommendationClaim,
    RecommendationClaimEvent,
    ReferralPointerEvent,
    ResearchEvidenceIntake,
    ResearchEvidenceIntakeEvent,
    ReviewEvent,
    Setting,
    SettingCanonicalMapping,
    TrustedWebResearchResult,
    UnansweredLookup,
)
from app.domain import am5_coverage, manuals, refusal_gaps


@dataclass(frozen=True)
class DashboardMetric:
    label: str
    value: int
    detail: str
    href: str
    tone: str


@dataclass(frozen=True)
class DashboardTask:
    task_id: str
    label: str
    count: int
    detail: str
    href: str
    action: str
    severity: str


@dataclass(frozen=True)
class DashboardCoverage:
    coverage_id: str
    label: str
    numerator: int
    denominator: int
    percent: float
    detail: str
    href: str


@dataclass(frozen=True)
class DashboardFootprint:
    label: str
    value: int
    detail: str


@dataclass(frozen=True)
class DashboardActivity:
    kind: str
    subject: str
    action: str
    actor: str
    created_at: str
    href: str


@dataclass(frozen=True)
class DashboardHealth:
    status: str
    label: str
    detail: str


@dataclass(frozen=True)
class AdminDashboard:
    health: DashboardHealth
    metrics: tuple[DashboardMetric, ...]
    coverages: tuple[DashboardCoverage, ...]
    footprint: tuple[DashboardFootprint, ...]
    tasks: tuple[DashboardTask, ...]
    active_tasks: tuple[DashboardTask, ...]
    clear_tasks: tuple[DashboardTask, ...]
    activities: tuple[DashboardActivity, ...]
    active_task_count: int
    am5_expansion_status: str
    am5_gate_count: int
    am5_passed_gate_count: int


def build_dashboard(session: Session) -> AdminDashboard:
    manual_rows = manuals.list_manuals(session)
    manual_count = len(manual_rows)
    certified_manual_count = sum(row.acceptance_reviewed_at is not None for row in manual_rows)
    incomplete_provenance = sum(bool(manuals.missing_provenance_fields(row)) for row in manual_rows)
    pending_eligibility = sum(row.eligibility_status == "pending" for row in manual_rows)

    approved_setting_count = _count(session, Setting, Setting.status == "approved")
    draft_setting_count = _count(session, Setting, Setting.status == "draft")
    failed_extraction_count = _count(session, ExtractionRun, ExtractionRun.status == "failed")
    canonical_count = _count(session, CanonicalSetting, CanonicalSetting.status == "active")
    mapped_approved_setting_count = int(
        session.scalar(
            select(func.count(distinct(SettingCanonicalMapping.setting_id)))
            .join(Setting, Setting.id == SettingCanonicalMapping.setting_id)
            .where(Setting.status == "approved")
        )
        or 0
    )
    unmapped_approved_setting_count = max(
        approved_setting_count - mapped_approved_setting_count, 0
    )
    pending_suggestion_count = _count(
        session,
        CanonicalMappingSuggestion,
        CanonicalMappingSuggestion.status == "pending",
    )
    canonical_conflict_count = _count(
        session,
        CanonicalBacklogAssessment,
        CanonicalBacklogAssessment.outcome == "conflict",
        CanonicalBacklogAssessment.review_status == "system_assessed",
    )

    approved_source_count = _count(
        session,
        EvidenceSource,
        EvidenceSource.status == "approved",
    )
    pending_source_count = _count(
        session,
        EvidenceSource,
        EvidenceSource.status == "pending",
    )
    failed_evidence_extraction_count = _count(
        session,
        EvidenceExtractionRun,
        EvidenceExtractionRun.status == "failed",
    )
    approved_claim_count = _count(
        session,
        GuidanceClaim,
        GuidanceClaim.status == "approved",
    )
    hardware_scoped_claim_count = _count(
        session,
        GuidanceClaim,
        GuidanceClaim.status == "approved",
        GuidanceClaim.applicability_scope == "reviewed_hardware",
    )
    draft_claim_count = _count(session, GuidanceClaim, GuidanceClaim.status == "draft")
    approved_recommendation_count = _count(
        session,
        RecommendationClaim,
        RecommendationClaim.status == "approved",
    )
    draft_recommendation_count = _count(
        session,
        RecommendationClaim,
        RecommendationClaim.status == "draft",
    )
    pending_knowledge_conflict_count = _count(
        session,
        GuidanceClaimConflict,
        GuidanceClaimConflict.status == "pending",
    )
    pending_candidate_count = _count(
        session, GuidanceCandidate, GuidanceCandidate.status == "pending"
    )
    research_candidate_count = _count(
        session, GuidanceCandidate, GuidanceCandidate.status == "needs_research"
    )
    unreviewed_research_count = int(
        session.scalar(
            select(func.count(TrustedWebResearchResult.id))
            .outerjoin(
                ResearchEvidenceIntake,
                ResearchEvidenceIntake.result_id == TrustedWebResearchResult.id,
            )
            .where(ResearchEvidenceIntake.result_id.is_(None))
        )
        or 0
    )
    covered_canonical_count = (
        session.scalar(
            select(func.count(distinct(GuidanceClaimSetting.canonical_id)))
            .join(GuidanceClaim, GuidanceClaim.id == GuidanceClaimSetting.claim_id)
            .join(
                CanonicalSetting,
                CanonicalSetting.canonical_id == GuidanceClaimSetting.canonical_id,
            )
            .where(
                GuidanceClaim.status == "approved",
                CanonicalSetting.status == "active",
            )
        )
        or 0
    )
    directly_evidenced_setting_count = _distinct_approved_claim_link_count(
        session,
        GuidanceClaimSourceSetting.setting_id,
        Setting,
        Setting.id == GuidanceClaimSourceSetting.setting_id,
        Setting.status == "approved",
    )
    hardware_scoped_approved_claim_count = _distinct_approved_claim_link_count(
        session,
        GuidanceClaimApplicabilityTarget.claim_id,
    )
    bios_scoped_approved_claim_count = _distinct_approved_claim_link_count(
        session,
        GuidanceClaimBiosScope.claim_id,
    )
    recommendation_canonical_count = int(
        session.scalar(
            select(func.count(distinct(RecommendationClaim.canonical_id)))
            .select_from(RecommendationClaim)
            .join(
                CanonicalSetting,
                CanonicalSetting.canonical_id == RecommendationClaim.canonical_id,
            )
            .where(
                RecommendationClaim.status == "approved",
                CanonicalSetting.status == "active",
            )
        )
        or 0
    )
    vendor_coverage_count = int(
        session.scalar(
            select(func.count(distinct(Manual.vendor_id)))
            .join(Setting, Setting.manual_id == Manual.id)
            .where(Setting.status == "approved", Manual.vendor_id.is_not(None))
        )
        or 0
    )
    platform_coverage_count = _approved_setting_target_count(
        session, ManualApplicabilityTarget.platform_id
    )
    chipset_coverage_count = _approved_setting_target_count(
        session, ManualApplicabilityTarget.chipset_id
    )

    gap_worklist = refusal_gaps.list_gap_worklist(session)
    refusal_event_count = _count(session, UnansweredLookup)
    refusal_demand = sum(row.occurrence_count for row in gap_worklist)

    coverage = am5_coverage.build_report(session)
    passed_gate_count = sum(row.passed for row in coverage.gates)
    failed_count = failed_extraction_count + failed_evidence_extraction_count
    health = _health(
        failed_count=failed_count,
        expansion_status=coverage.summary.expansion_status,
    )

    metrics = (
        DashboardMetric(
            "Official manuals",
            manual_count,
            f"{certified_manual_count} acceptance certified",
            "/manuals",
            "manuals",
        ),
        DashboardMetric(
            "Approved settings",
            approved_setting_count,
            f"{draft_setting_count} awaiting review",
            "/review",
            "settings",
        ),
        DashboardMetric(
            "Canonical settings",
            canonical_count,
            f"{covered_canonical_count} with accepted guidance",
            "/canonical-settings",
            "canonical",
        ),
        DashboardMetric(
            "Trusted sources",
            approved_source_count,
            f"{pending_source_count} awaiting approval",
            "/evidence-sources",
            "evidence",
        ),
        DashboardMetric(
            "Accepted claims",
            approved_claim_count,
            (
                f"{draft_claim_count} awaiting review · "
                f"{hardware_scoped_claim_count} hardware-scoped"
            ),
            "/guidance-claims",
            "claims",
        ),
        DashboardMetric(
            "Refusal demand",
            refusal_demand,
            f"{len(gap_worklist)} unique gaps · {refusal_event_count} events",
            "/refusal-gaps",
            "gaps",
        ),
        DashboardMetric(
            "Reviewed recommendations",
            approved_recommendation_count,
            f"{draft_recommendation_count} awaiting safety review",
            "/recommendation-claims",
            "recommendations",
        ),
    )
    coverages = (
        _coverage(
            "canonical",
            "Canonical mapping",
            mapped_approved_setting_count,
            approved_setting_count,
            f"{unmapped_approved_setting_count:,} approved settings remain in the backlog.",
            "/canonicalization/assessments",
        ),
        _coverage(
            "guidance",
            "Verified guidance",
            covered_canonical_count,
            canonical_count,
            "Active canonical identities with at least one approved factual claim.",
            "/guidance-candidates",
        ),
        _coverage(
            "evidence",
            "Direct source evidence",
            directly_evidenced_setting_count,
            approved_setting_count,
            "Approved Source Settings directly bound to an approved claim.",
            "/guidance-claims?status=approved",
        ),
        _coverage(
            "hardware",
            "Hardware scope",
            hardware_scoped_approved_claim_count,
            approved_claim_count,
            "Approved claims with an explicit reviewed hardware target.",
            "/guidance-claims?status=approved",
        ),
        _coverage(
            "bios",
            "BIOS-version scope",
            bios_scoped_approved_claim_count,
            approved_claim_count,
            "Approved claims with an explicit reviewed firmware boundary.",
            "/guidance-claims?status=approved",
        ),
        _coverage(
            "recommendation",
            "Safe recommendations",
            recommendation_canonical_count,
            canonical_count,
            "Active canonical identities with an approved recommendation claim.",
            "/recommendation-claims?status=approved",
        ),
    )
    footprint = (
        DashboardFootprint(
            "Vendors",
            vendor_coverage_count,
            "represented by approved Source Settings",
        ),
        DashboardFootprint(
            "Platforms",
            platform_coverage_count,
            "normalized targets represented by approved settings",
        ),
        DashboardFootprint(
            "Chipsets",
            chipset_coverage_count,
            "exact normalized targets represented by approved settings",
        ),
        DashboardFootprint(
            "Open conflicts",
            pending_knowledge_conflict_count,
            "scope-aware claim disagreements awaiting review",
        ),
    )
    tasks = (
        DashboardTask(
            "manual-provenance",
            "Manual provenance",
            incomplete_provenance,
            "Manuals missing required source identity or an official support page.",
            "/manuals",
            "Review manuals",
            "attention" if incomplete_provenance else "clear",
        ),
        DashboardTask(
            "manual-eligibility",
            "Eligibility decisions",
            pending_eligibility,
            "Uploaded documents waiting for a reviewer eligibility decision.",
            "/manuals",
            "Review eligibility",
            "attention" if pending_eligibility else "clear",
        ),
        DashboardTask(
            "extraction-failures",
            "Extraction failures",
            failed_count,
            (
                f"{failed_extraction_count} manual and "
                f"{failed_evidence_extraction_count} evidence extraction failures."
            ),
            "/manuals",
            "Inspect failures",
            "critical" if failed_count else "clear",
        ),
        DashboardTask(
            "setting-review",
            "BIOS setting drafts",
            draft_setting_count,
            "Extracted candidates waiting for approve, edit, or reject decisions.",
            "/review",
            "Open review queue",
            "attention" if draft_setting_count else "clear",
        ),
        DashboardTask(
            "evidence-review",
            "Evidence source review",
            pending_source_count,
            "Official tuning documents waiting for provenance approval.",
            "/evidence-sources",
            "Review evidence",
            "attention" if pending_source_count else "clear",
        ),
        DashboardTask(
            "canonical-mapping-backlog",
            "Canonical mapping backlog",
            unmapped_approved_setting_count,
            "Approved Source Settings investigated but not yet mapped to a verified identity.",
            "/canonicalization/assessments",
            "Review canonicalization",
            "normal" if unmapped_approved_setting_count else "clear",
        ),
        DashboardTask(
            "needs-guidance",
            "Canonical settings needing guidance",
            max(canonical_count - covered_canonical_count, 0),
            "Active canonical identities without an approved factual Guidance Claim.",
            "/guidance-candidates",
            "Build verified guidance",
            "normal" if canonical_count > covered_canonical_count else "clear",
        ),
        DashboardTask(
            "guidance-candidates",
            "Guidance candidates",
            pending_candidate_count + research_candidate_count,
            (
                f"{pending_candidate_count} pending review and "
                f"{research_candidate_count} needing research."
            ),
            "/guidance-candidates",
            "Review candidates",
            "attention" if pending_candidate_count else "normal",
        ),
        DashboardTask(
            "official-research-leads",
            "Official research leads",
            unreviewed_research_count,
            "Captured official-web excerpts waiting for human evidence-intake triage.",
            "/trusted-web-research?status=new",
            "Review research",
            "attention" if unreviewed_research_count else "clear",
        ),
        DashboardTask(
            "claim-review",
            "Guidance claim drafts",
            draft_claim_count,
            "Evidence-grounded claims waiting for a reviewer decision.",
            "/guidance-claims?status=draft",
            "Review claims",
            "attention" if draft_claim_count else "clear",
        ),
        DashboardTask(
            "recommendation-review",
            "Recommendation review",
            draft_recommendation_count,
            "Hardware-scoped actions awaiting evidence, risk, and conflict review.",
            "/recommendation-claims?status=draft",
            "Review recommendations",
            "attention" if draft_recommendation_count else "clear",
        ),
        DashboardTask(
            "knowledge-conflicts",
            "Knowledge conflicts",
            pending_knowledge_conflict_count,
            "Possible approved-claim disagreements waiting for scope-aware review.",
            "/guidance-conflicts?status=pending",
            "Review disagreements",
            "attention" if pending_knowledge_conflict_count else "clear",
        ),
        DashboardTask(
            "canonical-assessments",
            "Canonical assessments",
            canonical_conflict_count,
            "Complete backlog classifications with competing candidates to resolve.",
            "/canonicalization/assessments?outcome=conflict",
            "Review conflicts",
            "attention" if canonical_conflict_count else "clear",
        ),
        DashboardTask(
            "mapping-suggestions",
            "Mapping suggestions",
            pending_suggestion_count,
            "Exact aliases awaiting canonical mapping confirmation.",
            "/canonical-suggestions",
            "Review mappings",
            "attention" if pending_suggestion_count else "clear",
        ),
        DashboardTask(
            "refusal-gaps",
            "Unanswered lookup gaps",
            len(gap_worklist),
            f"Demand worklist with {refusal_demand} recorded lookup occurrences.",
            "/refusal-gaps",
            "Open demand worklist",
            "normal" if gap_worklist else "clear",
        ),
    )
    active_tasks = tuple(task for task in tasks if task.count > 0)
    clear_tasks = tuple(task for task in tasks if task.count == 0)
    return AdminDashboard(
        health=health,
        metrics=metrics,
        coverages=coverages,
        footprint=footprint,
        tasks=tasks,
        active_tasks=active_tasks,
        clear_tasks=clear_tasks,
        activities=_recent_activity(session),
        active_task_count=len(active_tasks),
        am5_expansion_status=coverage.summary.expansion_status,
        am5_gate_count=len(coverage.gates),
        am5_passed_gate_count=passed_gate_count,
    )


def _count(session: Session, model: type, *conditions: object) -> int:
    query = select(func.count()).select_from(model)
    if conditions:
        query = query.where(*conditions)
    return session.scalar(query) or 0


def _coverage(
    coverage_id: str,
    label: str,
    numerator: int,
    denominator: int,
    detail: str,
    href: str,
) -> DashboardCoverage:
    percent = round((numerator / denominator * 100) if denominator else 0.0, 1)
    return DashboardCoverage(
        coverage_id=coverage_id,
        label=label,
        numerator=numerator,
        denominator=denominator,
        percent=percent,
        detail=detail,
        href=href,
    )


def _distinct_approved_claim_link_count(
    session: Session,
    identity_column,
    joined_model: type | None = None,
    join_condition: object | None = None,
    joined_condition: object | None = None,
) -> int:
    query = (
        select(func.count(distinct(identity_column)))
        .select_from(identity_column.class_)
        .join(GuidanceClaim, GuidanceClaim.id == identity_column.class_.claim_id)
        .where(GuidanceClaim.status == "approved")
    )
    if joined_model is not None and join_condition is not None:
        query = query.join(joined_model, join_condition)
    if joined_condition is not None:
        query = query.where(joined_condition)
    return int(session.scalar(query) or 0)


def _approved_setting_target_count(session: Session, target_column) -> int:
    return int(
        session.scalar(
            select(func.count(distinct(target_column)))
            .select_from(ManualApplicabilityTarget)
            .join(Setting, Setting.manual_id == ManualApplicabilityTarget.manual_id)
            .where(Setting.status == "approved", target_column.is_not(None))
        )
        or 0
    )


def _health(*, failed_count: int, expansion_status: str) -> DashboardHealth:
    if failed_count:
        return DashboardHealth(
            "critical",
            "Pipeline attention required",
            f"{failed_count} extraction failure{'s' if failed_count != 1 else ''} need review.",
        )
    if expansion_status != "ready":
        return DashboardHealth(
            "attention",
            "AM5 validation is on hold",
            "No extraction failures are active, but at least one AM5 expansion gate is open.",
        )
    return DashboardHealth(
        "healthy",
        "All core systems healthy",
        "Pipelines are clear and the reviewed AM5 expansion gates are ready.",
    )


def _recent_activity(session: Session) -> tuple[DashboardActivity, ...]:
    activities: list[DashboardActivity] = []
    event_types = (
        (ManualEvent, "manual", "Manual", "manual_id", "/manuals/{}"),
        (ReviewEvent, "setting", "Setting", "setting_id", "/settings/{}"),
        (
            EvidenceSourceEvent,
            "evidence",
            "Evidence source",
            "source_id",
            "/evidence-sources/{}",
        ),
        (
            GuidanceClaimEvent,
            "claim",
            "Guidance claim",
            "claim_id",
            "/guidance-claims/{}",
        ),
        (
            GuidanceCandidateEvent,
            "candidate",
            "Guidance candidate",
            "candidate_id",
            "/guidance-candidates/{}",
        ),
        (
            RecommendationClaimEvent,
            "recommendation",
            "Recommendation claim",
            "claim_id",
            "/recommendation-claims/{}",
        ),
        (
            CanonicalMappingEvent,
            "mapping",
            "Setting mapping",
            "setting_id",
            "/settings/{}",
        ),
        (
            CanonicalBacklogAssessmentEvent,
            "assessment",
            "Canonical assessment",
            "setting_id",
            "/canonicalization/assessments/{}",
        ),
        (
            ReferralPointerEvent,
            "referral",
            "Referral pointer",
            "pointer_id",
            "/refusal-gaps",
        ),
        (
            ResearchEvidenceIntakeEvent,
            "research",
            "Research result",
            "result_id",
            "/trusted-web-research/results/{}",
        ),
    )
    for model, kind, label, subject_field, href_pattern in event_types:
        rows = session.scalars(
            select(model).order_by(desc(model.created_at), desc(model.id)).limit(6)
        )
        for row in rows:
            subject_id = getattr(row, subject_field)
            href = href_pattern.format(subject_id) if "{}" in href_pattern else href_pattern
            activities.append(
                DashboardActivity(
                    kind=kind,
                    subject=f"{label} {subject_id}",
                    action=row.action.replace("_", " "),
                    actor=row.actor,
                    created_at=row.created_at,
                    href=href,
                )
            )
    activities.sort(key=lambda row: row.created_at, reverse=True)
    return tuple(activities[:10])
