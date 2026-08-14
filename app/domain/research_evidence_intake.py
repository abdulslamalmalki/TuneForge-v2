"""Human-controlled promotion boundary for official live-research results."""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import config
from app.auth.context import current_actor
from app.db.models import (
    CanonicalSetting,
    EvidencePassage,
    EvidenceSource,
    GuidanceClaim,
    GuidanceClaimEvent,
    GuidanceClaimEvidence,
    GuidanceClaimSetting,
    Manual,
    ResearchEvidenceIntake,
    ResearchEvidenceIntakeEvent,
    Setting,
    SettingCanonicalMapping,
    TrustedWebResearchResult,
    TrustedWebResearchRun,
)
from app.domain import guidance_claims, hardware
from app.domain.extraction_runs import utc_now_iso
from app.research.trusted_web import TrustedWebResearchError

TRIAGE_STATUSES = {
    "evidence_intake",
    "needs_corroboration",
    "low_quality",
    "rejected",
    "archived",
}
ALL_STATUSES = TRIAGE_STATUSES | {"evidence_linked", "claim_drafted"}
TRUST_TIER = "official_research_lead_unreviewed"


@dataclass(frozen=True)
class ResearchQueueRow:
    run: TrustedWebResearchRun
    result: TrustedWebResearchResult
    intake: ResearchEvidenceIntake | None


@dataclass(frozen=True)
class ResearchIntakeDetail:
    run: TrustedWebResearchRun
    result: TrustedWebResearchResult
    intake: ResearchEvidenceIntake | None
    setting: Setting | None
    canonical: CanonicalSetting | None
    similar_sources: tuple[EvidenceSource, ...]
    related_claims: tuple[GuidanceClaim, ...]
    events: tuple[ResearchEvidenceIntakeEvent, ...]
    hardware_scope: dict[str, object]
    bios_scope: dict[str, object]
    possible_conflict: str | None


@dataclass(frozen=True)
class ReviewedEvidenceOption:
    source: EvidenceSource
    passage: EvidencePassage


