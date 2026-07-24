"""Persistent, failure-isolated multi-manual intake."""

from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    ExtractionRun,
    IngestionBatch,
    IngestionBatchItem,
    Manual,
    Setting,
)
from app.domain import manuals as manuals_domain
from app.domain.errors import DuplicateManualError, UploadRejectedError
from app.domain.extraction_runs import utc_now_iso

STATUS_PROCESSING = "processing"
STATUS_READY = "ready"
STATUS_READY_WITH_ERRORS = "ready_with_errors"
ITEM_ACCEPTED = "accepted"
ITEM_DUPLICATE = "duplicate"
ITEM_FAILED = "failed"


@dataclass(frozen=True)
class BatchUpload:
    filename: str
    stream: BinaryIO


@dataclass(frozen=True)
class BatchItemOverview:
    item: IngestionBatchItem
    manual: Manual | None
    workflow_status: str
    run_status: str | None
    draft_count: int


@dataclass(frozen=True)
class BatchOverview:
    batch: IngestionBatch
    items: list[BatchItemOverview]


def create_batch(session: Session, uploads: list[BatchUpload]) -> IngestionBatch:
    if not uploads:
        raise UploadRejectedError("Choose at least one PDF file.")
    now = utc_now_iso()
    batch = IngestionBatch(
        status=STATUS_PROCESSING,
        total_count=len(uploads),
        accepted_count=0,
        failed_count=0,
        created_at=now,
        finished_at=None,
    )
    session.add(batch)
    session.commit()

    for upload in uploads:
        try:
            manual = manuals_domain.create_manual(
                session,
                upload.stream,
                original_filename=upload.filename,
                vendor="",
                model_name="",
                eligibility_required=True,
            )
            item = IngestionBatchItem(
                batch_id=batch.id,
                manual_id=manual.id,
                original_filename=upload.filename,
                intake_status=ITEM_ACCEPTED,
                error_message=None,
                created_at=utc_now_iso(),
            )
            batch.accepted_count += 1
        except DuplicateManualError as error:
            item = IngestionBatchItem(
                batch_id=batch.id,
                manual_id=error.existing_id,
                original_filename=upload.filename,
                intake_status=ITEM_DUPLICATE,
                error_message=error.message,
                created_at=utc_now_iso(),
            )
            batch.failed_count += 1
        except UploadRejectedError as error:
            item = IngestionBatchItem(
                batch_id=batch.id,
                manual_id=None,
                original_filename=upload.filename,
                intake_status=ITEM_FAILED,
                error_message=error.message,
                created_at=utc_now_iso(),
            )
            batch.failed_count += 1
        session.add(item)
        session.commit()

    batch.status = STATUS_READY_WITH_ERRORS if batch.failed_count else STATUS_READY
    batch.finished_at = utc_now_iso()
    session.commit()
    return batch


def list_batches(session: Session) -> list[IngestionBatch]:
    return list(session.scalars(select(IngestionBatch).order_by(IngestionBatch.id.desc())))


def get_batch_overview(session: Session, batch_id: int) -> BatchOverview | None:
    batch = session.get(IngestionBatch, batch_id)
    if batch is None:
        return None
    items = list(
        session.scalars(
            select(IngestionBatchItem)
            .where(IngestionBatchItem.batch_id == batch_id)
            .order_by(IngestionBatchItem.id)
        )
    )
    manual_ids = [item.manual_id for item in items if item.manual_id is not None]
    manuals = {
        manual.id: manual
        for manual in session.scalars(select(Manual).where(Manual.id.in_(manual_ids)))
    }
    runs = {
        run.manual_id: run
        for run in session.scalars(
            select(ExtractionRun).where(ExtractionRun.manual_id.in_(manual_ids))
        )
    }
    drafts = dict(
        session.execute(
            select(Setting.manual_id, func.count())
            .where(Setting.manual_id.in_(manual_ids), Setting.status == "draft")
            .group_by(Setting.manual_id)
        ).all()
    )
    rows = []
    for item in items:
        manual = manuals.get(item.manual_id)
        run = runs.get(item.manual_id)
        draft_count = drafts.get(item.manual_id, 0)
        rows.append(
            BatchItemOverview(
                item=item,
                manual=manual,
                workflow_status=_workflow_status(item, manual, run, draft_count),
                run_status=run.status if run else None,
                draft_count=draft_count,
            )
        )
    return BatchOverview(batch=batch, items=rows)


def _workflow_status(
    item: IngestionBatchItem,
    manual: Manual | None,
    run: ExtractionRun | None,
    draft_count: int,
) -> str:
    if item.intake_status in (ITEM_FAILED, ITEM_DUPLICATE):
        return item.intake_status
    if manual is None:
        return "manual_deleted"
    if manual.eligibility_confidence is None:
        return "preflight_pending"
    if manuals_domain.missing_provenance_fields(manual):
        return "provenance_review"
    if manual.eligibility_status == "pending":
        return "eligibility_review"
    if manual.eligibility_status == "ineligible":
        return "ineligible"
    if run is None or run.status == "pending":
        return "extraction_pending"
    if run.status in ("running", "failed"):
        return f"extraction_{run.status}"
    if draft_count:
        return "draft_review"
    return "extraction_complete"
