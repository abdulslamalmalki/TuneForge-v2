"""Reviewer UI for refusal demand and official referral pointers."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.domain import canonical_settings, evidence_sources, refusal_gaps
from app.domain.errors import ReferralPointerError
from app.web.templating import templates

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/refusal-gaps", response_class=HTMLResponse)
def refusal_gap_page(request: Request, session: SessionDep) -> HTMLResponse:
    return _render_page(request, session)


@router.post("/referral-pointers")
def register_referral_pointer(
    request: Request,
    session: SessionDep,
    publisher_id: Annotated[str, Form()] = "",
    title: Annotated[str, Form()] = "",
    source_url: Annotated[str, Form()] = "",
    document_id: Annotated[str, Form()] = "",
    document_version: Annotated[str, Form()] = "",
    canonical_ids: Annotated[str, Form()] = "",
    review_note: Annotated[str, Form()] = "",
) -> Response:
    try:
        refusal_gaps.register_referral_pointer(
            session,
            publisher_id=publisher_id,
            title=title,
            source_url=source_url,
            document_id=document_id,
            document_version=document_version,
            canonical_ids=[item.strip() for item in canonical_ids.split(",")],
            review_note=review_note,
        )
    except ReferralPointerError as error:
        return _render_page(request, session, error=error.message, status_code=400)
    return RedirectResponse("/refusal-gaps", status_code=303)


@router.post("/referral-pointers/{pointer_id}/retire")
def retire_referral_pointer(
    pointer_id: int,
    request: Request,
    session: SessionDep,
    review_note: Annotated[str, Form()] = "",
) -> Response:
    try:
        refusal_gaps.retire_referral_pointer(
            session,
            pointer_id,
            review_note=review_note,
        )
    except ReferralPointerError as error:
        return _render_page(request, session, error=error.message, status_code=400)
    return RedirectResponse("/refusal-gaps", status_code=303)


def _render_page(
    request: Request,
    session: Session,
    *,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "refusal_gaps.html",
        {
            "worklist": refusal_gaps.list_gap_worklist(session),
            "events": refusal_gaps.list_lookup_gaps(session, limit=50),
            "pointers": refusal_gaps.list_referral_pointers(session),
            "publishers": tuple(
                (publisher_id, details[0])
                for publisher_id, details in evidence_sources.PUBLISHERS.items()
            ),
            "canonical_settings": canonical_settings.list_catalog(session),
            "error": error,
        },
        status_code=status_code,
    )
