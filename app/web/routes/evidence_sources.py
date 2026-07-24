"""Server-rendered trusted evidence source intake and review."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.domain import evidence_sources as evidence_domain
from app.domain.errors import EvidenceSourceError
from app.storage import files
from app.web.templating import templates

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/evidence-sources", response_class=HTMLResponse)
def list_sources(request: Request, session: SessionDep) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "evidence_sources.html",
        {"sources": evidence_domain.list_sources(session)},
    )


@router.get("/evidence-sources/upload", response_class=HTMLResponse)
def upload_form(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "evidence_source_upload.html",
        {"publishers": evidence_domain.PUBLISHERS, "form": {}, "error": None},
    )


@router.post("/evidence-sources")
def upload_source(
    request: Request,
    session: SessionDep,
    file: UploadFile,
    publisher_id: Annotated[str, Form()] = "",
    title: Annotated[str, Form()] = "",
    source_url: Annotated[str, Form()] = "",
    document_id: Annotated[str, Form()] = "",
    document_version: Annotated[str, Form()] = "",
    published_on: Annotated[str, Form()] = "",
    language: Annotated[str, Form()] = "en",
):
    form = {
        "publisher_id": publisher_id,
        "title": title,
        "source_url": source_url,
        "document_id": document_id,
        "document_version": document_version,
        "published_on": published_on,
        "language": language,
    }
    try:
        source = evidence_domain.create_source(
            session,
            file.file,
            file.filename,
            publisher_id=publisher_id,
            title=title,
            source_url=source_url,
            document_id=document_id,
            document_version=document_version,
            published_on=published_on,
            language=language,
        )
    except EvidenceSourceError as error:
        return templates.TemplateResponse(
            request,
            "evidence_source_upload.html",
            {
                "publishers": evidence_domain.PUBLISHERS,
                "form": form,
                "error": error.message,
            },
            status_code=400,
        )
    return RedirectResponse(url=f"/evidence-sources/{source.id}", status_code=303)


@router.get("/evidence-sources/{source_id}", response_class=HTMLResponse)
def source_detail(request: Request, source_id: int, session: SessionDep) -> HTMLResponse:
    detail = evidence_domain.get_detail(session, source_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Evidence source not found.")
    return templates.TemplateResponse(
        request,
        "evidence_source_detail.html",
        {"detail": detail, "error": None},
    )


@router.get("/evidence-sources/{source_id}/artifact")
def source_artifact(source_id: int, session: SessionDep) -> FileResponse:
    detail = evidence_domain.get_detail(session, source_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Evidence source not found.")
    path = files.evidence_source_file_path(detail.source.stored_filename)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Evidence source artifact is missing.")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=detail.source.original_filename,
    )


@router.post("/evidence-sources/{source_id}/review")
def review_source(
    request: Request,
    source_id: int,
    session: SessionDep,
    decision: Annotated[str, Form()] = "",
    note: Annotated[str, Form()] = "",
):
    detail = evidence_domain.get_detail(session, source_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Evidence source not found.")
    try:
        evidence_domain.review_source(
            session,
            detail.source,
            decision=decision,
            note=note,
        )
    except EvidenceSourceError as error:
        refreshed = evidence_domain.get_detail(session, source_id)
        if refreshed is None:
            raise RuntimeError("Evidence source disappeared during review.") from None
        return templates.TemplateResponse(
            request,
            "evidence_source_detail.html",
            {"detail": refreshed, "error": error.message},
            status_code=400,
        )
    return RedirectResponse(url=f"/evidence-sources/{source_id}", status_code=303)
