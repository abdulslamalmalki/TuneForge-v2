"""Versioned API for trusted tuning-evidence source intake and review."""

from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse

from app.api.v1.deps import SessionDep
from app.api.v1.schemas import (
    ErrorOut,
    EvidenceSourceDetailOut,
    EvidenceSourceReviewIn,
    EvidenceSourceSummaryOut,
)
from app.domain import evidence_sources as evidence_domain
from app.domain.errors import EvidenceSourceError
from app.storage import files

router = APIRouter(tags=["evidence sources"])

_ERRORS = {
    400: {"model": ErrorOut, "description": "Evidence source validation failed"},
    404: {"model": ErrorOut, "description": "Evidence source not found"},
}


@router.get("/evidence-sources", summary="List trusted tuning-evidence sources")
def list_evidence_sources(session: SessionDep) -> list[EvidenceSourceSummaryOut]:
    return [
        EvidenceSourceSummaryOut.from_overview(row) for row in evidence_domain.list_sources(session)
    ]


@router.post(
    "/evidence-sources",
    summary="Upload an official PDF as a pending evidence source",
    response_model=EvidenceSourceDetailOut,
    status_code=status.HTTP_201_CREATED,
    responses=_ERRORS,
)
def upload_evidence_source(
    session: SessionDep,
    file: Annotated[UploadFile, File()],
    publisher_id: Annotated[str, Form()] = "",
    title: Annotated[str, Form()] = "",
    source_url: Annotated[str, Form()] = "",
    document_id: Annotated[str, Form()] = "",
    document_version: Annotated[str, Form()] = "",
    published_on: Annotated[str, Form()] = "",
    language: Annotated[str, Form()] = "en",
) -> EvidenceSourceDetailOut:
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
        raise HTTPException(status_code=400, detail=error.message) from None
    detail = evidence_domain.get_detail(session, source.id)
    if detail is None:
        raise RuntimeError("Created evidence source could not be reloaded.")
    return EvidenceSourceDetailOut.from_detail(detail)


@router.get(
    "/evidence-sources/{source_id}",
    summary="Get evidence source provenance, extraction, passages, and audit history",
    responses={404: _ERRORS[404]},
)
def evidence_source_detail(source_id: int, session: SessionDep) -> EvidenceSourceDetailOut:
    detail = evidence_domain.get_detail(session, source_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Evidence source not found.")
    return EvidenceSourceDetailOut.from_detail(detail)


@router.get(
    "/evidence-sources/{source_id}/artifact",
    summary="Download the immutable source PDF",
    responses={404: _ERRORS[404]},
)
def evidence_source_artifact(source_id: int, session: SessionDep) -> FileResponse:
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


@router.post(
    "/evidence-sources/{source_id}/review",
    summary="Approve or reject a pending evidence source",
    responses=_ERRORS,
)
def review_evidence_source(
    source_id: int,
    body: EvidenceSourceReviewIn,
    session: SessionDep,
) -> EvidenceSourceDetailOut:
    detail = evidence_domain.get_detail(session, source_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Evidence source not found.")
    try:
        evidence_domain.review_source(
            session,
            detail.source,
            decision=body.decision,
            note=body.note,
        )
    except EvidenceSourceError as error:
        raise HTTPException(status_code=400, detail=error.message) from None
    reviewed = evidence_domain.get_detail(session, source_id)
    if reviewed is None:
        raise RuntimeError("Reviewed evidence source could not be reloaded.")
    return EvidenceSourceDetailOut.from_detail(reviewed)
