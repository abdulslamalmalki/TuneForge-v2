"""Persistent batch-ingestion reviewer pages."""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app import config
from app.db.session import get_session
from app.domain import batches, eligibility, provenance_discovery
from app.domain.errors import UploadRejectedError
from app.web.templating import templates

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/batches", response_class=HTMLResponse)
def list_batches(request: Request, session: SessionDep) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "batches.html", {"batches": batches.list_batches(session)}
    )


@router.get("/batches/upload", response_class=HTMLResponse)
def batch_upload_form(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "batch_upload.html", {})


@router.post("/batches")
def upload_batch(
    request: Request,
    session: SessionDep,
    background_tasks: BackgroundTasks,
    files: Annotated[list[UploadFile], File()],
):
    if len(files) > config.MAX_BATCH_FILES:
        return templates.TemplateResponse(
            request,
            "batch_upload.html",
            {"error": f"A batch can contain at most {config.MAX_BATCH_FILES} files."},
            status_code=400,
        )
    try:
        batch = batches.create_batch(
            session,
            [batches.BatchUpload(filename=file.filename or "", stream=file.file) for file in files],
        )
    except UploadRejectedError as error:
        return templates.TemplateResponse(
            request, "batch_upload.html", {"error": error.message}, status_code=400
        )
    _queue_preflight(session, batch.id, background_tasks)
    return RedirectResponse(url=f"/batches/{batch.id}", status_code=303)


@router.get("/batches/{batch_id}", response_class=HTMLResponse)
def batch_detail(request: Request, batch_id: int, session: SessionDep) -> HTMLResponse:
    overview = batches.get_batch_overview(session, batch_id)
    if overview is None:
        raise HTTPException(status_code=404, detail="Batch not found.")
    return templates.TemplateResponse(request, "batch_detail.html", {"overview": overview})


def _queue_preflight(session: Session, batch_id: int, background_tasks: BackgroundTasks) -> None:
    overview = batches.get_batch_overview(session, batch_id)
    if overview is None:
        return
    for row in overview.items:
        if row.item.intake_status != batches.ITEM_ACCEPTED or row.manual is None:
            continue
        provenance_discovery.queue_discovery(session, row.manual)
        background_tasks.add_task(eligibility.execute_classification, row.manual.id)
        background_tasks.add_task(provenance_discovery.execute_discovery, row.manual.id)
