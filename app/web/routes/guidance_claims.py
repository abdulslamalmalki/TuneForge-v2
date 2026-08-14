"""Server-rendered guidance claim review queue and detail workflow."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.domain import guidance_claims as claim_domain
from app.domain.errors import GuidanceClaimError
from app.web.templating import templates

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/guidance-claims", response_class=HTMLResponse)
def claim_queue(
    request: Request,
    session: SessionDep,
    status: str = "draft",
) -> HTMLResponse:
    try:
        claims = claim_domain.list_claims(session, status=status)
    except GuidanceClaimError as error:
        return templates.TemplateResponse(
            request,
            "guidance_claims.html",
            {
                "claims": [],
                "counts": claim_domain.status_counts(session),
                "status_filter": status,
                "error": error.message,
            },
            status_code=400,
        )
    return templates.TemplateResponse(
        request,
        "guidance_claims.html",
        {
            "claims": claims,
            "counts": claim_domain.status_counts(session),
            "status_filter": status,
            "error": None,
        },
    )


@router.get("/guidance-claims/{claim_id}", response_class=HTMLResponse)
def claim_detail(request: Request, claim_id: int, session: SessionDep) -> HTMLResponse:
    detail = claim_domain.get_detail(session, claim_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Guidance claim not found.")
    return templates.TemplateResponse(
        request,
        "guidance_claim_detail.html",
        {"detail": detail, "claim_types": claim_domain.CLAIM_TYPES, "error": None},
    )


@router.post("/guidance-claims/{claim_id}/edit")
def edit_claim(
    request: Request,
    claim_id: int,
    session: SessionDep,
    claim_text: Annotated[str, Form()] = "",
    claim_type: Annotated[str, Form()] = "",
    passage_ids: Annotated[str, Form()] = "",
    source_setting_ids: Annotated[str, Form()] = "",
    canonical_ids: Annotated[str, Form()] = "",
):
    claim = claim_domain.get_claim(session, claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail="Guidance claim not found.")
    try:
        claim_domain.edit_claim(
            session,
            claim,
            claim_text=claim_text,
            claim_type=claim_type,
            passage_ids=_parse_passage_ids(passage_ids),
            source_setting_ids=_parse_passage_ids(source_setting_ids),
            canonical_ids=_parse_canonical_ids(canonical_ids),
        )
    except GuidanceClaimError as error:
        return _detail_error(request, session, claim_id, error.message)
    return RedirectResponse(url=f"/guidance-claims/{claim_id}", status_code=303)


@router.post("/guidance-claims/{claim_id}/review")
def review_claim(
    request: Request,
    claim_id: int,
    session: SessionDep,
    decision: Annotated[str, Form()] = "",
    note: Annotated[str, Form()] = "",
):
    claim = claim_domain.get_claim(session, claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail="Guidance claim not found.")
    try:
        claim_domain.review_claim(session, claim, decision=decision, note=note)
    except GuidanceClaimError as error:
        return _detail_error(request, session, claim_id, error.message)
    return RedirectResponse(url=f"/guidance-claims/{claim_id}", status_code=303)


def _parse_passage_ids(value: str) -> list[int]:
    try:
        return [int(item) for item in value.replace(",", " ").split()]
    except ValueError:
        raise GuidanceClaimError("Evidence passage IDs must be numbers.") from None


def _parse_canonical_ids(value: str) -> list[str]:
    return [item.strip() for item in value.replace(",", "\n").splitlines() if item.strip()]


def _detail_error(
    request: Request,
    session: Session,
    claim_id: int,
    message: str,
) -> HTMLResponse:
    detail = claim_domain.get_detail(session, claim_id)
    if detail is None:
        raise RuntimeError("Guidance claim disappeared during review.")
    return templates.TemplateResponse(
        request,
        "guidance_claim_detail.html",
        {"detail": detail, "claim_types": claim_domain.CLAIM_TYPES, "error": message},
        status_code=400,
    )
