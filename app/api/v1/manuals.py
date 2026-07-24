"""API v1 manual resources — same domain calls as the HTML pages."""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Response, UploadFile

from app.api.v1.deps import SessionDep
from app.api.v1.pagination import PAGINATED_200, PageDep, paginate
from app.api.v1.schemas import (
    DuplicateUploadErrorOut,
    ErrorOut,
    ManualDetailOut,
    ManualEligibilityIn,
    ManualProvenanceIn,
    ManualSummaryOut,
)
from app.domain import eligibility, extraction_pipeline, extraction_runs, hardware
from app.domain import manuals as manuals_domain
from app.domain.errors import (
    DuplicateManualError,
    ManualEligibilityError,
    ManualProvenanceError,
    UploadRejectedError,
)

router = APIRouter(prefix="/manuals", tags=["manuals"])


@router.get(
    "",
    summary="List manuals",
    description="Every manual with its extraction-run status and settings counts (FR-27).",
    responses=PAGINATED_200,
)
def list_manuals(session: SessionDep, response: Response, page: PageDep) -> list[ManualSummaryOut]:
    overviews = manuals_domain.list_manuals_overview(session)
    return paginate([ManualSummaryOut.from_overview(o) for o in overviews], page, response)


@router.post(
    "",
    status_code=201,
    summary="Upload a manual",
    description=(
        "Multipart PDF upload (FR-1 to FR-8). Validation, hashing, dedupe,"
        " and storage are the same domain path as the web form; the single"
        " eligibility preflight starts in the background. Extraction remains"
        " pending until a reviewer confirms the document is eligible."
    ),
    responses={
        400: {"model": ErrorOut, "description": "Upload rejected by validation"},
        409: {
            "model": DuplicateUploadErrorOut,
            "description": "Duplicate content; detail carries existing_manual_id",
        },
    },
)
def upload_manual(
    session: SessionDep,
    background_tasks: BackgroundTasks,
    file: UploadFile,
    vendor: Annotated[str, Form()] = "",
    model_name: Annotated[str, Form()] = "",
    document_title: Annotated[str, Form()] = "",
    source_url: Annotated[str, Form()] = "",
    document_version: Annotated[str, Form()] = "",
    published_on: Annotated[str, Form()] = "",
    language: Annotated[str, Form()] = "en",
    board_revision: Annotated[str, Form()] = "",
    chipset: Annotated[str, Form()] = "",
    eligibility_workflow: Annotated[str, Form()] = "review",
) -> ManualDetailOut:
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
        raise HTTPException(
            status_code=409,
            detail={"message": error.message, "existing_manual_id": error.existing_id},
        ) from None
    except UploadRejectedError as error:
        raise HTTPException(status_code=400, detail=error.message) from None
    if manual.eligibility_status == eligibility.STATUS_PENDING:
        background_tasks.add_task(eligibility.execute_classification, manual.id)
    else:
        background_tasks.add_task(extraction_pipeline.execute_pending_run, manual.id)
    run = extraction_runs.get_run_for_manual(session, manual.id)
    events = manuals_domain.list_manual_events(session, manual.id)
    return ManualDetailOut.from_manual(
        manual,
        run,
        events,
        hardware.get_manual_applicability(session, manual.id),
    )


@router.get(
    "/{manual_id}",
    summary="Get one manual",
    description="Manual metadata plus its extraction run (FR-28).",
    responses={404: {"model": ErrorOut, "description": "Manual not found"}},
)
def manual_detail(manual_id: int, session: SessionDep) -> ManualDetailOut:
    manual = manuals_domain.get_manual(session, manual_id)
    if manual is None:
        raise HTTPException(status_code=404, detail="Manual not found.")
    run = extraction_runs.get_run_for_manual(session, manual.id)
    events = manuals_domain.list_manual_events(session, manual.id)
    return ManualDetailOut.from_manual(
        manual,
        run,
        events,
        hardware.get_manual_applicability(session, manual.id),
    )


@router.put(
    "/{manual_id}/provenance",
    summary="Replace manual provenance",
    description=(
        "Replace the curator-editable source and hardware labels. Upload facts"
        " such as SHA-256, retrieval timestamp, and stored filename remain immutable;"
        " real changes append an audit event."
    ),
    responses={
        400: {"model": ErrorOut, "description": "Invalid provenance metadata"},
        404: {"model": ErrorOut, "description": "Manual not found"},
    },
)
def update_provenance(
    manual_id: int,
    body: ManualProvenanceIn,
    session: SessionDep,
    background_tasks: BackgroundTasks,
) -> ManualDetailOut:
    manual = manuals_domain.get_manual(session, manual_id)
    if manual is None:
        raise HTTPException(status_code=404, detail="Manual not found.")
    try:
        manuals_domain.update_manual_provenance(
            session,
            manual,
            vendor=body.vendor,
            model_name=body.model_name,
            document_title=body.document_title,
            source_url=body.source_url,
            document_version=body.document_version,
            published_on=body.published_on,
            language=body.language,
            board_revision=body.board_revision,
            chipset=body.chipset,
        )
    except ManualProvenanceError as error:
        raise HTTPException(status_code=400, detail=error.message) from None
    run = extraction_runs.get_run_for_manual(session, manual.id)
    if (
        run is not None
        and run.status == extraction_runs.STATUS_PENDING
        and eligibility.permits_extraction(manual)
        and not manuals_domain.missing_provenance_fields(manual)
    ):
        background_tasks.add_task(extraction_pipeline.execute_pending_run, manual.id)
    events = manuals_domain.list_manual_events(session, manual.id)
    return ManualDetailOut.from_manual(
        manual,
        run,
        events,
        hardware.get_manual_applicability(session, manual.id),
    )


@router.post(
    "/{manual_id}/eligibility",
    summary="Review manual extraction eligibility",
    description=(
        "Confirm or override the deterministic document-type suggestion. Only an"
        " eligible decision permits the pending extraction run to start."
    ),
    responses={
        400: {"model": ErrorOut, "description": "Invalid eligibility decision"},
        404: {"model": ErrorOut, "description": "Manual not found"},
    },
)
def review_eligibility(
    manual_id: int,
    body: ManualEligibilityIn,
    session: SessionDep,
    background_tasks: BackgroundTasks,
) -> ManualDetailOut:
    manual = manuals_domain.get_manual(session, manual_id)
    if manual is None:
        raise HTTPException(status_code=404, detail="Manual not found.")
    try:
        eligibility.review_eligibility(
            session,
            manual,
            document_type=body.document_type,
            decision=body.decision,
            note=body.note,
        )
    except ManualEligibilityError as error:
        raise HTTPException(status_code=400, detail=error.message) from None
    run = extraction_runs.get_run_for_manual(session, manual.id)
    if (
        body.decision == eligibility.STATUS_ELIGIBLE
        and run is not None
        and run.status == extraction_runs.STATUS_PENDING
        and not manuals_domain.missing_provenance_fields(manual)
    ):
        background_tasks.add_task(extraction_pipeline.execute_pending_run, manual.id)
    events = manuals_domain.list_manual_events(session, manual.id)
    return ManualDetailOut.from_manual(
        manual,
        run,
        events,
        hardware.get_manual_applicability(session, manual.id),
    )
