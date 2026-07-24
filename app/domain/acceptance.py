"""Extraction acceptance metrics and curator-recorded missed settings.

Precision is derived from completed review decisions: approved candidates are
true positives and rejected candidates are false positives. Recall is an
estimate based on approved candidates plus active missed-setting records. It is
only considered final after the curator explicitly certifies that the manual was
inspected for omissions and every extracted candidate has been reviewed.
"""

import json
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import config
from app.auth.context import current_actor
from app.db.models import (
    ExtractionRun,
    Manual,
    ManualEvent,
    MissedSetting,
    ReviewEvent,
    Setting,
)
from app.domain.errors import AcceptanceError
from app.domain.extraction_runs import utc_now_iso

ACTOR = "local-reviewer"

STATUS_ACTIVE = "active"
STATUS_DISMISSED = "dismissed"
PAGE_PENDING = "pending"
PAGE_CORRECT = "correct"
PAGE_CORRECTED = "corrected"
ACTION_PAGE_REFERENCE_REVIEWED = "page_reference_reviewed"
ACTION_ACCEPTANCE_CERTIFIED = "acceptance_certified"
ACTION_ACCEPTANCE_INVALIDATED = "acceptance_invalidated"


@dataclass(frozen=True)
class AcceptanceReport:
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


@dataclass(frozen=True)
class PageReferenceRow:
    setting_id: int
    name: str
    extracted_page_number: int
    effective_page_number: int
    review_status: str


def build_report(session: Session, manual: Manual) -> AcceptanceReport:
    # Local import avoids a domain-module cycle: manual provenance changes also
    # call the acceptance invalidation helper in this module.
    from app.domain.manuals import missing_provenance_fields

    settings = list(
        session.scalars(select(Setting).where(Setting.manual_id == manual.id).order_by(Setting.id))
    )
    run = session.scalar(select(ExtractionRun).where(ExtractionRun.manual_id == manual.id))
    draft_count = sum(setting.status == "draft" for setting in settings)
    approved = [setting for setting in settings if setting.status == "approved"]
    rejected_count = sum(setting.status == "rejected" for setting in settings)
    reviewed_count = len(approved) + rejected_count
    corrected_count = sum(
        setting.edited_name is not None or setting.edited_description is not None
        for setting in approved
    )
    missed_count = session.scalar(
        select(func.count())
        .select_from(MissedSetting)
        .where(MissedSetting.manual_id == manual.id, MissedSetting.status == STATUS_ACTIVE)
    )
    total = len(settings)
    page_reviewed = [setting for setting in approved if setting.page_review_status != PAGE_PENDING]
    page_correct_count = sum(setting.page_review_status == PAGE_CORRECT for setting in approved)
    page_corrected_count = sum(setting.page_review_status == PAGE_CORRECTED for setting in approved)
    page_pending_count = len(approved) - len(page_reviewed)
    review_completion = reviewed_count / total if total else 1.0
    precision = len(approved) / reviewed_count if reviewed_count else None
    recall_denominator = len(approved) + missed_count
    estimated_recall = len(approved) / recall_denominator if recall_denominator else None
    provenance_complete = not missing_provenance_fields(manual)
    ground_truth_reviewed = manual.acceptance_reviewed_at is not None
    return AcceptanceReport(
        manual_id=manual.id,
        run_status=run.status if run else None,
        parser_version=run.parser_version if run else None,
        total_candidates=total,
        draft_count=draft_count,
        approved_count=len(approved),
        rejected_count=rejected_count,
        reviewed_count=reviewed_count,
        corrected_count=corrected_count,
        empty_description_count=sum(
            not setting.extracted_description.strip() for setting in settings
        ),
        page_reviewed_count=len(page_reviewed),
        page_pending_count=page_pending_count,
        page_correct_count=page_correct_count,
        page_corrected_count=page_corrected_count,
        page_accuracy=(page_correct_count / len(page_reviewed) if page_reviewed else None),
        missed_count=missed_count,
        review_completion=review_completion,
        precision=precision,
        estimated_recall=estimated_recall,
        provenance_complete=provenance_complete,
        ground_truth_reviewed=ground_truth_reviewed,
        acceptance_complete=(
            run is not None
            and run.status == "succeeded"
            and draft_count == 0
            and page_pending_count == 0
            and provenance_complete
            and ground_truth_reviewed
        ),
    )