def status_counts(session: Session) -> dict[str, int]:
    counts = {"new": 0, **dict.fromkeys(sorted(ALL_STATUSES), 0)}
    counts["new"] = int(
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
    counts.update(
        {
            status: count
            for status, count in session.execute(
                select(ResearchEvidenceIntake.status, func.count())
                .group_by(ResearchEvidenceIntake.status)
                .order_by(ResearchEvidenceIntake.status)
            )
        }
    )
    return counts


def reviewed_evidence_options(
    session: Session, publisher_id: str
) -> tuple[ReviewedEvidenceOption, ...]:
    return tuple(
        ReviewedEvidenceOption(source, passage)
        for source, passage in session.execute(
            select(EvidenceSource, EvidencePassage)
            .join(EvidencePassage, EvidencePassage.source_id == EvidenceSource.id)
            .where(
                EvidenceSource.status == "approved",
                EvidenceSource.publisher_id == publisher_id,
            )
            .order_by(EvidenceSource.title, EvidencePassage.page_number, EvidencePassage.id)
        )
    )


def list_results(
    session: Session,
    *,
    status: str = "",
    publisher_id: str = "",
) -> tuple[ResearchQueueRow, ...]:
    if status and status != "new" and status not in ALL_STATUSES:
        raise TrustedWebResearchError("Unknown research intake status.")
    statement = (
        select(TrustedWebResearchRun, TrustedWebResearchResult, ResearchEvidenceIntake)
        .join(
            TrustedWebResearchResult,
            TrustedWebResearchResult.run_id == TrustedWebResearchRun.id,
        )
        .outerjoin(
            ResearchEvidenceIntake,
            ResearchEvidenceIntake.result_id == TrustedWebResearchResult.id,
        )
        .order_by(TrustedWebResearchRun.created_at.desc(), TrustedWebResearchResult.ordinal)
    )
    if status == "new":
        statement = statement.where(ResearchEvidenceIntake.result_id.is_(None))
    elif status:
        statement = statement.where(ResearchEvidenceIntake.status == status)
    if publisher_id:
        statement = statement.where(
            TrustedWebResearchResult.publisher_id == publisher_id.strip().casefold()
        )
    return tuple(ResearchQueueRow(*row) for row in session.execute(statement))


def get_detail(session: Session, result_id: int) -> ResearchIntakeDetail | None:
    row = session.execute(
        select(TrustedWebResearchRun, TrustedWebResearchResult, ResearchEvidenceIntake)
        .join(
            TrustedWebResearchResult,
            TrustedWebResearchResult.run_id == TrustedWebResearchRun.id,
        )
        .outerjoin(
            ResearchEvidenceIntake,
            ResearchEvidenceIntake.result_id == TrustedWebResearchResult.id,
        )
        .where(TrustedWebResearchResult.id == result_id)
    ).first()
    if row is None:
        return None
    run, result, intake = row
    setting = session.get(Setting, intake.setting_id) if intake and intake.setting_id else None
    canonical = (
        session.get(CanonicalSetting, intake.canonical_id)
        if intake and intake.canonical_id
        else None
    )
    related_claims = (
        tuple(
            session.scalars(
                select(GuidanceClaim)
                .join(GuidanceClaimSetting, GuidanceClaimSetting.claim_id == GuidanceClaim.id)
                .where(GuidanceClaimSetting.canonical_id == canonical.canonical_id)
                .order_by(GuidanceClaim.status, GuidanceClaim.id)
            )
        )
        if canonical
        else ()
    )
    return ResearchIntakeDetail(
        run=run,
        result=result,
        intake=intake,
        setting=setting,
        canonical=canonical,
        similar_sources=tuple(
            session.scalars(
                select(EvidenceSource)
                .where(EvidenceSource.publisher_id == result.publisher_id)
                .order_by(EvidenceSource.status, EvidenceSource.title)
            )
        ),
        related_claims=related_claims,
        events=(
            tuple(
                session.scalars(
                    select(ResearchEvidenceIntakeEvent)
                    .where(ResearchEvidenceIntakeEvent.result_id == result.id)
                    .order_by(ResearchEvidenceIntakeEvent.id)
                )
            )
            if intake
            else ()
        ),
        hardware_scope=_json_object(intake.hardware_scope_json) if intake else {},
        bios_scope=_json_object(intake.bios_scope_json) if intake else {},
        possible_conflict=_possible_conflict(
            intake.proposed_claim_text if intake else None,
            related_claims,
        ),
    )


def triage_result(
    session: Session,
    result_id: int,
    *,
    status: str,
    note: str,
    setting_id: int | None = None,
    canonical_id: str = "",
    proposed_claim_text: str = "",
    proposed_claim_type: str = "",
) -> ResearchEvidenceIntake:
    if status not in TRIAGE_STATUSES:
        raise TrustedWebResearchError("Choose a supported research triage state.")
    result = session.get(TrustedWebResearchResult, result_id)
    if result is None:
        raise TrustedWebResearchError("Trusted-web research result not found.")
    note = _note(note)
    setting, canonical = _validate_context(
        session, setting_id=setting_id, canonical_id=canonical_id
    )
    claim_text, claim_type = _validate_proposed_claim(proposed_claim_text, proposed_claim_type)
    hardware_scope = _hardware_scope(session, setting)
    bios_scope = {"status": "not_explicitly_reviewed"}
    now = utc_now_iso()
    row = session.get(ResearchEvidenceIntake, result_id)
    if row is not None and row.status in {"evidence_linked", "claim_drafted"}:
        raise TrustedWebResearchError(
            "This result has crossed the reviewed-evidence boundary and can no longer "
            "be retriaged. Continue in Evidence Sources or Guidance Claims."
        )
    before = _snapshot(row)
    values = {
        "status": status,
        "setting_id": setting.id if setting else None,
        "canonical_id": canonical.canonical_id if canonical else None,
        "hardware_scope_json": json.dumps(hardware_scope, sort_keys=True),
        "bios_scope_json": json.dumps(bios_scope, sort_keys=True),
        "proposed_claim_text": claim_text,
        "proposed_claim_type": claim_type,
        "reviewer": current_actor(),
        "review_note": note,
        "reviewed_at": now,
        "updated_at": now,
    }
    if row is None:
        row = ResearchEvidenceIntake(
            result_id=result_id,
            **values,
            evidence_source_id=None,
            evidence_passage_id=None,
            draft_claim_id=None,
            created_at=now,
        )
        session.add(row)
        session.flush()
    else:
        for key, value in values.items():
            setattr(row, key, value)
    _event(
        session,
        row,
        "triaged",
        {"before": before, "after": _snapshot(row), "note": note},
    )
    session.commit()
    return row


def link_reviewed_evidence(
    session: Session,
    result_id: int,
    *,
    evidence_source_id: int,
    evidence_passage_id: int,
    note: str,
) -> ResearchEvidenceIntake:
    row = _intake(session, result_id)
    note = _note(note)
    if row.status not in {"evidence_intake", "needs_corroboration", "evidence_linked"}:
        raise TrustedWebResearchError(
            "Only an evidence-intake or corroboration result can link reviewed evidence."
        )
    source, passage = _validate_evidence(
        session,
        row,
        evidence_source_id=evidence_source_id,
        evidence_passage_id=evidence_passage_id,
    )
    before = _snapshot(row)
    now = utc_now_iso()
    row.status = "evidence_linked"
    row.evidence_source_id = source.id
    row.evidence_passage_id = passage.id
    row.reviewer = current_actor()
    row.review_note = note
    row.reviewed_at = now
    row.updated_at = now
    _event(
        session,
        row,
        "reviewed_evidence_linked",
        {
            "before": before,
            "evidence_source_id": source.id,
            "evidence_passage_id": passage.id,
            "note": note,
        },
    )
    session.commit()
    return row


def create_draft_claim(
    session: Session,
    result_id: int,
    *,
    note: str,
) -> GuidanceClaim:
    row = _intake(session, result_id)
    note = _note(note)
    if row.status != "evidence_linked":
        raise TrustedWebResearchError(
            "Link an approved Evidence Source passage before creating a claim."
        )
    if (
        row.canonical_id is None
        or row.evidence_passage_id is None
        or not row.proposed_claim_text
        or not row.proposed_claim_type
    ):
        raise TrustedWebResearchError(
            "Canonical setting, reviewed passage, and proposed atomic claim are required."
        )
    try:
        claim = guidance_claims.create_claim(
            session,
            claim_text=row.proposed_claim_text,
            claim_type=row.proposed_claim_type,
            passage_ids=(row.evidence_passage_id,),
            canonical_ids=(row.canonical_id,),
            actor=current_actor(),
            commit=False,
        )
        now = utc_now_iso()
        row.status = "claim_drafted"
        row.draft_claim_id = claim.id
        row.reviewer = current_actor()
        row.review_note = note
        row.reviewed_at = now
        row.updated_at = now
        _event(
            session,
            row,
            "draft_claim_created",
            {"claim_id": claim.id, "note": note, "public": False},
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return claim


def attach_to_draft_claim(
    session: Session,
    result_id: int,
    *,
    claim_id: int,
    note: str,
) -> GuidanceClaim:
    row = _intake(session, result_id)
    note = _note(note)
    if row.status != "evidence_linked" or row.evidence_passage_id is None:
        raise TrustedWebResearchError("Link reviewed evidence before attaching it to a claim.")
    claim = session.get(GuidanceClaim, claim_id)
    if claim is None or claim.status != "draft":
        raise TrustedWebResearchError("Evidence can be attached only to a draft claim.")
    canonical_ids = set(
        session.scalars(
            select(GuidanceClaimSetting.canonical_id).where(
                GuidanceClaimSetting.claim_id == claim.id
            )
        )
    )
    if row.canonical_id not in canonical_ids:
        raise TrustedWebResearchError(
            "The draft claim does not reference this intake's canonical setting."
        )
    existing = session.get(GuidanceClaimEvidence, (claim.id, row.evidence_passage_id))
    if existing is None:
        session.add(
            GuidanceClaimEvidence(
                claim_id=claim.id,
                passage_id=row.evidence_passage_id,
                relation="supports",
            )
        )
    now = utc_now_iso()
    session.add(
        GuidanceClaimEvent(
            claim_id=claim.id,
            action="evidence_attached_from_research",
            actor=current_actor(),
            detail=json.dumps(
                {
                    "research_result_id": row.result_id,
                    "passage_id": row.evidence_passage_id,
                    "note": note,
                },
                sort_keys=True,
            ),
            created_at=now,
        )
    )
    row.review_note = note
    row.reviewer = current_actor()
    row.reviewed_at = now
    row.updated_at = now
    row.status = "claim_drafted"
    row.draft_claim_id = claim.id
    _event(
        session,
        row,
        "attached_to_draft_claim",
        {"claim_id": claim.id, "note": note},
    )
    session.commit()
    return claim


def _validate_context(
    session: Session,
    *,
    setting_id: int | None,
    canonical_id: str,
) -> tuple[Setting | None, CanonicalSetting | None]:
    setting = session.get(Setting, setting_id) if setting_id else None
    if setting_id and (setting is None or setting.status != "approved"):
        raise TrustedWebResearchError("Relevant setting must be an approved source setting.")
    canonical_id = canonical_id.strip()
    mapping = session.get(SettingCanonicalMapping, setting.id) if setting else None
    if not canonical_id and mapping:
        canonical_id = mapping.canonical_id
    canonical = session.get(CanonicalSetting, canonical_id) if canonical_id else None
    if canonical_id and (canonical is None or canonical.status != "active"):
        raise TrustedWebResearchError("Relevant canonical setting is not active.")
    if mapping and canonical and mapping.canonical_id != canonical.canonical_id:
        raise TrustedWebResearchError(
            "Relevant setting is mapped to a different canonical identity."
        )
    return setting, canonical


def _validate_proposed_claim(text: str, claim_type: str) -> tuple[str | None, str | None]:
    text = " ".join(text.split())
    claim_type = claim_type.strip().casefold()
    if bool(text) != bool(claim_type):
        raise TrustedWebResearchError(
            "Proposed claim text and claim type must be supplied together."
        )
    if not text:
        return None, None
    if len(text) > config.MAX_GUIDANCE_CLAIM_CHARS:
        raise TrustedWebResearchError("Proposed claim is too long.")
    if claim_type not in guidance_claims.CLAIM_TYPES:
        raise TrustedWebResearchError("Choose a supported claim type.")
    return text, claim_type


def _validate_evidence(
    session: Session,
    intake: ResearchEvidenceIntake,
    *,
    evidence_source_id: int,
    evidence_passage_id: int,
) -> tuple[EvidenceSource, EvidencePassage]:
    source = session.get(EvidenceSource, evidence_source_id)
    passage = session.get(EvidencePassage, evidence_passage_id)
    result = session.get(TrustedWebResearchResult, intake.result_id)
    if source is None or source.status != "approved":
        raise TrustedWebResearchError("Choose an approved Evidence Source.")
    if passage is None or passage.source_id != source.id:
        raise TrustedWebResearchError("Evidence passage does not belong to that source.")
    if result is None or source.publisher_id != result.publisher_id:
        raise TrustedWebResearchError(
            "Reviewed Evidence Source publisher must match the research result publisher."
        )
    return source, passage


def _hardware_scope(session: Session, setting: Setting | None) -> dict[str, object]:
    if setting is None:
        return {"status": "not_linked", "targets": []}
    manual = session.get(Manual, setting.manual_id)
    view = hardware.get_manual_applicability(session, manual.id) if manual else None
    if view is None:
        return {"status": "not_reviewed", "targets": []}
    return {
        "status": view.model_status,
        "scope": view.scope,
        "targets": [
            {"type": target.target_type, "id": target.target_id, "name": target.canonical_name}
            for target in view.targets
        ],
    }


def _possible_conflict(
    proposed_text: str | None,
    claims: tuple[GuidanceClaim, ...],
) -> str | None:
    if not proposed_text:
        return None
    normalized = set(proposed_text.casefold().split())
    if len(normalized) < 3:
        return None
    negative = any(term in normalized for term in {"no", "not", "never", "cannot"})
    for claim in claims:
        claim_tokens = set(claim.claim_text.casefold().split())
        if not claim_tokens:
            continue
        overlap = len(normalized & claim_tokens) / len(normalized | claim_tokens)
        claim_negative = any(term in claim_tokens for term in {"no", "not", "never", "cannot"})
        if overlap >= 0.6 and negative != claim_negative:
            return f"Possible polarity conflict with guidance claim {claim.id}."
    return None


def _intake(session: Session, result_id: int) -> ResearchEvidenceIntake:
    row = session.get(ResearchEvidenceIntake, result_id)
    if row is None:
        raise TrustedWebResearchError("Promote or triage this result before continuing.")
    return row


def _note(value: str) -> str:
    value = " ".join(value.split())
    if not value:
        raise TrustedWebResearchError("A reviewer note is required.")
    if len(value) > config.MAX_GUIDANCE_REVIEW_NOTE_LENGTH:
        raise TrustedWebResearchError("Reviewer note is too long.")
    return value


def _snapshot(row: ResearchEvidenceIntake | None) -> dict[str, object] | None:
    if row is None:
        return None
    return {
        "status": row.status,
        "setting_id": row.setting_id,
        "canonical_id": row.canonical_id,
        "proposed_claim_text": row.proposed_claim_text,
        "proposed_claim_type": row.proposed_claim_type,
        "evidence_source_id": row.evidence_source_id,
        "evidence_passage_id": row.evidence_passage_id,
        "draft_claim_id": row.draft_claim_id,
    }


def _event(
    session: Session,
    row: ResearchEvidenceIntake,
    action: str,
    detail: dict[str, object],
) -> None:
    session.add(
        ResearchEvidenceIntakeEvent(
            result_id=row.result_id,
            action=action,
            actor=current_actor(),
            detail=json.dumps(detail, sort_keys=True),
            created_at=utc_now_iso(),
        )
    )


def _json_object(value: str) -> dict[str, object]:
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
