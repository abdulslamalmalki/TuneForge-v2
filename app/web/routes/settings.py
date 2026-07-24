"""Setting detail page and review actions (FR-18 to FR-22, plan section 12).

All review rules live in the domain state machine; these routes only
translate forms to domain calls. A ReviewActionError re-renders the detail
page with the inline message at 400; success redirects (PRG) with a fixed
`done` key so only server-defined text is displayed. The browser's built-in
PDF viewer is the manual viewer — the page link is the served PDF plus the
standard #page=N fragment.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.db.models import Setting
from app.db.session import get_session
from app.domain import canonical_settings as canonical_domain
from app.domain import hardware
from app.domain import manuals as manuals_domain
from app.domain import review as review_domain
from app.domain.errors import CanonicalSettingError, ReviewActionError
from app.web.templating import templates

router = APIRouter()

SessionDep = Annotated[Session, Depends(get_session)]

DONE_MESSAGES = {
    "edited": "Changes saved.",
    "approved": "Setting approved.",
    "rejected": "Setting rejected.",
    "mapped": "Canonical mapping saved.",
    "unmapped": "Canonical mapping removed.",
    "reopened": "Setting reopened — it is a draft again.",
}


@router.get("/settings/{setting_id}", response_class=HTMLResponse)
def setting_detail(
    request: Request, setting_id: int, session: SessionDep, done: str = ""
) -> HTMLResponse:
    setting = _get_setting_or_404(session, setting_id)
    return _render_detail(request, session, setting, message=DONE_MESSAGES.get(done))


@router.post("/settings/{setting_id}/edit")
def edit_setting(
    request: Request,
    setting_id: int,
    session: SessionDep,
    name: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
):
    setting = _get_setting_or_404(session, setting_id)
    try:
        review_domain.edit_draft_setting(session, setting, name=name, description=description)
    except ReviewActionError as error:
        return _render_detail(request, session, setting, error=error.message, status_code=400)
    return _done_redirect(setting, "edited")


@router.post("/settings/{setting_id}/approve")
def approve_setting(
    request: Request,
    setting_id: int,
    session: SessionDep,
    note: Annotated[str, Form()] = "",
):
    setting = _get_setting_or_404(session, setting_id)
    try:
        review_domain.approve_setting(session, setting, note=note)
    except ReviewActionError as error:
        return _render_detail(request, session, setting, error=error.message, status_code=400)
    return _done_redirect(setting, "approved")


@router.post("/settings/{setting_id}/reject")
def reject_setting(
    request: Request,
    setting_id: int,
    session: SessionDep,
    reason: Annotated[str, Form()] = "",
):
    setting = _get_setting_or_404(session, setting_id)
    try:
        review_domain.reject_setting(session, setting, reason)
    except ReviewActionError as error:
        return _render_detail(request, session, setting, error=error.message, status_code=400)
    return _done_redirect(setting, "rejected")


@router.post("/settings/{setting_id}/reopen")
def reopen_setting(request: Request, setting_id: int, session: SessionDep):
    setting = _get_setting_or_404(session, setting_id)
    try:
        review_domain.reopen_setting(session, setting)
    except ReviewActionError as error:
        return _render_detail(request, session, setting, error=error.message, status_code=400)
    return _done_redirect(setting, "reopened")


@router.post("/settings/{setting_id}/canonical")
def map_canonical_setting(
    request: Request,
    setting_id: int,
    session: SessionDep,
    canonical_id: Annotated[str, Form()] = "",
    note: Annotated[str, Form()] = "",
):
    setting = _get_setting_or_404(session, setting_id)
    try:
        canonical_domain.map_setting(
            session,
            setting,
            canonical_id=canonical_id,
            note=note,
        )
    except CanonicalSettingError as error:
        return _render_detail(request, session, setting, error=error.message, status_code=400)
    return _done_redirect(setting, "mapped")


@router.post("/settings/{setting_id}/canonical/unmap")
def unmap_canonical_setting(
    request: Request,
    setting_id: int,
    session: SessionDep,
    reason: Annotated[str, Form()] = "",
):
    setting = _get_setting_or_404(session, setting_id)
    try:
        canonical_domain.unmap_setting(session, setting, reason=reason)
    except CanonicalSettingError as error:
        return _render_detail(request, session, setting, error=error.message, status_code=400)
    return _done_redirect(setting, "unmapped")


def _get_setting_or_404(session: Session, setting_id: int) -> Setting:
    setting = review_domain.get_setting(session, setting_id)
    if setting is None:
        raise HTTPException(status_code=404, detail="Setting not found.")
    return setting


def _done_redirect(setting: Setting, done: str) -> RedirectResponse:
    return RedirectResponse(url=f"/settings/{setting.id}?done={done}", status_code=303)


def _render_detail(
    request: Request,
    session: Session,
    setting: Setting,
    *,
    message: str | None = None,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    context = {
        "setting": setting,
        "manual": manuals_domain.get_manual(session, setting.manual_id),
        "applicability": hardware.get_manual_applicability(session, setting.manual_id),
        "effective_name": review_domain.effective_name(setting),
        "effective_description": review_domain.effective_description(setting),
        "events": review_domain.list_review_events(session, setting.id),
        "canonical_mapping": canonical_domain.get_mapping(session, setting.id),
        "canonical_suggestions": canonical_domain.suggest_for_setting(session, setting),
        "canonical_catalog": canonical_domain.list_catalog(session),
        "canonical_events": canonical_domain.list_mapping_events(session, setting.id),
        "message": message,
        "error": error,
    }
    return templates.TemplateResponse(
        request, "setting_detail.html", context, status_code=status_code
    )
