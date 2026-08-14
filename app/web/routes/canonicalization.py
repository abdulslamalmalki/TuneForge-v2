"""Protected reviewer interface for the canonicalization backlog."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.domain import (
    canonical_backlog_assessments,
    canonical_identity_review,
    canonicalization,
)
from app.domain.errors import CanonicalSettingError
from app.normalization.vendors import VENDORS
from app.web.templating import templates

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/canonicalization/assessments", response_class=HTMLResponse)
def assessment_queue(
    request: Request,
    session: SessionDep,
    outcome: str = "",
    vendor_id: str = "",
    review_status: str = "",
) -> HTMLResponse:
    try:
        rows = canonical_backlog_assessments.list_assessments(
            session,
            outcome=outcome,
            vendor_id=vendor_id,
            review_status=review_status,
        )
    except CanonicalSettingError as error:
        rows = ()
        message = error.message
    else:
        message = ""
    return templates.TemplateResponse(
        request,
        "canonical_assessments.html",
        {
            "rows": rows,
            "metrics": canonical_backlog_assessments.metrics(session),
            "outcomes": canonical_backlog_assessments.OUTCOMES,
            "outcome_filter": outcome,
            "vendor_id": vendor_id,
            "vendors": VENDORS,
            "review_status": review_status,
            "error": message,
        },
        status_code=400 if message else 200,
    )


@router.get(
    "/canonicalization/assessments/{setting_id}", response_class=HTMLResponse
)
def assessment_detail(
    request: Request, setting_id: int, session: SessionDep
) -> HTMLResponse:
    return _assessment_detail_response(request, session, setting_id)


@router.post("/canonicalization/assessments/{setting_id}/confirm")
def confirm_assessment(
    request: Request,
    setting_id: int,
    session: SessionDep,
    outcome: Annotated[str, Form()] = "",
    candidate_canonical_id: Annotated[str, Form()] = "",
    assessment_method: Annotated[str, Form()] = "human_review",
    note: Annotated[str, Form()] = "",
):
    try:
        canonical_backlog_assessments.confirm_assessment(
            session,
            setting_id,
            outcome=outcome,
            candidate_canonical_id=candidate_canonical_id,
            assessment_method=assessment_method,
            note=note,
        )
    except CanonicalSettingError as error:
        return _assessment_detail_response(
            request, session, setting_id, error=error.message
        )
    return RedirectResponse(
        f"/canonicalization/assessments/{setting_id}", status_code=303
    )


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


@router.post("/canonicalization/settings/{setting_id}/menu-context")
def review_menu_context(
    request: Request,
    setting_id: int,
    session: SessionDep,
    menu_path: Annotated[str, Form()] = "",
    source_page_number: Annotated[int, Form()] = 1,
    evidence_excerpt: Annotated[str, Form()] = "",
    note: Annotated[str, Form()] = "",
):
    try:
        canonical_identity_review.review_menu_context(
            session,
            setting_id,
            menu_path=menu_path,
            source_page_number=source_page_number,
            evidence_excerpt=evidence_excerpt,
            note=note,
        )
    except CanonicalSettingError as error:
        return _render(request, session, error=error.message, status_code=400)
    return RedirectResponse(
        url="/canonicalization?notice=Reviewed+menu+context+saved.",
        status_code=303,
    )


@router.post("/canonicalization/settings/{setting_id}/menu-context/revoke")
def revoke_menu_context(
    request: Request,
    setting_id: int,
    session: SessionDep,
    reason: Annotated[str, Form()] = "",
):
    try:
        canonical_identity_review.revoke_menu_context(
            session,
            setting_id,
            reason=reason,
        )
    except CanonicalSettingError as error:
        return _render(request, session, error=error.message, status_code=400)
    return RedirectResponse(
        url="/canonicalization?notice=Reviewed+menu+context+revoked.",
        status_code=303,
    )


@router.post("/canonicalization/identity-proposals/{proposal_id}/approve")
def approve_identity_proposal(
    request: Request,
    proposal_id: int,
    session: SessionDep,
    note: Annotated[str, Form()] = "",
):
    try:
        identity = canonical_identity_review.approve_identity_proposal(
            session,
            proposal_id,
            note=note,
        )
    except CanonicalSettingError as error:
        return _render(request, session, error=error.message, status_code=400)
    return RedirectResponse(
        url=(
            f"/canonical-settings/{identity.canonical_id}"
            "?notice=Canonical+identity+proposal+approved."
        ),
        status_code=303,
    )


@router.post("/canonicalization/identity-proposals/{proposal_id}/reject")
def reject_identity_proposal(
    request: Request,
    proposal_id: int,
    session: SessionDep,
    reason: Annotated[str, Form()] = "",
):
    try:
        canonical_identity_review.reject_identity_proposal(
            session,
            proposal_id,
            reason=reason,
        )
    except CanonicalSettingError as error:
        return _render(request, session, error=error.message, status_code=400)
    return RedirectResponse(
        url="/canonicalization?notice=Canonical+identity+proposal+rejected.",
        status_code=303,
    )


@router.post("/canonicalization/identity-proposals/{proposal_id}/reopen")
def reopen_identity_proposal(
    request: Request,
    proposal_id: int,
    session: SessionDep,
    reason: Annotated[str, Form()] = "",
):
    try:
        canonical_identity_review.reopen_identity_proposal(
            session,
            proposal_id,
            reason=reason,
        )
    except CanonicalSettingError as error:
        return _render(request, session, error=error.message, status_code=400)
    return RedirectResponse(
        url="/canonicalization?notice=Canonical+identity+proposal+reopened.",
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
    proposals = canonical_identity_review.list_identity_proposals(session)
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
            "identity_proposals": proposals,
            "notice": notice or request.query_params.get("notice", ""),
            "error": error,
        },
        status_code=status_code,
    )


def _assessment_detail_response(
    request: Request,
    session: Session,
    setting_id: int,
    *,
    error: str = "",
) -> HTMLResponse:
    detail = canonical_backlog_assessments.get_detail(session, setting_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Canonical assessment not found.")
    return templates.TemplateResponse(
        request,
        "canonical_assessment_detail.html",
        {
            "detail": detail,
            "outcomes": canonical_backlog_assessments.OUTCOMES,
            "error": error,
        },
        status_code=400 if error else 200,
    )
