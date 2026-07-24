"""Read-only TuneForge admin dashboard."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.domain import admin_dashboard, release_readiness
from app.web.templating import templates

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/admin", response_class=HTMLResponse)
def dashboard(request: Request, session: SessionDep) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "admin_dashboard.html",
        {
            "dashboard": admin_dashboard.build_dashboard(session),
            "release": release_readiness.build_report(session),
        },
    )


@router.get("/admin/release-readiness", response_class=HTMLResponse)
def release_readiness_page(request: Request, session: SessionDep) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "release_readiness.html",
        {"report": release_readiness.build_report(session)},
    )