def list_page_references(session: Session, manual_id: int) -> list[PageReferenceRow]:
    """Approved candidates whose extracted source page must be checked."""
    settings = session.scalars(
        select(Setting)
        .where(Setting.manual_id == manual_id, Setting.status == "approved")
        .order_by(Setting.page_number, Setting.id)
    )
    return [
        PageReferenceRow(
            setting_id=setting.id,
            name=setting.edited_name or setting.extracted_name,
            extracted_page_number=setting.page_number,
            effective_page_number=setting.edited_page_number or setting.page_number,
            review_status=setting.page_review_status,
        )
        for setting in settings
    ]


def review_page_reference(
    session: Session, manual: Manual, setting_id: int, *, page_number: int
) -> bool:
    """Verify or correct one approved candidate's extracted PDF page."""
    setting = session.scalar(
        select(Setting).where(Setting.id == setting_id, Setting.manual_id == manual.id)
    )
    if setting is None:
        raise AcceptanceError("Setting not found for this manual.")
    if setting.status != "approved":
        raise AcceptanceError("Only approved settings need page-reference review.")
    if page_number < 1 or page_number > manual.page_count:
        raise AcceptanceError(
            f"Page number must be between 1 and the manual's {manual.page_count} pages."
        )

    new_status = PAGE_CORRECT if page_number == setting.page_number else PAGE_CORRECTED
    new_override = None if new_status == PAGE_CORRECT else page_number
    if setting.page_review_status == new_status and setting.edited_page_number == new_override:
        return False

    before = {
        "status": setting.page_review_status,
        "effective_page_number": setting.edited_page_number or setting.page_number,
    }
    setting.page_review_status = new_status
    setting.edited_page_number = new_override
    setting.updated_at = utc_now_iso()
    invalidate_certification(
        session,
        manual,
        reason="page_reference_changed",
    )
    session.add(
        ReviewEvent(
            setting_id=setting.id,
            action=ACTION_PAGE_REFERENCE_REVIEWED,
            actor=current_actor(),
            detail=json.dumps(
                {
                    "before": before,
                    "after": {
                        "status": new_status,
                        "effective_page_number": page_number,
                    },
                },
                sort_keys=True,
            ),
            created_at=setting.updated_at,
        )
    )
    session.commit()
    return True


def list_missed_settings(
    session: Session, manual_id: int, *, include_dismissed: bool = False
) -> list[MissedSetting]:
    query = select(MissedSetting).where(MissedSetting.manual_id == manual_id)
    if not include_dismissed:
        query = query.where(MissedSetting.status == STATUS_ACTIVE)
    query = query.order_by(MissedSetting.page_number, MissedSetting.id)
    return list(session.scalars(query))


def add_missed_setting(
    session: Session,
    manual: Manual,
    *,
    name: str,
    page_number: int,
    description: str = "",
    note: str = "",
) -> MissedSetting:
    name = " ".join(name.split())
    if not name:
        raise AcceptanceError("Missed setting name is required.")
    if len(name) > config.MAX_MISSED_SETTING_NAME_LENGTH:
        raise AcceptanceError(
            "Missed setting name must be "
            f"{config.MAX_MISSED_SETTING_NAME_LENGTH} characters or fewer."
        )
    if page_number < 1 or page_number > manual.page_count:
        raise AcceptanceError(
            f"Page number must be between 1 and the manual's {manual.page_count} pages."
        )
    description = description.strip()
    note = note.strip()
    _check_note_length("Description", description)
    _check_note_length("Note", note)
    duplicate = session.scalar(
        select(MissedSetting.id).where(
            MissedSetting.manual_id == manual.id,
            MissedSetting.status == STATUS_ACTIVE,
            MissedSetting.page_number == page_number,
            func.lower(MissedSetting.name) == name.lower(),
        )
    )
    if duplicate is not None:
        raise AcceptanceError("This missed setting is already recorded on that page.")
    missed = MissedSetting(
        manual_id=manual.id,
        name=name,
        page_number=page_number,
        description=description or None,
        note=note or None,
        status=STATUS_ACTIVE,
        actor=current_actor(),
        created_at=utc_now_iso(),
    )
    session.add(missed)
    invalidate_certification(
        session,
        manual,
        reason="missed_setting_added",
    )
    session.commit()
    return missed


