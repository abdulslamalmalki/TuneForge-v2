"""Reviewer-controlled guidance claims grounded in accepted evidence passages."""

import json
from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import config
from app.auth.context import current_actor
from app.db.models import (
    CanonicalSetting,
    CpuModel,
    EvidencePassage,
    EvidenceSource,
    GuidanceClaim,
    GuidanceClaimEvent,
    GuidanceClaimEvidence,
    GuidanceClaimSetting,
)
from app.domain.errors import GuidanceClaimError
from app.domain.extraction_runs import utc_now_iso

ACTOR = "local-reviewer"
STATUS_DRAFT = "draft"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUSES = (STATUS_DRAFT, STATUS_APPROVED, STATUS_REJECTED)
CLAIM_TYPES = ("definition", "behavior", "constraint", "safety")
APPLICABILITY_SOURCE_SCOPE = "source_scope"
APPLICABILITY_CPU_MODEL = "cpu_model"


@dataclass(frozen=True)
class GuidanceCitation:
    passage: EvidencePassage
    source: EvidenceSource


@dataclass(frozen=True)
class GuidanceClaimOverview:
    claim: GuidanceClaim
    evidence_count: int
    setting_count: int


@dataclass(frozen=True)
class GuidanceClaimDetail:
    claim: GuidanceClaim
    evidence: tuple[GuidanceCitation, ...]
    settings: tuple[CanonicalSetting, ...]
    cpu_model: CpuModel | None
    events: tuple[GuidanceClaimEvent, ...]


def create_claim(
    session: Session,
    *,
    claim_text: str,
    claim_type: str,
    passage_ids: tuple[int, ...] | list[int],
    canonical_ids: tuple[str, ...] | list[str],
    applicability_scope: str = APPLICABILITY_SOURCE_SCOPE,
    cpu_model_id: str | None = None,
    actor: str | None = None,
) -> GuidanceClaim:
    actor = actor or current_actor()
    values = _validate_claim_values(
        session,
        claim_text=claim_text,
        claim_type=claim_type,
        passage_ids=passage_ids,
        canonical_ids=canonical_ids,
        applicability_scope=applicability_scope,
        cpu_model_id=cpu_model_id,
    )
    duplicate = session.scalar(
        select(GuidanceClaim.id).where(
            func.lower(GuidanceClaim.claim_text) == values["claim_text"].lower()
        )
    )
    if duplicate is not None:
        raise GuidanceClaimError(
            f"An equivalent guidance claim already exists as claim {duplicate}."
        )
    now = utc_now_iso()
    claim = GuidanceClaim(
        claim_text=values["claim_text"],
        claim_type=values["claim_type"],
        applicability_scope=values["applicability_scope"],
        cpu_model_id=values["cpu_model_id"],
        status=STATUS_DRAFT,
        review_note=None,
        reviewer=None,
        reviewed_at=None,
        created_at=now,
        updated_at=now,
    )
    session.add(claim)
    session.flush()
    _replace_links(
        session,
        claim.id,
        passage_ids=values["passage_ids"],
        canonical_ids=values["canonical_ids"],
    )
    session.add(
        GuidanceClaimEvent(
            claim_id=claim.id,
            action="candidate_created",
            actor=actor,
            detail=json.dumps(_claim_snapshot(claim, values), sort_keys=True),
            created_at=now,
        )
    )
    session.commit()
    session.refresh(claim)
    return claim


