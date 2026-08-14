"""Protected guidance candidate queue and human review workflow."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.domain import guidance_candidates
from app.domain.errors import GuidanceClaimError
from app.web.templating import templates

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/guidance-candidates", response_class=HTMLResponse)
def candidate_queue(
    request: Request,
    session: SessionDep,
    status: str = "pending",
    vendor: str = "",
    claim_type: str = "",
    conflicts: bool = False,
) -> HTMLResponse:
    try:
        rows = guidance_candidates.list_candidates(
            session,
            status=status,
            vendor_id=vendor,
            claim_type=claim_type,
            conflicts_only=conflicts,
        )
    except GuidanceClaimError as error:
        rows = ()
        message = error.message
    else:
        message = None
    return templates.TemplateResponse(
        request,
        "guidance_candidates.html",
        {
            "rows": rows,
            "counts": guidance_candidates.status_counts(session),
            "metrics": guidance_candidates.coverage_metrics(session),
            "status_filter": status,
            "vendor_filter": vendor,
            "claim_type_filter": claim_type,
            "conflicts_only": conflicts,
            "claim_types": guidance_candidates.guidance_claims.CLAIM_TYPES,
            "error": message,
        },
        status_code=400 if message else 200,
    )


@router.get("/guidance-candidates/{candidate_id}", response_class=HTMLResponse)
def candidate_detail(
    request: Request, candidate_id: int, session: SessionDep
) -> HTMLResponse:
    return _detail_response(request, session, candidate_id)


@router.post("/guidance-candidates/{candidate_id}/edit")
def edit_candidate(
    request: Request,
    candidate_id: int,
    session: SessionDep,
    claim_text: Annotated[str, Form()] = "",
    claim_type: Annotated[str, Form()] = "",
    note: Annotated[str, Form()] = "",
):
    try:
        guidance_candidates.edit_candidate(
            session,
            candidate_id,
            claim_text=claim_text,
            claim_type=claim_type,
            note=note,
        )
    except GuidanceClaimError as error:
        return _detail_response(request, session, candidate_id, error=error.message)
    return RedirectResponse(f"/guidance-candidates/{candidate_id}", status_code=303)


@router.post("/guidance-candidates/{candidate_id}/state")
def set_candidate_state(
    request: Request,
    candidate_id: int,
    session: SessionDep,
    status: Annotated[str, Form()] = "",
    note: Annotated[str, Form()] = "",
):
    try:
        guidance_candidates.set_review_state(
            session, candidate_id, status=status, note=note
        )
    except GuidanceClaimError as error:
        return _detail_response(request, session, candidate_id, error=error.message)
    return RedirectResponse(f"/guidance-candidates?status={status}", status_code=303)


@router.post("/guidance-candidates/{candidate_id}/merge")
def merge_candidate(
    request: Request,
    candidate_id: int,
    session: SessionDep,
    duplicate_of_candidate_id: Annotated[int, Form()],
    note: Annotated[str, Form()] = "",
):
    try:
        guidance_candidates.merge_duplicate(
            session,
            candidate_id,
            duplicate_of_candidate_id=duplicate_of_candidate_id,
            note=note,
        )
    except GuidanceClaimError as error:
        return _detail_response(request, session, candidate_id, error=error.message)
    return RedirectResponse("/guidance-candidates?status=duplicate", status_code=303)


@router.post("/guidance-candidates/{candidate_id}/promote")
def promote_candidate(
    request: Request,
    candidate_id: int,
    session: SessionDep,
    note: Annotated[str, Form()] = "",
):
    try:
        claim = guidance_candidates.promote_candidate(session, candidate_id, note=note)
    except GuidanceClaimError as error:
        return _detail_response(request, session, candidate_id, error=error.message)
    return RedirectResponse(f"/guidance-claims/{claim.id}", status_code=303)


def _detail_response(
    request: Request,
    session: Session,
    candidate_id: int,
    *,
    error: str | None = None,
) -> HTMLResponse:
    detail = guidance_candidates.get_detail(session, candidate_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Guidance candidate not found.")
    return templates.TemplateResponse(
        request,
        "guidance_candidate_detail.html",
        {
            "detail": detail,
            "claim_types": guidance_candidates.guidance_claims.CLAIM_TYPES,
            "error": error,
        },
        status_code=400 if error else 200,
    )
