"""Public manual submissions that always enter private human review."""

import logging
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.domain import eligibility
from app.domain import manuals as manuals_domain
from app.domain.errors import DuplicateManualError, UploadRejectedError
from app.normalization.vendors import VENDORS
from app.provenance.official_sources import support_page_error
from app.web.templating import templates

logger = logging.getLogger(__name__)
router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/upload-manual", response_class=HTMLResponse)
def upload_manual_form(request: Request) -> HTMLResponse:
    return _render_form(request, form={})


@router.post("/upload-manual", response_class=HTMLResponse)
def submit_manual(
    request: Request,
    session: SessionDep,
    background_tasks: BackgroundTasks,
    file: UploadFile,
    vendor: Annotated[str, Form()] = "",
    model_name: Annotated[str, Form()] = "",
    document_title: Annotated[str, Form()] = "",
    source_url: Annotated[str, Form()] = "",
    document_version: Annotated[str, Form()] = "",
    language: Annotated[str, Form()] = "en",
    board_revision: Annotated[str, Form()] = "",
    chipset: Annotated[str, Form()] = "",
) -> HTMLResponse:
    form = {
        "vendor": vendor,
        "model_name": model_name,
        "document_title": document_title,
        "source_url": source_url,
        "document_version": document_version,
        "language": language,
        "board_revision": board_revision,
        "chipset": chipset,
    }
    if not model_name.strip():
        return _render_form(
            request,
            form=form,
            error="Enter the motherboard model covered by this manual.",
            status_code=400,
        )
    provenance_error = support_page_error(vendor, source_url)
    if provenance_error:
        return _render_form(
            request,
            form=form,
            error=provenance_error,
            status_code=400,
        )
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
            language=language,
            board_revision=board_revision,
            chipset=chipset,
            eligibility_required=True,
        )
    except DuplicateManualError as error:
        logger.info("public duplicate manual submission existing_id=%s", error.existing_id)
        return templates.TemplateResponse(
            request,
            "public_manual_submitted.html",
            {
                "reference": f"TF-MANUAL-{error.existing_id:06d}",
                "duplicate": True,
            },
            status_code=409,
        )
    except UploadRejectedError as error:
        logger.info("public manual submission rejected: %s", error.message)
        return _render_form(
            request,
            form=form,
            error=error.message,
            status_code=400,
        )

    background_tasks.add_task(eligibility.execute_classification, manual.id)
    return templates.TemplateResponse(
        request,
        "public_manual_submitted.html",
        {
            "reference": f"TF-MANUAL-{manual.id:06d}",
            "duplicate": False,
        },
        status_code=202,
    )


def _render_form(
    request: Request,
    *,
    form: dict[str, str],
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "public_manual_upload.html",
        {
            "vendors": VENDORS,
            "form": form,
            "error": error,
        },
        status_code=status_code,
    )
