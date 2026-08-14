"""Protected human-review boundary for official live-research leads."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.domain import research_evidence_intake
from app.domain.errors import GuidanceClaimError
from app.research.trusted_web import TrustedWebResearchError
from app.web.templating import templates

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/trusted-web-research", response_class=HTMLResponse)
def research_queue(
    request: Request,
    session: SessionDep,
    status: str = "new",
    publisher: str = "",
) -> HTMLResponse:
    try:
        rows = research_evidence_intake.list_results(session, status=status, publisher_id=publisher)
    except TrustedWebResearchError as error:
        rows = ()
        message = str(error)
    else:
        message = None
    return templates.TemplateResponse(
        request,
        "trusted_web_research.html",
        {
            "rows": rows,
            "counts": research_evidence_intake.status_counts(session),
            "status_filter": status,
            "publisher_filter": publisher,
            "error": message,
        },
        status_code=400 if message else 200,
    )


@router.get("/trusted-web-research/results/{result_id}", response_class=HTMLResponse)
def research_detail(request: Request, result_id: int, session: SessionDep) -> HTMLResponse:
    return _detail_response(request, session, result_id)


@router.post("/trusted-web-research/results/{result_id}/triage")
def triage_result(
    request: Request,
    result_id: int,
    session: SessionDep,
    status: Annotated[str, Form()] = "",
    note: Annotated[str, Form()] = "",
    setting_id: Annotated[str, Form()] = "",
    canonical_id: Annotated[str, Form()] = "",
    proposed_claim_text: Annotated[str, Form()] = "",
    proposed_claim_type: Annotated[str, Form()] = "",
):
    try:
        parsed_setting_id = int(setting_id) if setting_id.strip() else None
        research_evidence_intake.triage_result(
            session,
            result_id,
            status=status,
            note=note,
            setting_id=parsed_setting_id,
            canonical_id=canonical_id,
            proposed_claim_text=proposed_claim_text,
            proposed_claim_type=proposed_claim_type,
        )
    except (TrustedWebResearchError, ValueError) as error:
        message = str(error) or "Source setting ID must be a number."
        return _detail_response(request, session, result_id, error=message)
    return RedirectResponse(f"/trusted-web-research/results/{result_id}", status_code=303)


@router.post("/trusted-web-research/results/{result_id}/link-evidence")
def link_evidence(
    request: Request,
    result_id: int,
    session: SessionDep,
    evidence_source_id: Annotated[int, Form()],
    evidence_passage_id: Annotated[int, Form()],
    note: Annotated[str, Form()] = "",
):
    try:
        research_evidence_intake.link_reviewed_evidence(
            session,
            result_id,
            evidence_source_id=evidence_source_id,
            evidence_passage_id=evidence_passage_id,
            note=note,
        )
    except TrustedWebResearchError as error:
        return _detail_response(request, session, result_id, error=str(error))
    return RedirectResponse(f"/trusted-web-research/results/{result_id}", status_code=303)


@router.post("/trusted-web-research/results/{result_id}/create-claim")
def create_claim(
    request: Request,
    result_id: int,
    session: SessionDep,
    note: Annotated[str, Form()] = "",
):
    try:
        claim = research_evidence_intake.create_draft_claim(session, result_id, note=note)
    except (TrustedWebResearchError, GuidanceClaimError) as error:
        return _detail_response(request, session, result_id, error=str(error))
    return RedirectResponse(f"/guidance-claims/{claim.id}", status_code=303)


@router.post("/trusted-web-research/results/{result_id}/attach-claim")
def attach_claim(
    request: Request,
    result_id: int,
    session: SessionDep,
    claim_id: Annotated[int, Form()],
    note: Annotated[str, Form()] = "",
):
    try:
        claim = research_evidence_intake.attach_to_draft_claim(
            session, result_id, claim_id=claim_id, note=note
        )
    except TrustedWebResearchError as error:
        return _detail_response(request, session, result_id, error=str(error))
    return RedirectResponse(f"/guidance-claims/{claim.id}", status_code=303)


def _detail_response(
    request: Request,
    session: Session,
    result_id: int,
    *,
    error: str | None = None,
) -> HTMLResponse:
    detail = research_evidence_intake.get_detail(session, result_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Research result not found.")
    return templates.TemplateResponse(
        request,
        "trusted_web_research_detail.html",
        {
            "detail": detail,
            "trust_tier": research_evidence_intake.TRUST_TIER,
            "triage_statuses": sorted(research_evidence_intake.TRIAGE_STATUSES),
            "claim_types": research_evidence_intake.guidance_claims.CLAIM_TYPES,
            "evidence_options": research_evidence_intake.reviewed_evidence_options(
                session, detail.result.publisher_id
            ),
            "error": error,
        },
        status_code=400 if error else 200,
    )
