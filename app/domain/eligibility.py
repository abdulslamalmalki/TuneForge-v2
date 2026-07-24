"""Reviewer-gated manual eligibility analysis and audited decisions."""

from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from app.auth.context import current_actor
from app.db.models import ExtractionRun, Manual, ManualEvent
from app.db.session import SessionLocal
from app.domain import hardware as hardware_domain
from app.domain.errors import ManualEligibilityError
from app.domain.extraction_runs import utc_now_iso
from app.eligibility.classifier import (
    DOCUMENT_TYPES,
    TYPE_BIOS_GUIDE,
    TYPE_UNKNOWN,
    classify_document,
    read_bounded_sample,
)
from app.storage import files

logger = logging.getLogger(__name__)

ACTOR = "local-reviewer"
CLASSIFIER_ACTOR = "eligibility-classifier"
STATUS_PENDING = "pending"
STATUS_ELIGIBLE = "eligible"
STATUS_INELIGIBLE = "ineligible"
STATUS_LEGACY = "legacy"
DECISIONS = (STATUS_ELIGIBLE, STATUS_INELIGIBLE)

ACTION_CLASSIFIED = "eligibility_classified"
ACTION_CLASSIFICATION_FAILED = "eligibility_classification_failed"
ACTION_CONFIRMED = "eligibility_confirmed"
ACTION_OVERRIDDEN = "eligibility_overridden"


def execute_classification(manual_id: int) -> None:
    """Background entry point for bounded, deterministic PDF preflight."""
    with SessionLocal() as session:
        manual = session.get(Manual, manual_id)
        if manual is None or manual.eligibility_status != STATUS_PENDING:
            return
        try:
            sample = read_bounded_sample(files.manual_file_path(manual.stored_filename))
            result = classify_document(
                filename=manual.original_filename,
                document_title=manual.document_title,
                page_count=manual.page_count,
                sample_text=sample,
            )
            manual.document_type = result.document_type
            manual.eligibility_confidence = result.confidence
            manual.eligibility_reasons = json.dumps(list(result.reasons), sort_keys=True)
            detail = {
                "classifier_version": result.classifier_version,
                "document_type": result.document_type,
                "confidence": result.confidence,
                "eligible_recommendation": result.eligible_recommendation,
                "reasons": list(result.reasons),
                "scores": result.scores,
            }
            action = ACTION_CLASSIFIED
        except Exception:
            logger.exception("eligibility classification failed manual_id=%s", manual_id)
            manual.document_type = TYPE_UNKNOWN
            manual.eligibility_confidence = 0
            manual.eligibility_reasons = json.dumps(
                ["Automatic analysis failed; reviewer classification is required."]
            )
            detail = {
                "document_type": TYPE_UNKNOWN,
                "confidence": 0,
                "eligible_recommendation": False,
                "reasons": ["Automatic analysis failed; reviewer classification is required."],
            }
            action = ACTION_CLASSIFICATION_FAILED
        session.add(
            ManualEvent(
                manual_id=manual.id,
                action=action,
                actor=CLASSIFIER_ACTOR,
                detail=json.dumps(detail, sort_keys=True),
                created_at=utc_now_iso(),
            )
        )
        hardware_domain.sync_manual_applicability(
            session,
            manual,
            actor=CLASSIFIER_ACTOR,
        )
        session.commit()


def review_eligibility(
    session: Session,
    manual: Manual,
    *,
    document_type: str,
    decision: str,
    note: str = "",
) -> bool:
    document_type = document_type.strip()
    decision = decision.strip()
    note = note.strip()
    if document_type not in DOCUMENT_TYPES:
        raise ManualEligibilityError("Choose a valid document type.")
    if decision not in DECISIONS:
        raise ManualEligibilityError("Choose whether this document is eligible for extraction.")
    if decision == STATUS_ELIGIBLE and document_type != TYPE_BIOS_GUIDE and not note:
        raise ManualEligibilityError(
            "Explain why a non-BIOS-guide document should enter setting extraction."
        )
    if len(note) > 2000:
        raise ManualEligibilityError("Eligibility note must be 2,000 characters or fewer.")

    before = eligibility_snapshot(manual)
    after = {
        **before,
        "document_type": document_type,
        "eligibility_status": decision,
        "eligibility_note": note or None,
    }
    if (
        before["document_type"] == after["document_type"]
        and before["eligibility_status"] == after["eligibility_status"]
        and before["eligibility_note"] == after["eligibility_note"]
    ):
        return False
    run = session.query(ExtractionRun).filter_by(manual_id=manual.id).one_or_none()
    if run is not None and run.status in ("running", "succeeded"):
        raise ManualEligibilityError(
            "Eligibility cannot be changed after setting extraction has started."
        )

    suggestion_overridden = (
        manual.document_type is not None and manual.document_type != document_type
    )
    previous_decision = manual.eligibility_status
    now = utc_now_iso()
    manual.document_type = document_type
    manual.eligibility_status = decision
    manual.eligibility_reviewed_at = now
    actor = current_actor()
    manual.eligibility_reviewer = actor
    manual.eligibility_note = note or None
    action = (
        ACTION_OVERRIDDEN
        if suggestion_overridden or previous_decision in DECISIONS
        else ACTION_CONFIRMED
    )
    session.add(
        ManualEvent(
            manual_id=manual.id,
            action=action,
            actor=actor,
            detail=json.dumps(
                {"before": before, "after": eligibility_snapshot(manual)}, sort_keys=True
            ),
            created_at=now,
        )
    )
    hardware_domain.sync_manual_applicability(session, manual, actor=actor, created_at=now)
    session.commit()
    return True


def eligibility_snapshot(manual: Manual) -> dict[str, object]:
    try:
        reasons = json.loads(manual.eligibility_reasons or "[]")
    except json.JSONDecodeError:
        reasons = []
    return {
        "document_type": manual.document_type,
        "eligibility_status": manual.eligibility_status,
        "eligibility_confidence": manual.eligibility_confidence,
        "eligibility_reasons": reasons,
        "eligibility_reviewed_at": manual.eligibility_reviewed_at,
        "eligibility_reviewer": manual.eligibility_reviewer,
        "eligibility_note": manual.eligibility_note,
    }


def permits_extraction(manual: Manual) -> bool:
    return manual.eligibility_status in (STATUS_ELIGIBLE, STATUS_LEGACY)