def list_claims(session: Session, *, status: str = "") -> list[GuidanceClaimOverview]:
    status = status.strip().lower()
    if status and status not in STATUSES:
        raise GuidanceClaimError("Status filter must be draft, approved, or rejected.")
    evidence_counts = (
        select(GuidanceClaimEvidence.claim_id, func.count().label("evidence_count"))
        .group_by(GuidanceClaimEvidence.claim_id)
        .subquery()
    )
    setting_counts = (
        select(GuidanceClaimSetting.claim_id, func.count().label("setting_count"))
        .group_by(GuidanceClaimSetting.claim_id)
        .subquery()
    )
    query = (
        select(
            GuidanceClaim,
            func.coalesce(evidence_counts.c.evidence_count, 0),
            func.coalesce(setting_counts.c.setting_count, 0),
        )
        .outerjoin(evidence_counts, evidence_counts.c.claim_id == GuidanceClaim.id)
        .outerjoin(setting_counts, setting_counts.c.claim_id == GuidanceClaim.id)
        .order_by(GuidanceClaim.id)
    )
    if status:
        query = query.where(GuidanceClaim.status == status)
    return [
        GuidanceClaimOverview(
            claim=claim,
            evidence_count=evidence_count,
            setting_count=setting_count,
        )
        for claim, evidence_count, setting_count in session.execute(query)
    ]


def status_counts(session: Session) -> dict[str, int]:
    counts = dict(
        session.execute(
            select(GuidanceClaim.status, func.count()).group_by(GuidanceClaim.status)
        ).all()
    )
    return {status: counts.get(status, 0) for status in STATUSES}


def get_claim(session: Session, claim_id: int) -> GuidanceClaim | None:
    return session.get(GuidanceClaim, claim_id)


def get_detail(session: Session, claim_id: int) -> GuidanceClaimDetail | None:
    claim = session.get(GuidanceClaim, claim_id)
    if claim is None:
        return None
    evidence = tuple(
        GuidanceCitation(passage=passage, source=source)
        for passage, source in session.execute(
            select(EvidencePassage, EvidenceSource)
            .join(
                GuidanceClaimEvidence,
                GuidanceClaimEvidence.passage_id == EvidencePassage.id,
            )
            .join(EvidenceSource, EvidenceSource.id == EvidencePassage.source_id)
            .where(GuidanceClaimEvidence.claim_id == claim_id)
            .order_by(EvidencePassage.page_number, EvidencePassage.ordinal)
        )
    )
    settings = tuple(
        session.scalars(
            select(CanonicalSetting)
            .join(
                GuidanceClaimSetting,
                GuidanceClaimSetting.canonical_id == CanonicalSetting.canonical_id,
            )
            .where(GuidanceClaimSetting.claim_id == claim_id)
            .order_by(CanonicalSetting.canonical_name)
        )
    )
    cpu_model = session.get(CpuModel, claim.cpu_model_id) if claim.cpu_model_id else None
    events = tuple(
        session.scalars(
            select(GuidanceClaimEvent)
            .where(GuidanceClaimEvent.claim_id == claim_id)
            .order_by(GuidanceClaimEvent.id)
        )
    )
    return GuidanceClaimDetail(
        claim=claim,
        evidence=evidence,
        settings=settings,
        cpu_model=cpu_model,
        events=events,
    )


def edit_claim(
    session: Session,
    claim: GuidanceClaim,
    *,
    claim_text: str,
    claim_type: str,
    passage_ids: tuple[int, ...] | list[int],
    canonical_ids: tuple[str, ...] | list[str],
    applicability_scope: str = APPLICABILITY_SOURCE_SCOPE,
    cpu_model_id: str | None = None,
) -> bool:
    if claim.status != STATUS_DRAFT:
        raise GuidanceClaimError("Only draft guidance claims can be edited.")
    values = _validate_claim_values(
        session,
        claim_text=claim_text,
        claim_type=claim_type,
        passage_ids=passage_ids,
        canonical_ids=canonical_ids,
        applicability_scope=applicability_scope,
        cpu_model_id=cpu_model_id,
    )
    detail = get_detail(session, claim.id)
    if detail is None:
        raise RuntimeError(f"Guidance claim {claim.id} could not be reloaded.")
    before = _detail_snapshot(detail)
    after = {
        "claim_text": values["claim_text"],
        "claim_type": values["claim_type"],
        "applicability_scope": values["applicability_scope"],
        "cpu_model_id": values["cpu_model_id"],
        "passage_ids": list(values["passage_ids"]),
        "canonical_ids": list(values["canonical_ids"]),
    }
    if before == after:
        return False
    duplicate = session.scalar(
        select(GuidanceClaim.id).where(
            GuidanceClaim.id != claim.id,
            func.lower(GuidanceClaim.claim_text) == values["claim_text"].lower(),
        )
    )
    if duplicate is not None:
        raise GuidanceClaimError(
            f"An equivalent guidance claim already exists as claim {duplicate}."
        )
    now = utc_now_iso()
    claim.claim_text = values["claim_text"]
    claim.claim_type = values["claim_type"]
    claim.applicability_scope = values["applicability_scope"]
    claim.cpu_model_id = values["cpu_model_id"]
    claim.updated_at = now
    _replace_links(
        session,
        claim.id,
        passage_ids=values["passage_ids"],
        canonical_ids=values["canonical_ids"],
    )
    session.add(
        GuidanceClaimEvent(
            claim_id=claim.id,
            action="candidate_edited",
            actor=current_actor(),
            detail=json.dumps({"before": before, "after": after}, sort_keys=True),
            created_at=now,
        )
    )
    session.commit()
    return True


