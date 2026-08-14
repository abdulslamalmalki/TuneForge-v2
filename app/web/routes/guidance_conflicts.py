"""Administrator queue for knowledge-conflict detection and resolution."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.domain import guidance_conflicts
from app.domain.errors import GuidanceClaimError
from app.web.templating import templates

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/guidance-conflicts", response_class=HTMLResponse)
def conflict_queue(
    request: Request,
    session: SessionDep,
    status: str = "pending",
) -> HTMLResponse:
    try:
        rows = guidance_conflicts.list_conflicts(session, status=status)
    except GuidanceClaimError as error:
        return templates.TemplateResponse(
            request,
            "guidance_conflicts.html",
            {
                "rows": [],
                "counts": guidance_conflicts.status_counts(session),
                "status_filter": status,
                "error": error.message,
            },
            status_code=400,
        )
    return templates.TemplateResponse(
        request,
        "guidance_conflicts.html",
        {
            "rows": rows,
            "counts": guidance_conflicts.status_counts(session),
            "status_filter": status,
            "error": None,
        },
    )


@router.post("/guidance-conflicts/scan")
def scan_conflicts(session: SessionDep) -> RedirectResponse:
    guidance_conflicts.scan_approved_claims(session)
    return RedirectResponse("/guidance-conflicts?status=pending", status_code=303)


@router.get("/guidance-conflicts/{conflict_id}", response_class=HTMLResponse)
def conflict_detail(
    request: Request,
    conflict_id: int,
    session: SessionDep,
) -> HTMLResponse:
    detail = guidance_conflicts.get_detail(session, conflict_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Knowledge conflict not found.")
    return templates.TemplateResponse(
        request,
        "guidance_conflict_detail.html",
        {"detail": detail, "error": None},
    )


@router.post("/guidance-conflicts/{conflict_id}/review")
def review_conflict(
    request: Request,
    conflict_id: int,
    session: SessionDep,
    decision: Annotated[str, Form()] = "",
    note: Annotated[str, Form()] = "",
):
    detail = guidance_conflicts.get_detail(session, conflict_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Knowledge conflict not found.")
    try:
        guidance_conflicts.review_conflict(
            session,
            detail.conflict,
            decision=decision,
            note=note,
        )
    except GuidanceClaimError as error:
        refreshed = guidance_conflicts.get_detail(session, conflict_id)
        return templates.TemplateResponse(
            request,
            "guidance_conflict_detail.html",
            {"detail": refreshed, "error": error.message},
            status_code=400,
        )
    return RedirectResponse(f"/guidance-conflicts/{conflict_id}", status_code=303)
