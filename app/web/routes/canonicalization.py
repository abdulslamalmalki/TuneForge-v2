"""Protected reviewer interface for the canonicalization backlog."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.domain import canonicalization
from app.domain.errors import CanonicalSettingError
from app.normalization.vendors import VENDORS
from app.web.templating import templates

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/canonicalization", response_class=HTMLResponse)
def backlog(
    request: Request,
    session: SessionDep,
    query: str = "",
    vendor_id: str = "",
    page: int = 1,
) -> HTMLResponse:
    return _render(
        request,
        session,
        query=query,
        vendor_id=vendor_id,
        page=page,
    )


@router.post("/canonicalization/review", response_class=HTMLResponse)
def run_reviewer(
    request: Request,
    session: SessionDep,
) -> HTMLResponse:
    report = canonicalization.run_reviewer(session)
    return _render(
        request,
        session,
        report=report,
        notice=(
            f"Reviewed {report.evaluated_count:,} records. "
            f"{len(report.auto_approved_mappings):,} automatic mappings passed "
            "the 99.5% threshold."
        ),
    )


@router.post("/canonicalization/settings/{setting_id}/reverse")
def reverse_mapping(
    request: Request,
    setting_id: int,
    session: SessionDep,
    reason: Annotated[str, Form()] = "",
):
    try:
        canonicalization.reverse_automatic_mapping(
            session,
            setting_id,
            reason=reason,
        )
    except CanonicalSettingError as error:
        return _render(request, session, error=error.message, status_code=400)
    return RedirectResponse(
        url="/canonicalization?notice=Automatic+mapping+reversed.",
        status_code=303,
    )


def _render(
    request: Request,
    session: Session,
    *,
    query: str = "",
    vendor_id: str = "",
    page: int = 1,
    report: canonicalization.CanonicalizationRunReport | None = None,
    notice: str = "",
    error: str = "",
    status_code: int = 200,
) -> HTMLResponse:
    page_result = canonicalization.backlog_page(
        session,
        query=query,
        vendor_id=vendor_id,
        page=page,
    )
    return templates.TemplateResponse(
        request,
        "canonicalization_backlog.html",
        {
            "backlog": page_result,
            "query": query,
            "vendor_id": vendor_id,
            "vendors": VENDORS,
            "threshold": canonicalization.CONFIDENCE_THRESHOLD_BP / 100,
            "engine_version": canonicalization.ENGINE_VERSION,
            "report": report,
            "notice": notice or request.query_params.get("notice", ""),
            "error": error,
        },
        status_code=status_code,
    )
