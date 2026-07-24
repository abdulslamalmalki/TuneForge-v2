import logging
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.domain import (
    eligibility,
    extraction_pipeline,
    extraction_runs,
    hardware,
    provenance_discovery,
)
from app.domain import manuals as manuals_domain
from app.domain.errors import (
    DuplicateManualError,
    ManualDeletionError,
    ManualEligibilityError,
    ManualProvenanceError,
    UploadRejectedError,
)
from app.storage import files
from app.web.templating import templates

logger = logging.getLogger(__name__)

router = APIRouter()

SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/manuals", response_class=HTMLResponse)
def list_manuals(request: Request, session: SessionDep) -> HTMLResponse:
    overview = manuals_domain.list_manuals_overview(session)
    return templates.TemplateResponse(request, "manuals.html", {"manuals": overview})


@router.get("/manuals/upload", response_class=HTMLResponse)
def upload_form(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "upload.html", {"form": {}})


@router.post("/manuals")
def upload_manual(
    request: Request,
    session: SessionDep,
    file: UploadFile,
    background_tasks: BackgroundTasks,
    vendor: Annotated[str, Form()] = "",
    model_name: Annotated[str, Form()] = "",
    document_title: Annotated[str, Form()] = "",
    source_url: Annotated[str, Form()] = "",
    document_version: Annotated[str, Form()] = "",
    published_on: Annotated[str, Form()] = "",
    language: Annotated[str, Form()] = "en",
    board_revision: Annotated[str, Form()] = "",
    chipset: Annotated[str, Form()] = "",
    provenance_workflow: Annotated[str, Form()] = "",
    eligibility_workflow: Annotated[str, Form()] = "review",
):
    form = {
        "vendor": vendor,
        "model_name": model_name,
        "document_title": document_title,
        "source_url": source_url,
        "document_version": document_version,
        "published_on": published_on,
        "language": language,
        "board_revision": board_revision,
        "chipset": chipset,
    }
    try:
        manual = manuals_domain.create_manual(
            session,
            file.file,
            original_filename=file.filename,
            vendor=vendor,
            model_name=model_name,
            document_title=document_title,
            source_url=source_url,
            document_version=document_version,
            published_on=published_on,
            language=language,
            board_revision=board_revision,
            chipset=chipset,
            eligibility_required=eligibility_workflow != "legacy",
        )
    except DuplicateManualError as error:
        logger.info("upload rejected: %s existing_id=%s", error.message, error.existing_id)
        context = {
            "error": error.message,
            "existing_manual_id": error.existing_id,
            "form": form,
        }
        return templates.TemplateResponse(request, "upload.html", context, status_code=400)
    except UploadRejectedError as error:
        logger.info("upload rejected: %s", error.message)
        context = {"error": error.message, "form": form}
        return templates.TemplateResponse(request, "upload.html", context, status_code=400)
    if manual.eligibility_status == eligibility.STATUS_PENDING:
        background_tasks.add_task(eligibility.execute_classification, manual.id)
    if provenance_workflow == "discover" and manuals_domain.missing_provenance_fields(manual):
        provenance_discovery.queue_discovery(session, manual)
        background_tasks.add_task(provenance_discovery.execute_discovery, manual.id)
        destination = f"/manuals/{manual.id}/provenance/discovery"
    elif manual.eligibility_status == eligibility.STATUS_PENDING:
        destination = f"/manuals/{manual.id}/eligibility"
    else:
        # Runs after the response is sent (FR-11): upload returns immediately.
        background_tasks.add_task(extraction_pipeline.execute_pending_run, manual.id)
        destination = f"/manuals/{manual.id}"
    return RedirectResponse(url=destination, status_code=303)


@router.get("/manuals/{manual_id}", response_class=HTMLResponse)
def manual_detail(request: Request, manual_id: int, session: SessionDep) -> HTMLResponse:
    manual = _get_manual_or_404(session, manual_id)
    run = extraction_runs.get_run_for_manual(session, manual.id)
    events = manuals_domain.list_manual_events(session, manual.id)
    missing = manuals_domain.missing_provenance_fields(manual)
    discovery = provenance_discovery.latest_discovery(session, manual.id)
    context = {
        "manual": manual,
        "run": run,
        "events": events,
        "missing": missing,
        "discovery": discovery,
        "eligibility": eligibility.eligibility_snapshot(manual),
        "applicability": hardware.get_manual_applicability(session, manual.id),
    }
    return templates.TemplateResponse(request, "manual_detail.html", context)


