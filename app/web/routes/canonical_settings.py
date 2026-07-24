"""Server-rendered canonical-setting catalog."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.domain import canonical_settings as canonical_domain
from app.domain import canonical_suggestions as suggestion_domain
from app.domain.errors import CanonicalSettingError
from app.normalization.canonical_settings import RISK_LEVELS
from app.normalization.cpu import CPU_MODELS
from app.web.templating import templates

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/canonical-settings", response_class=HTMLResponse)
def catalog(request: Request, session: SessionDep) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "canonical_settings.html",
        {
            "catalog": canonical_domain.list_catalog(session),
            "error": None,
            "form": {},
            "cpu_models": CPU_MODELS,
        },
    )


@router.post("/canonical-settings")
def create_identity(
    request: Request,
    session: SessionDep,
    name: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
    aliases: Annotated[str, Form()] = "",
):
    try:
        identity = canonical_domain.create_identity(
            session,
            name=name,
            description=description,
            aliases=tuple(item.strip() for item in aliases.splitlines() if item.strip()),
        )
    except CanonicalSettingError as error:
        return templates.TemplateResponse(
            request,
            "canonical_settings.html",
            {
                "catalog": canonical_domain.list_catalog(session),
                "error": error.message,
                "form": {
                    "name": name,
                    "description": description,
                    "aliases": aliases,
                },
                "cpu_models": CPU_MODELS,
            },
            status_code=400,
        )
    return RedirectResponse(
        url=f"/canonical-settings/{identity.canonical_id}",
        status_code=303,
    )


@router.get("/canonical-settings/{canonical_id}", response_class=HTMLResponse)
def detail(request: Request, canonical_id: str, session: SessionDep) -> HTMLResponse:
    value = canonical_domain.get_detail(session, canonical_id)
    if value is None:
        raise HTTPException(status_code=404, detail="Canonical setting not found.")
    return templates.TemplateResponse(
        request,
        "canonical_setting_detail.html",
        {
            "detail": value,
            "categories": canonical_domain.list_categories(session),
            "risk_levels": RISK_LEVELS,
            "error": None,
            "notice": request.query_params.get("notice", ""),
        },
    )


@router.post("/canonical-settings/{canonical_id}/classification")
def classify(
    request: Request,
    canonical_id: str,
    session: SessionDep,
    category_id: Annotated[str, Form()] = "",
    risk_level: Annotated[str, Form()] = "",
    risk_rationale: Annotated[str, Form()] = "",
):
    value = canonical_domain.get_detail(session, canonical_id)
    if value is None:
        raise HTTPException(status_code=404, detail="Canonical setting not found.")
    try:
        canonical_domain.classify_identity(
            session,
            value.identity,
            category_id=category_id,
            risk_level=risk_level,
            risk_rationale=risk_rationale,
        )
    except CanonicalSettingError as error:
        return templates.TemplateResponse(
            request,
            "canonical_setting_detail.html",
            {
                "detail": canonical_domain.get_detail(session, canonical_id),
                "categories": canonical_domain.list_categories(session),
                "risk_levels": RISK_LEVELS,
                "error": error.message,
                "notice": "",
            },
            status_code=400,
        )
    return RedirectResponse(
        url=f"/canonical-settings/{canonical_id}",
        status_code=303,
    )


@router.post("/canonical-settings/{canonical_id}/deprecate")
def deprecate(
    request: Request,
    canonical_id: str,
    session: SessionDep,
    reason: Annotated[str, Form()] = "",
):
    return _change_identity_status(
        request,
        session,
        canonical_id=canonical_id,
        status=canonical_domain.IDENTITY_DEPRECATED,
        reason=reason,
    )


@router.post("/canonical-settings/{canonical_id}/reactivate")
def reactivate(
    request: Request,
    canonical_id: str,
    session: SessionDep,
    reason: Annotated[str, Form()] = "",
):
    return _change_identity_status(
        request,
        session,
        canonical_id=canonical_id,
        status=canonical_domain.IDENTITY_ACTIVE,
        reason=reason,
    )


@router.get("/canonical-suggestions", response_class=HTMLResponse)
def suggestion_queue(
    request: Request,
    session: SessionDep,
    canonical_id: str = "",
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "canonical_suggestions.html",
        {
            "queue": suggestion_domain.list_queue(
                session,
                canonical_id=canonical_id,
            ),
            "counts": suggestion_domain.status_counts(session),
            "canonical_id": canonical_id,
            "catalog": canonical_domain.list_catalog(session),
            "error": None,
        },
    )


@router.post("/canonical-suggestions/refresh")
def refresh_suggestions(session: SessionDep):
    suggestion_domain.refresh_suggestions(session)
    return RedirectResponse(url="/canonical-suggestions", status_code=303)


@router.post("/canonical-suggestions/{suggestion_id}/accept")
def accept_suggestion(
    request: Request,
    suggestion_id: int,
    session: SessionDep,
    note: Annotated[str, Form()] = "",
):
    try:
        suggestion_domain.accept_suggestion(
            session,
            suggestion_id,
            note=note,
        )
    except CanonicalSettingError as error:
        return _suggestion_error(request, session, error.message)
    return RedirectResponse(url="/canonical-suggestions", status_code=303)


@router.post("/canonical-suggestions/{suggestion_id}/dismiss")
def dismiss_suggestion(
    request: Request,
    suggestion_id: int,
    session: SessionDep,
    reason: Annotated[str, Form()] = "",
):
    try:
        suggestion_domain.dismiss_suggestion(
            session,
            suggestion_id,
            reason=reason,
        )
    except CanonicalSettingError as error:
        return _suggestion_error(request, session, error.message)
    return RedirectResponse(url="/canonical-suggestions", status_code=303)


def _suggestion_error(
    request: Request,
    session: Session,
    message: str,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "canonical_suggestions.html",
        {
            "queue": suggestion_domain.list_queue(session),
            "counts": suggestion_domain.status_counts(session),
            "canonical_id": "",
            "catalog": canonical_domain.list_catalog(session),
            "error": message,
        },
        status_code=400,
    )


def _change_identity_status(
    request: Request,
    session: Session,
    *,
    canonical_id: str,
    status: str,
    reason: str,
):
    value = canonical_domain.get_detail(session, canonical_id)
    if value is None:
        raise HTTPException(status_code=404, detail="Canonical setting not found.")
    try:
        canonical_domain.set_identity_status(
            session,
            value.identity,
            status=status,
            reason=reason,
        )
    except CanonicalSettingError as error:
        return templates.TemplateResponse(
            request,
            "canonical_setting_detail.html",
            {
                "detail": canonical_domain.get_detail(session, canonical_id),
                "categories": canonical_domain.list_categories(session),
                "risk_levels": RISK_LEVELS,
                "error": error.message,
                "notice": "",
            },
            status_code=400,
        )
    action = "deprecated" if status == canonical_domain.IDENTITY_DEPRECATED else "reactivated"
    return RedirectResponse(
        url=f"/canonical-settings/{canonical_id}?notice=Canonical+identity+{action}.",
        status_code=303,
    )
