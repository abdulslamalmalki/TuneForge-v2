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
    GuidanceClaimConflict,
    GuidanceClaimEvent,
    GuidanceClaimSetting,
    ManualEvent,
    ReferralPointerEvent,
    ResearchEvidenceIntake,
    ResearchEvidenceIntakeEvent,
    ReviewEvent,
    Setting,
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
    tasks: tuple[DashboardTask, ...]
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
    canonical_count = _count(session, CanonicalSetting)
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
            .where(GuidanceClaim.status == "approved")
        )
        or 0
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
    return AdminDashboard(
        health=health,
        metrics=metrics,
        tasks=tasks,
        activities=_recent_activity(session),
        active_task_count=sum(task.count > 0 for task in tasks),
        am5_expansion_status=coverage.summary.expansion_status,
        am5_gate_count=len(coverage.gates),
        am5_passed_gate_count=passed_gate_count,
    )


def _count(session: Session, model: type, *conditions: object) -> int:
    query = select(func.count()).select_from(model)
    if conditions:
        query = query.where(*conditions)
    return session.scalar(query) or 0


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