@router.get("/manuals/{manual_id}/provenance/edit", response_class=HTMLResponse)
def edit_provenance_form(request: Request, manual_id: int, session: SessionDep) -> HTMLResponse:
    manual = _get_manual_or_404(session, manual_id)
    return templates.TemplateResponse(
        request,
        "manual_provenance_edit.html",
        {"manual": manual, "form": manuals_domain.provenance_snapshot(manual)},
    )


@router.post("/manuals/{manual_id}/provenance")
def update_provenance(
    request: Request,
    manual_id: int,
    session: SessionDep,
    background_tasks: BackgroundTasks,
    vendor: Annotated[str, Form()] = "",
    model_name: Annotated[str, Form()] = "",
    document_title: Annotated[str, Form()] = "",
    source_url: Annotated[str, Form()] = "",
    document_version: Annotated[str, Form()] = "",
    published_on: Annotated[str, Form()] = "",
    language: Annotated[str, Form()] = "en",
    board_revision: Annotated[str, Form()] = "",
    chipset: Annotated[str, Form()] = "",
):
    manual = _get_manual_or_404(session, manual_id)
    form = {
        "vendor": vendor,
        "model_name": model_name,
        "document_title": document_title,
        "source_url": source_url,
        "document_version": document_version,
        "published_on": published_on,
        "language": language,
        "board_revision": board_revision,
        "chipset": chipset,
    }
    try:
        manuals_domain.update_manual_provenance(session, manual, **form)
    except ManualProvenanceError as error:
        return templates.TemplateResponse(
            request,
            "manual_provenance_edit.html",
            {"manual": manual, "form": form, "error": error.message},
            status_code=400,
        )
    run = extraction_runs.get_run_for_manual(session, manual.id)
    if (
        run is not None
        and run.status == extraction_runs.STATUS_PENDING
        and not manuals_domain.missing_provenance_fields(manual)
        and eligibility.permits_extraction(manual)
    ):
        background_tasks.add_task(extraction_pipeline.execute_pending_run, manual.id)
    return RedirectResponse(url=f"/manuals/{manual.id}", status_code=303)


@router.get("/manuals/{manual_id}/provenance/discovery", response_class=HTMLResponse)
def provenance_discovery_page(
    request: Request, manual_id: int, session: SessionDep
) -> HTMLResponse:
    manual = _get_manual_or_404(session, manual_id)
    discovery = provenance_discovery.latest_discovery(session, manual.id)
    form = discovery.metadata if discovery else manuals_domain.provenance_snapshot(manual)
    return templates.TemplateResponse(
        request,
        "manual_provenance_discovery.html",
        {"manual": manual, "discovery": discovery, "form": form},
    )


@router.post("/manuals/{manual_id}/provenance/discover")
def retry_provenance_discovery(
    manual_id: int,
    session: SessionDep,
    background_tasks: BackgroundTasks,
) -> RedirectResponse:
    manual = _get_manual_or_404(session, manual_id)
    provenance_discovery.queue_discovery(session, manual)
    background_tasks.add_task(provenance_discovery.execute_discovery, manual.id)
    return RedirectResponse(url=f"/manuals/{manual.id}/provenance/discovery", status_code=303)