def review_claim(
    session: Session,
    claim: GuidanceClaim,
    *,
    decision: str,
    note: str,
) -> GuidanceClaim:
    if claim.status != STATUS_DRAFT:
        raise GuidanceClaimError("Only draft guidance claims can be reviewed.")
    decision = decision.strip().lower()
    if decision not in {STATUS_APPROVED, STATUS_REJECTED}:
        raise GuidanceClaimError("Decision must be approved or rejected.")
    note = " ".join(note.split())
    if not note:
        raise GuidanceClaimError("Record a reviewer note for this decision.")
    if len(note) > config.MAX_GUIDANCE_REVIEW_NOTE_LENGTH:
        raise GuidanceClaimError(
            f"Reviewer note must be {config.MAX_GUIDANCE_REVIEW_NOTE_LENGTH} characters or fewer."
        )
    detail = get_detail(session, claim.id)
    if detail is None:
        raise RuntimeError(f"Guidance claim {claim.id} could not be reloaded.")
    if decision == STATUS_APPROVED:
        if not detail.evidence:
            raise GuidanceClaimError("An approved claim must have supporting evidence.")
        if not detail.settings:
            raise GuidanceClaimError("An approved claim must link to a canonical setting.")
        if any(citation.source.status != "approved" for citation in detail.evidence):
            raise GuidanceClaimError("All supporting evidence sources must be approved.")
        if claim.applicability_scope != APPLICABILITY_SOURCE_SCOPE:
            raise GuidanceClaimError(
                "CPU-model-specific guidance is not enabled in the 0.20 pilot."
            )
    now = utc_now_iso()
    claim.status = decision
    claim.review_note = note
    actor = current_actor()
    claim.reviewer = actor
    claim.reviewed_at = now
    claim.updated_at = now
    session.add(
        GuidanceClaimEvent(
            claim_id=claim.id,
            action=f"claim_{decision}",
            actor=actor,
            detail=json.dumps({"note": note}, sort_keys=True),
            created_at=now,
        )
    )
    session.commit()
    session.refresh(claim)
    return claim


