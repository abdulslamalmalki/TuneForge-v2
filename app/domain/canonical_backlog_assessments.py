"""Complete, conservative classification of the canonicalization backlog.

Assessments organize evidence and reviewer work. They never create a mapping or
canonical identity; those mutations remain in the existing 99.5%-gated flows.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import config
from app.auth.context import current_actor
from app.db.models import (
    CanonicalBacklogAssessment,
    CanonicalBacklogAssessmentEvent,
    CanonicalSetting,
    Manual,
    Setting,
    SettingCanonicalMapping,
)
from app.domain import canonicalization
from app.domain.errors import CanonicalSettingError
from app.domain.extraction_runs import utc_now_iso
from app.domain.review import effective_description, effective_name

ENGINE_VERSION = f"{canonicalization.ENGINE_VERSION}-classification-1.0"
SYSTEM_ACTOR = "canonical-backlog-classifier"
SYSTEM_ASSESSED = "system_assessed"
HUMAN_CONFIRMED = "human_confirmed"
OUTCOMES = (
    "verified_exact_mapping",
    "strong_candidate",
    "likely_equivalent",
    "partial_equivalence",
    "related_not_equivalent",
    "vendor_specific",
    "needs_human_review",
    "needs_research",
    "insufficient_evidence",
    "conflict",
)


@dataclass(frozen=True)
class AssessmentRunReport:
    evaluated_count: int
    created_count: int
    updated_count: int
    unchanged_count: int
    human_confirmed_preserved_count: int
    resolved_mapping_count: int
    outcome_counts: dict[str, int]


@dataclass(frozen=True)
class AssessmentQueueRow:
    assessment: CanonicalBacklogAssessment
    setting_name: str
    description: str
    vendor_id: str | None
    vendor_name: str
    manual_title: str
    page_number: int
    source_url: str | None
    candidate_name: str | None


@dataclass(frozen=True)
class AssessmentDetail:
    assessment: CanonicalBacklogAssessment
    setting: Setting
    manual: Manual
    candidate: CanonicalSetting | None
    evidence: dict[str, object]
    events: tuple[CanonicalBacklogAssessmentEvent, ...]
    previous_id: int | None
    next_id: int | None


@dataclass(frozen=True)
class AssessmentMetrics:
    approved_unmapped_count: int
    assessed_unmapped_count: int
    missing_assessment_count: int
    system_assessed_count: int
    human_confirmed_count: int
    outcome_counts: dict[str, int]


def refresh_assessments(
    session: Session,
    *,
    commit: bool = True,
) -> AssessmentRunReport:
    """Classify every current approved-unmapped setting without mapping it."""
    evaluation = canonicalization.evaluate_backlog(session)
    proposed = {
        setting_id: group
        for group in evaluation.proposed_groups
        for setting_id in group.setting_ids
    }
    now = utc_now_iso()
    counts: Counter[str] = Counter()
    current_ids = {entry.setting_id for entry in evaluation.entries}
    for entry in evaluation.entries:
        outcome = _classify(entry, in_identity_proposal=entry.setting_id in proposed)
        evidence = _evidence_payload(entry, proposed.get(entry.setting_id))
        evidence_json = json.dumps(evidence, sort_keys=True)
        row = session.get(CanonicalBacklogAssessment, entry.setting_id)
        if row is not None and row.review_status == HUMAN_CONFIRMED:
            counts["preserved"] += 1
            counts[row.outcome] += 1
            continue
        snapshot = {
            "outcome": outcome,
            "candidate_canonical_id": entry.candidate_canonical_id,
            "confidence_basis_points": entry.confidence_basis_points,
            "evidence_json": evidence_json,
            "engine_version": ENGINE_VERSION,
            "assessment_method": "deterministic_triage",
            "review_status": SYSTEM_ASSESSED,
        }
        if row is None:
            row = CanonicalBacklogAssessment(
                setting_id=entry.setting_id,
                **snapshot,
                reviewer=None,
                review_note=None,
                reviewed_at=None,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.flush()
            action = "system_assessed"
            counts["created"] += 1
        elif _snapshot(row) != snapshot:
            before = _snapshot(row)
            for key, value in snapshot.items():
                setattr(row, key, value)
            row.reviewer = None
            row.review_note = None
            row.reviewed_at = None
            row.updated_at = now
            action = "system_reassessed"
            evidence = {"before": before, "after": snapshot, "evidence": evidence}
            counts["updated"] += 1
        else:
            counts["unchanged"] += 1
            counts[outcome] += 1
            continue
        session.add(
            CanonicalBacklogAssessmentEvent(
                setting_id=entry.setting_id,
                action=action,
                actor=SYSTEM_ACTOR,
                detail=json.dumps(evidence, sort_keys=True),
                created_at=now,
            )
        )
        counts[outcome] += 1

    resolved = list(
        session.execute(
            select(CanonicalBacklogAssessment, SettingCanonicalMapping.canonical_id)
            .join(
                SettingCanonicalMapping,
                SettingCanonicalMapping.setting_id == CanonicalBacklogAssessment.setting_id,
            )
            .where(
                CanonicalBacklogAssessment.setting_id.not_in(current_ids),
                CanonicalBacklogAssessment.review_status == SYSTEM_ASSESSED,
                CanonicalBacklogAssessment.outcome != "verified_exact_mapping",
            )
        )
    )
    for row, canonical_id in resolved:
        before = _snapshot(row)
        row.outcome = "verified_exact_mapping"
        row.candidate_canonical_id = canonical_id
        row.confidence_basis_points = 10000
        row.evidence_json = json.dumps(
            {
                "mapping_record_present": True,
                "canonical_id": canonical_id,
                "notice": "Mapping provenance remains in canonical mapping events.",
            },
            sort_keys=True,
        )
        row.updated_at = now
        session.add(
            CanonicalBacklogAssessmentEvent(
                setting_id=row.setting_id,
                action="mapping_resolved",
                actor=SYSTEM_ACTOR,
                detail=json.dumps(
                    {"before": before, "canonical_id": canonical_id}, sort_keys=True
                ),
                created_at=now,
            )
        )
        counts["resolved"] += 1
    if commit:
        session.commit()
    else:
        session.flush()
    return AssessmentRunReport(
        evaluated_count=len(evaluation.entries),
        created_count=counts["created"],
        updated_count=counts["updated"],
        unchanged_count=counts["unchanged"],
        human_confirmed_preserved_count=counts["preserved"],
        resolved_mapping_count=counts["resolved"],
        outcome_counts={outcome: counts[outcome] for outcome in OUTCOMES},
    )


def list_assessments(
    session: Session,
    *,
    outcome: str = "",
    vendor_id: str = "",
    review_status: str = "",
    limit: int = 250,
) -> tuple[AssessmentQueueRow, ...]:
    if outcome and outcome not in OUTCOMES:
        raise CanonicalSettingError("Unknown canonical assessment outcome.")
    if review_status and review_status not in {SYSTEM_ASSESSED, HUMAN_CONFIRMED}:
        raise CanonicalSettingError("Unknown canonical assessment review status.")
    statement = (
        select(CanonicalBacklogAssessment, Setting, Manual, CanonicalSetting)
        .join(Setting, Setting.id == CanonicalBacklogAssessment.setting_id)
        .join(Manual, Manual.id == Setting.manual_id)
        .outerjoin(
            CanonicalSetting,
            CanonicalSetting.canonical_id
            == CanonicalBacklogAssessment.candidate_canonical_id,
        )
        .order_by(
            CanonicalBacklogAssessment.review_status.desc(),
            CanonicalBacklogAssessment.confidence_basis_points.desc(),
            CanonicalBacklogAssessment.setting_id,
        )
        .limit(max(1, min(limit, 500)))
    )
    if outcome:
        statement = statement.where(CanonicalBacklogAssessment.outcome == outcome)
    if vendor_id:
        statement = statement.where(Manual.vendor_id == vendor_id.strip().casefold())
    if review_status:
        statement = statement.where(CanonicalBacklogAssessment.review_status == review_status)
    return tuple(
        AssessmentQueueRow(
            assessment=assessment,
            setting_name=effective_name(setting),
            description=effective_description(setting),
            vendor_id=manual.vendor_id,
            vendor_name=manual.vendor or "Unknown vendor",
            manual_title=manual.document_title,
            page_number=setting.edited_page_number or setting.page_number,
            source_url=manual.source_url,
            candidate_name=candidate.canonical_name if candidate else None,
        )
        for assessment, setting, manual, candidate in session.execute(statement)
    )


def get_detail(session: Session, setting_id: int) -> AssessmentDetail | None:
    row = session.execute(
        select(CanonicalBacklogAssessment, Setting, Manual, CanonicalSetting)
        .join(Setting, Setting.id == CanonicalBacklogAssessment.setting_id)
        .join(Manual, Manual.id == Setting.manual_id)
        .outerjoin(
            CanonicalSetting,
            CanonicalSetting.canonical_id
            == CanonicalBacklogAssessment.candidate_canonical_id,
        )
        .where(CanonicalBacklogAssessment.setting_id == setting_id)
    ).first()
    if row is None:
        return None
    assessment, setting, manual, candidate = row
    ids = tuple(
        session.scalars(
            select(CanonicalBacklogAssessment.setting_id)
            .where(CanonicalBacklogAssessment.outcome == assessment.outcome)
            .order_by(
                CanonicalBacklogAssessment.confidence_basis_points.desc(),
                CanonicalBacklogAssessment.setting_id,
            )
        )
    )
    index = ids.index(setting_id)
    return AssessmentDetail(
        assessment=assessment,
        setting=setting,
        manual=manual,
        candidate=candidate,
        evidence=_json_object(assessment.evidence_json),
        events=tuple(
            session.scalars(
                select(CanonicalBacklogAssessmentEvent)
                .where(CanonicalBacklogAssessmentEvent.setting_id == setting_id)
                .order_by(CanonicalBacklogAssessmentEvent.id)
            )
        ),
        previous_id=ids[index - 1] if index > 0 else None,
        next_id=ids[index + 1] if index + 1 < len(ids) else None,
    )


def confirm_assessment(
    session: Session,
    setting_id: int,
    *,
    outcome: str,
    candidate_canonical_id: str = "",
    note: str,
    assessment_method: str = "human_review",
) -> CanonicalBacklogAssessment:
    if outcome not in OUTCOMES:
        raise CanonicalSettingError("Choose a supported assessment outcome.")
    if assessment_method not in {"human_review", "external_research"}:
        raise CanonicalSettingError("Assessment method must be human review or research.")
    note = " ".join(note.split())
    if not note:
        raise CanonicalSettingError("A reviewer rationale is required.")
    if len(note) > config.MAX_CANONICAL_MAPPING_NOTE_LENGTH:
        raise CanonicalSettingError("Assessment rationale is too long.")
    row = session.get(CanonicalBacklogAssessment, setting_id)
    if row is None:
        raise CanonicalSettingError("Canonical backlog assessment not found.")
    candidate_canonical_id = candidate_canonical_id.strip()
    if candidate_canonical_id:
        identity = session.get(CanonicalSetting, candidate_canonical_id)
        if identity is None or identity.status != "active":
            raise CanonicalSettingError("Candidate canonical identity is not active.")
    else:
        candidate_canonical_id = row.candidate_canonical_id or ""
    candidate_required = {
        "verified_exact_mapping",
        "strong_candidate",
        "likely_equivalent",
        "partial_equivalence",
        "related_not_equivalent",
    }
    if outcome in candidate_required and not candidate_canonical_id:
        raise CanonicalSettingError("This outcome requires a canonical candidate.")
    mapping = session.get(SettingCanonicalMapping, setting_id)
    if outcome == "verified_exact_mapping" and (
        mapping is None or mapping.canonical_id != candidate_canonical_id
    ):
        raise CanonicalSettingError(
            "Verified exact mapping requires an existing audited mapping record."
        )
    before = _snapshot(row)
    now = utc_now_iso()
    row.outcome = outcome
    row.candidate_canonical_id = candidate_canonical_id or None
    row.assessment_method = assessment_method
    row.review_status = HUMAN_CONFIRMED
    row.reviewer = current_actor()
    row.review_note = note
    row.reviewed_at = now
    row.updated_at = now
    session.add(
        CanonicalBacklogAssessmentEvent(
            setting_id=setting_id,
            action="human_confirmed",
            actor=current_actor(),
            detail=json.dumps(
                {
                    "before": before,
                    "after": _snapshot(row),
                    "note": note,
                },
                sort_keys=True,
            ),
            created_at=now,
        )
    )
    session.commit()
    return row


def metrics(session: Session) -> AssessmentMetrics:
    approved_unmapped = session.scalar(
        select(func.count())
        .select_from(Setting)
        .outerjoin(
            SettingCanonicalMapping,
            SettingCanonicalMapping.setting_id == Setting.id,
        )
        .where(Setting.status == "approved", SettingCanonicalMapping.setting_id.is_(None))
    ) or 0
    assessed_unmapped = session.scalar(
        select(func.count())
        .select_from(CanonicalBacklogAssessment)
        .join(Setting, Setting.id == CanonicalBacklogAssessment.setting_id)
        .outerjoin(
            SettingCanonicalMapping,
            SettingCanonicalMapping.setting_id == CanonicalBacklogAssessment.setting_id,
        )
        .where(Setting.status == "approved", SettingCanonicalMapping.setting_id.is_(None))
    ) or 0
    review_counts = dict(
        session.execute(
            select(CanonicalBacklogAssessment.review_status, func.count())
            .group_by(CanonicalBacklogAssessment.review_status)
        ).all()
    )
    outcome_counts = dict(
        session.execute(
            select(CanonicalBacklogAssessment.outcome, func.count())
            .group_by(CanonicalBacklogAssessment.outcome)
        ).all()
    )
    return AssessmentMetrics(
        approved_unmapped_count=approved_unmapped,
        assessed_unmapped_count=assessed_unmapped,
        missing_assessment_count=max(0, approved_unmapped - assessed_unmapped),
        system_assessed_count=review_counts.get(SYSTEM_ASSESSED, 0),
        human_confirmed_count=review_counts.get(HUMAN_CONFIRMED, 0),
        outcome_counts={outcome: outcome_counts.get(outcome, 0) for outcome in OUTCOMES},
    )


def _classify(
    entry: canonicalization.CanonicalizationEntry,
    *,
    in_identity_proposal: bool,
) -> str:
    if in_identity_proposal:
        return "needs_human_review"
    if any("Competing canonical candidates" in reason for reason in entry.reasons):
        return "conflict"
    if entry.candidate_canonical_id and entry.confidence_basis_points >= 9000:
        return "strong_candidate"
    if entry.candidate_canonical_id and entry.confidence_basis_points >= 8000:
        return "likely_equivalent"
    if entry.candidate_canonical_id and entry.confidence_basis_points >= 6500:
        return "needs_human_review"
    if entry.candidate_canonical_id:
        return "needs_research"
    return "insufficient_evidence"


def _evidence_payload(
    entry: canonicalization.CanonicalizationEntry,
    proposal: canonicalization.ProposedCanonicalGroup | None,
) -> dict[str, object]:
    return {
        "engine_version": ENGINE_VERSION,
        "setting_id": entry.setting_id,
        "source": {
            "manual_id": entry.manual_id,
            "manual_title": entry.manual_title,
            "vendor_id": entry.vendor_id,
            "vendor_name": entry.vendor_name,
            "page_number": entry.page_number,
            "source_url": entry.source_url,
            "source_trusted": entry.source_trusted,
        },
        "setting": {
            "name": entry.effective_name,
            "description": entry.effective_description,
            "reviewed_menu_path": entry.reviewed_menu_path,
        },
        "candidate": {
            "canonical_id": entry.candidate_canonical_id,
            "canonical_name": entry.candidate_canonical_name,
            "confidence_basis_points": entry.confidence_basis_points,
            "automatic_eligible": entry.automatic_eligible,
            "support_setting_ids": list(entry.support_setting_ids),
        },
        "reasons": list(entry.reasons),
        "signals": [
            {
                "key": signal.key,
                "label": signal.label,
                "state": signal.state,
                "score_basis_points": signal.score_basis_points,
                "detail": signal.detail,
            }
            for signal in entry.signals
        ],
        "identity_proposal": (
            {
                "canonical_id": proposal.canonical_id,
                "canonical_name": proposal.canonical_name,
                "setting_ids": list(proposal.setting_ids),
                "vendor_ids": list(proposal.vendor_ids),
                "confidence_basis_points": proposal.confidence_basis_points,
                "automatic_eligible": proposal.automatic_eligible,
                "reasons": list(proposal.reasons),
            }
            if proposal
            else None
        ),
        "policy": {
            "automatic_mapping_threshold_basis_points": (
                canonicalization.CONFIDENCE_THRESHOLD_BP
            ),
            "assessment_is_mapping": False,
        },
    }


def _snapshot(row: CanonicalBacklogAssessment) -> dict[str, object]:
    return {
        "outcome": row.outcome,
        "candidate_canonical_id": row.candidate_canonical_id,
        "confidence_basis_points": row.confidence_basis_points,
        "evidence_json": row.evidence_json,
        "engine_version": row.engine_version,
        "assessment_method": row.assessment_method,
        "review_status": row.review_status,
    }


def _json_object(value: str) -> dict[str, object]:
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