@router.post("/manuals/{manual_id}/provenance/confirm")
def confirm_provenance_discovery(
    request: Request,
    manual_id: int,
    session: SessionDep,
    background_tasks: BackgroundTasks,
    vendor: Annotated[str, Form()] = "",
    model_name: Annotated[str, Form()] = "",
    document_title: Annotated[str, Form()] = "",
    source_url: Annotated[str, Form()] = "",
    manual_source_url: Annotated[str, Form()] = "",
    document_version: Annotated[str, Form()] = "",
    published_on: Annotated[str, Form()] = "",
    language: Annotated[str, Form()] = "en",
    board_revision: Annotated[str, Form()] = "",
    chipset: Annotated[str, Form()] = "",
):
    manual = _get_manual_or_404(session, manual_id)
    form = {
        "vendor": vendor,
        "model_name": model_name,
        "document_title": document_title,
        "document_version": document_version,
        "published_on": published_on,
        "language": language,
        "board_revision": board_revision,
        "chipset": chipset,
    }
    selected_url = manual_source_url.strip() or source_url.strip()
    try:
        provenance_discovery.confirm_discovery(session, manual, source_url=selected_url, **form)
    except ManualProvenanceError as error:
        discovery = provenance_discovery.latest_discovery(session, manual.id)
        return templates.TemplateResponse(
            request,
            "manual_provenance_discovery.html",
            {
                "manual": manual,
                "discovery": discovery,
                "form": form,
                "manual_source_url": manual_source_url,
                "error": error.message,
            },
            status_code=400,
        )
    run = extraction_runs.get_run_for_manual(session, manual.id)
    if (
        run is not None
        and run.status == extraction_runs.STATUS_PENDING
        and eligibility.permits_extraction(manual)
    ):
        background_tasks.add_task(extraction_pipeline.execute_pending_run, manual.id)
        return RedirectResponse(url=f"/manuals/{manual.id}", status_code=303)
    return RedirectResponse(url=f"/manuals/{manual.id}/eligibility", status_code=303)


@router.get("/manuals/{manual_id}/eligibility", response_class=HTMLResponse)
def eligibility_review_page(request: Request, manual_id: int, session: SessionDep) -> HTMLResponse:
    manual = _get_manual_or_404(session, manual_id)
    return templates.TemplateResponse(
        request,
        "manual_eligibility.html",
        {
            "manual": manual,
            "eligibility": eligibility.eligibility_snapshot(manual),
            "document_types": eligibility.DOCUMENT_TYPES,
            "missing": manuals_domain.missing_provenance_fields(manual),
        },
    )


@router.post("/manuals/{manual_id}/eligibility")
def review_manual_eligibility(
    request: Request,
    manual_id: int,
    session: SessionDep,
    background_tasks: BackgroundTasks,
    document_type: Annotated[str, Form()],
    decision: Annotated[str, Form()],
    note: Annotated[str, Form()] = "",
):
    manual = _get_manual_or_404(session, manual_id)
    try:
        eligibility.review_eligibility(
            session,
            manual,
            document_type=document_type,
            decision=decision,
            note=note,
        )
    except ManualEligibilityError as error:
        return templates.TemplateResponse(
            request,
            "manual_eligibility.html",
            {
                "manual": manual,
                "eligibility": {
                    **eligibility.eligibility_snapshot(manual),
                    "document_type": document_type,
                    "eligibility_status": decision,
                    "eligibility_note": note,
                },
                "document_types": eligibility.DOCUMENT_TYPES,
                "missing": manuals_domain.missing_provenance_fields(manual),
                "error": error.message,
            },
            status_code=400,
        )
    run = extraction_runs.get_run_for_manual(session, manual.id)
    if (
        decision == eligibility.STATUS_ELIGIBLE
        and run is not None
        and run.status == extraction_runs.STATUS_PENDING
        and not manuals_domain.missing_provenance_fields(manual)
    ):
        background_tasks.add_task(extraction_pipeline.execute_pending_run, manual.id)
    return RedirectResponse(url=f"/manuals/{manual.id}", status_code=303)


@router.get("/manuals/{manual_id}/file")
def manual_file(manual_id: int, session: SessionDep) -> FileResponse:
    manual = _get_manual_or_404(session, manual_id)
    path = files.manual_file_path(manual.stored_filename)
    if not path.is_file():
        logger.error("stored file missing manual_id=%s expected=%s", manual.id, path)
        raise HTTPException(status_code=500, detail="The stored PDF file is missing.")
    return FileResponse(path, media_type="application/pdf")


@router.post("/manuals/{manual_id}/delete")
def delete_manual(
    request: Request,
    manual_id: int,
    session: SessionDep,
    confirm: Annotated[str, Form()] = "",
):
    manual = _get_manual_or_404(session, manual_id)
    if confirm != "yes":
        return templates.TemplateResponse(request, "manual_delete_confirm.html", {"manual": manual})
    try:
        manuals_domain.delete_manual(session, manual)
    except ManualDeletionError as error:
        raise HTTPException(status_code=500, detail=error.message) from None
    return RedirectResponse(url="/manuals", status_code=303)


def _get_manual_or_404(session: Session, manual_id: int):
    manual = manuals_domain.get_manual(session, manual_id)
    if manual is None:
        raise HTTPException(status_code=404, detail="Manual not found.")
    return manual