def _validate_claim_values(
    session: Session,
    *,
    claim_text: str,
    claim_type: str,
    passage_ids: tuple[int, ...] | list[int],
    canonical_ids: tuple[str, ...] | list[str],
    applicability_scope: str,
    cpu_model_id: str | None,
) -> dict[str, object]:
    claim_text = " ".join(claim_text.split())
    if not claim_text:
        raise GuidanceClaimError("Claim text is required.")
    if len(claim_text) > config.MAX_GUIDANCE_CLAIM_CHARS:
        raise GuidanceClaimError(
            f"Claim text must be {config.MAX_GUIDANCE_CLAIM_CHARS} characters or fewer."
        )
    claim_type = claim_type.strip().lower()
    if claim_type not in CLAIM_TYPES:
        raise GuidanceClaimError("Claim type must be definition, behavior, constraint, or safety.")
    applicability_scope = applicability_scope.strip().lower()
    cpu_model_id = (cpu_model_id or "").strip() or None
    if applicability_scope == APPLICABILITY_CPU_MODEL:
        if cpu_model_id is None or session.get(CpuModel, cpu_model_id) is None:
            raise GuidanceClaimError("Choose a known CPU model for CPU-model applicability.")
        raise GuidanceClaimError("CPU-model-specific guidance is not enabled in the 0.20 pilot.")
    if applicability_scope != APPLICABILITY_SOURCE_SCOPE or cpu_model_id is not None:
        raise GuidanceClaimError(
            "The 0.20 pilot accepts source-scope claims without inferred CPU applicability."
        )
    clean_passage_ids = tuple(sorted(dict.fromkeys(int(value) for value in passage_ids)))
    if not clean_passage_ids:
        raise GuidanceClaimError("Select at least one supporting evidence passage.")
    evidence_rows = session.execute(
        select(EvidencePassage.id, EvidenceSource.status)
        .join(EvidenceSource, EvidenceSource.id == EvidencePassage.source_id)
        .where(EvidencePassage.id.in_(clean_passage_ids))
    ).all()
    if len(evidence_rows) != len(clean_passage_ids):
        raise GuidanceClaimError("One or more evidence passages were not found.")
    if any(source_status != "approved" for _, source_status in evidence_rows):
        raise GuidanceClaimError("Guidance claims require approved evidence sources.")
    clean_canonical_ids = tuple(
        sorted(dict.fromkeys(value.strip() for value in canonical_ids if value.strip()))
    )
    if not clean_canonical_ids:
        raise GuidanceClaimError("Link the claim to at least one canonical setting.")
    found_canonical_ids = set(
        session.scalars(
            select(CanonicalSetting.canonical_id).where(
                CanonicalSetting.canonical_id.in_(clean_canonical_ids)
            )
        )
    )
    if found_canonical_ids != set(clean_canonical_ids):
        raise GuidanceClaimError("One or more canonical settings were not found.")
    return {
        "claim_text": claim_text,
        "claim_type": claim_type,
        "applicability_scope": applicability_scope,
        "cpu_model_id": cpu_model_id,
        "passage_ids": clean_passage_ids,
        "canonical_ids": clean_canonical_ids,
    }


def _replace_links(
    session: Session,
    claim_id: int,
    *,
    passage_ids: tuple[int, ...],
    canonical_ids: tuple[str, ...],
) -> None:
    session.execute(delete(GuidanceClaimEvidence).where(GuidanceClaimEvidence.claim_id == claim_id))
    session.execute(delete(GuidanceClaimSetting).where(GuidanceClaimSetting.claim_id == claim_id))
    session.add_all(
        GuidanceClaimEvidence(claim_id=claim_id, passage_id=value, relation="supports")
        for value in passage_ids
    )
    session.add_all(
        GuidanceClaimSetting(claim_id=claim_id, canonical_id=value) for value in canonical_ids
    )


def _claim_snapshot(claim: GuidanceClaim, values: dict[str, object]) -> dict[str, object]:
    return {
        "claim_text": claim.claim_text,
        "claim_type": claim.claim_type,
        "applicability_scope": claim.applicability_scope,
        "cpu_model_id": claim.cpu_model_id,
        "passage_ids": list(values["passage_ids"]),
        "canonical_ids": list(values["canonical_ids"]),
    }


def _detail_snapshot(detail: GuidanceClaimDetail) -> dict[str, object]:
    return {
        "claim_text": detail.claim.claim_text,
        "claim_type": detail.claim.claim_type,
        "applicability_scope": detail.claim.applicability_scope,
        "cpu_model_id": detail.claim.cpu_model_id,
        "passage_ids": [citation.passage.id for citation in detail.evidence],
        "canonical_ids": [setting.canonical_id for setting in detail.settings],
    }
