"""Server-rendered extraction acceptance workflow."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.domain import acceptance as acceptance_domain
from app.domain import manuals as manuals_domain
from app.domain.errors import AcceptanceError
from app.web.templating import templates

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/manuals/{manual_id}/acceptance", response_class=HTMLResponse)
def acceptance_report(request: Request, manual_id: int, session: SessionDep) -> HTMLResponse:
    manual = _manual_or_404(session, manual_id)
    return _render(request, session, manual)


@router.post("/manuals/{manual_id}/settings/{setting_id}/page-reference")
def review_page_reference(
    request: Request,
    manual_id: int,
    setting_id: int,
    session: SessionDep,
    page_number: Annotated[int, Form()] = 0,
):
    manual = _manual_or_404(session, manual_id)
    try:
        acceptance_domain.review_page_reference(
            session, manual, setting_id, page_number=page_number
        )
    except AcceptanceError as error:
        return _render(request, session, manual, error=error.message, status_code=400)
    return RedirectResponse(url=f"/manuals/{manual.id}/acceptance", status_code=303)


@router.post("/manuals/{manual_id}/missed-settings")
def add_missed_setting(
    request: Request,
    manual_id: int,
    session: SessionDep,
    name: Annotated[str, Form()] = "",
    page_number: Annotated[int, Form()] = 0,
    description: Annotated[str, Form()] = "",
    note: Annotated[str, Form()] = "",
):
    manual = _manual_or_404(session, manual_id)
    form = {
        "name": name,
        "page_number": page_number,
        "description": description,
        "note": note,
    }
    try:
        acceptance_domain.add_missed_setting(session, manual, **form)
    except AcceptanceError as error:
        return _render(request, session, manual, error=error.message, form=form, status_code=400)
    return RedirectResponse(url=f"/manuals/{manual.id}/acceptance", status_code=303)


@router.post("/manuals/{manual_id}/missed-settings/{missed_setting_id}/dismiss")
def dismiss_missed_setting(
    request: Request,
    manual_id: int,
    missed_setting_id: int,
    session: SessionDep,
    reason: Annotated[str, Form()] = "",
):
    manual = _manual_or_404(session, manual_id)
    try:
        acceptance_domain.dismiss_missed_setting(session, manual, missed_setting_id, reason=reason)
    except AcceptanceError as error:
        return _render(request, session, manual, error=error.message, status_code=400)
    return RedirectResponse(url=f"/manuals/{manual.id}/acceptance", status_code=303)


@router.post("/manuals/{manual_id}/acceptance/certify")
def certify_acceptance(
    request: Request,
    manual_id: int,
    session: SessionDep,
    notes: Annotated[str, Form()] = "",
):
    manual = _manual_or_404(session, manual_id)
    try:
        acceptance_domain.certify_ground_truth(session, manual, notes=notes)
    except AcceptanceError as error:
        return _render(request, session, manual, error=error.message, status_code=400)
    return RedirectResponse(url=f"/manuals/{manual.id}/acceptance", status_code=303)


def _manual_or_404(session: Session, manual_id: int):
    manual = manuals_domain.get_manual(session, manual_id)
    if manual is None:
        raise HTTPException(status_code=404, detail="Manual not found.")
    return manual


def _render(
    request: Request,
    session: Session,
    manual,
    *,
    error: str | None = None,
    form: dict | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    context = {
        "manual": manual,
        "report": acceptance_domain.build_report(session, manual),
        "missed_settings": acceptance_domain.list_missed_settings(session, manual.id),
        "page_references": acceptance_domain.list_page_references(session, manual.id),
        "error": error,
        "form": form or {},
    }
    return templates.TemplateResponse(
        request, "acceptance_report.html", context, status_code=status_code
    )