def dismiss_missed_setting(
    session: Session, manual: Manual, missed_setting_id: int, *, reason: str
) -> MissedSetting:
    missed = session.scalar(
        select(MissedSetting).where(
            MissedSetting.id == missed_setting_id,
            MissedSetting.manual_id == manual.id,
        )
    )
    if missed is None:
        raise AcceptanceError("Missed setting not found for this manual.")
    if missed.status != STATUS_ACTIVE:
        raise AcceptanceError("This missed setting has already been dismissed.")
    reason = reason.strip()
    if not reason:
        raise AcceptanceError("A dismissal reason is required.")
    _check_note_length("Dismissal reason", reason)
    missed.status = STATUS_DISMISSED
    missed.dismissed_at = utc_now_iso()
    missed.dismissal_reason = reason
    invalidate_certification(
        session,
        manual,
        reason="missed_setting_dismissed",
    )
    session.commit()
    return missed


def certify_ground_truth(session: Session, manual: Manual, *, notes: str = "") -> None:
    from app.domain.manuals import missing_provenance_fields

    report = build_report(session, manual)
    if report.run_status != "succeeded":
        raise AcceptanceError("Only a successfully extracted manual can be certified.")
    if report.draft_count:
        raise AcceptanceError(
            f"Review all extracted candidates first; {report.draft_count} drafts remain."
        )
    if report.page_pending_count:
        raise AcceptanceError(
            "Review every approved setting's page reference first; "
            f"{report.page_pending_count} remain."
        )
    missing = missing_provenance_fields(manual)
    if missing:
        raise AcceptanceError(
            "Complete the manual provenance first. Missing: " + ", ".join(missing) + "."
        )
    notes = notes.strip()
    _check_note_length("Acceptance notes", notes)
    manual.acceptance_reviewed_at = utc_now_iso()
    actor = current_actor()
    manual.acceptance_reviewer = actor
    manual.acceptance_notes = notes or None
    session.add(
        ManualEvent(
            manual_id=manual.id,
            action=ACTION_ACCEPTANCE_CERTIFIED,
            actor=actor,
            detail=json.dumps(
                {
                    "notes": notes or None,
                    "report": _acceptance_snapshot(report),
                },
                sort_keys=True,
            ),
            created_at=manual.acceptance_reviewed_at,
        )
    )
    session.commit()


def invalidate_certification(
    session: Session,
    manual: Manual,
    *,
    reason: str,
    actor: str | None = None,
) -> bool:
    """Clear a live certification and preserve the previous decision forever.

    Returns False when the manual is not certified, avoiding noisy audit rows
    while ordinary pre-certification review is still in progress. The caller
    owns the transaction so the invalidation event commits with the change that
    caused it.
    """
    if manual.acceptance_reviewed_at is None:
        return False
    before = {
        "reviewed_at": manual.acceptance_reviewed_at,
        "reviewer": manual.acceptance_reviewer,
        "notes": manual.acceptance_notes,
    }
    manual.acceptance_reviewed_at = None
    manual.acceptance_reviewer = None
    manual.acceptance_notes = None
    session.add(
        ManualEvent(
            manual_id=manual.id,
            action=ACTION_ACCEPTANCE_INVALIDATED,
            actor=actor or current_actor(),
            detail=json.dumps(
                {"before": before, "reason": reason},
                sort_keys=True,
            ),
            created_at=utc_now_iso(),
        )
    )
    return True


def _acceptance_snapshot(report: AcceptanceReport) -> dict[str, object]:
    return {
        "run_status": report.run_status,
        "parser_version": report.parser_version,
        "total_candidates": report.total_candidates,
        "approved_count": report.approved_count,
        "rejected_count": report.rejected_count,
        "page_reviewed_count": report.page_reviewed_count,
        "missed_count": report.missed_count,
        "precision": report.precision,
        "estimated_recall": report.estimated_recall,
        "provenance_complete": report.provenance_complete,
    }


def _check_note_length(label: str, value: str) -> None:
    if len(value) > config.MAX_ACCEPTANCE_NOTE_LENGTH:
        raise AcceptanceError(
            f"{label} must be {config.MAX_ACCEPTANCE_NOTE_LENGTH} characters or fewer."
        )
