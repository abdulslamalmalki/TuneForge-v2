"""Extraction-run lifecycle: each manual's single processing record.

System invariant: every manual has exactly one extraction run, regardless of
when the manual was created. A pending run has not been processed, so its
parser_version stays NULL until parsing actually occurs. The UNIQUE index on
extraction_runs.manual_id is the database-level backstop against duplicates.
"""

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ExtractionRun, Manual

logger = logging.getLogger(__name__)

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"

INTERRUPTED_MESSAGE = (
    "Extraction was interrupted by an application shutdown. "
    "Delete the manual and upload it again to retry."
)


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def create_pending_run(session: Session, manual: Manual) -> ExtractionRun:
    """Add the manual's single pending extraction run (caller commits)."""
    run = ExtractionRun(
        manual_id=manual.id,
        status=STATUS_PENDING,
        parser_version=None,
        created_at=utc_now_iso(),
    )
    session.add(run)
    return run


def fail_interrupted_runs(session: Session) -> int:
    """Mark runs stuck in `running` after an unclean shutdown as failed (FR-29).

    Only `running` runs are touched; idempotent. Returns the number marked.
    """
    interrupted = session.scalars(
        select(ExtractionRun).where(ExtractionRun.status == STATUS_RUNNING)
    ).all()
    for run in interrupted:
        run.status = STATUS_FAILED
        run.error_message = INTERRUPTED_MESSAGE
        run.finished_at = utc_now_iso()
    session.commit()
    if interrupted:
        logger.info(
            "marked %s interrupted extraction run(s) as failed at startup", len(interrupted)
        )
    return len(interrupted)


def get_run_for_manual(session: Session, manual_id: int) -> ExtractionRun | None:
    return session.scalars(
        select(ExtractionRun).where(ExtractionRun.manual_id == manual_id)
    ).first()


def ensure_every_manual_has_run(session: Session) -> int:
    """Create one pending run for every manual that lacks one; idempotent.

    Returns the number of runs created. Existing runs are never touched.
    """
    has_run = select(ExtractionRun.id).where(ExtractionRun.manual_id == Manual.id).exists()
    manuals_without_run = session.scalars(select(Manual).where(~has_run)).all()
    for manual in manuals_without_run:
        create_pending_run(session, manual)
    session.commit()
    if manuals_without_run:
        logger.info(
            "created pending extraction runs for %s manual(s) that had none",
            len(manuals_without_run),
        )
    return len(manuals_without_run)
