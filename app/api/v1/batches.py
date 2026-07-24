"""Persistent batch-ingestion API."""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile

from app import config
from app.api.v1.deps import SessionDep
from app.api.v1.schemas import BatchDetailOut, BatchSummaryOut, ErrorOut
from app.domain import batches, eligibility, provenance_discovery
from app.domain.errors import UploadRejectedError

router = APIRouter(prefix="/batches", tags=["batches"])


@router.get("", summary="List ingestion batches")
def list_batches(session: SessionDep) -> list[BatchSummaryOut]:
    return [
        BatchSummaryOut.model_validate(batch, from_attributes=True)
        for batch in batches.list_batches(session)
    ]


@router.post(
    "",
    status_code=201,
    summary="Upload a manual batch",
    responses={400: {"model": ErrorOut, "description": "Batch rejected"}},
)
def upload_batch(
    session: SessionDep,
    background_tasks: BackgroundTasks,
    files: Annotated[list[UploadFile], File()],
) -> BatchDetailOut:
    if len(files) > config.MAX_BATCH_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"A batch can contain at most {config.MAX_BATCH_FILES} files.",
        )
    try:
        batch = batches.create_batch(
            session,
            [batches.BatchUpload(filename=file.filename or "", stream=file.file) for file in files],
        )
    except UploadRejectedError as error:
        raise HTTPException(status_code=400, detail=error.message) from None
    _queue_preflight(session, batch.id, background_tasks)
    overview = batches.get_batch_overview(session, batch.id)
    assert overview is not None
    return BatchDetailOut.from_overview(overview)


@router.get(
    "/{batch_id}",
    summary="Get ingestion batch progress",
    responses={404: {"model": ErrorOut, "description": "Batch not found"}},
)
def batch_detail(batch_id: int, session: SessionDep) -> BatchDetailOut:
    overview = batches.get_batch_overview(session, batch_id)
    if overview is None:
        raise HTTPException(status_code=404, detail="Batch not found.")
    return BatchDetailOut.from_overview(overview)


def _queue_preflight(session: SessionDep, batch_id: int, background_tasks: BackgroundTasks) -> None:
    overview = batches.get_batch_overview(session, batch_id)
    if overview is None:
        return
    for row in overview.items:
        if row.item.intake_status != batches.ITEM_ACCEPTED or row.manual is None:
            continue
        provenance_discovery.queue_discovery(session, row.manual)
        background_tasks.add_task(eligibility.execute_classification, row.manual.id)
        background_tasks.add_task(provenance_discovery.execute_discovery, row.manual.id)
