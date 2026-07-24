"""Server-rendered citation-first setting explanation."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.domain import guidance_read, refusal_gaps
from app.domain import system_context as system_context_domain
from app.domain.errors import GuidanceReadError, SystemContextError
from app.web.templating import templates

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/guidance", response_class=HTMLResponse)
def guidance_page(
    request: Request,
    session: SessionDep,
    setting: str = "",
    motherboard_model_id: str = "",
    cpu_model_id: str = "",
) -> HTMLResponse:
    explanation = None
    error = None
    status_code = 200
    context = None
    referral_pointers = []
    try:
        context = system_context_domain.resolve_optional(
            session,
            motherboard_model_id=motherboard_model_id,
            cpu_model_id=cpu_model_id,
        )
        if setting.strip():
            explanation = guidance_read.explain_setting(
                session,
                setting,
                system_context=context,
            )
    except SystemContextError as exception:
        error = exception.message
        status_code = 400
    except GuidanceReadError as exception:
        error = exception.message
        status_code = 404 if error.startswith("No exact") else 400
        if status_code == 404:
            gap = refusal_gaps.try_record_lookup_gap(
                session,
                surface="guidance",
                reason="no_exact_guidance_match",
                query=setting,
                context={
                    "model_id": motherboard_model_id,
                    "cpu_model_id": cpu_model_id,
                },
            )
            matches = (
                gap.nearest_matches
                if gap
                else refusal_gaps.nearest_canonical_matches(session, setting)
            )
            referral_pointers = refusal_gaps.referrals_for_matches(session, matches)
    if explanation is not None and not explanation.facts:
        gap = refusal_gaps.try_record_lookup_gap(
            session,
            surface="guidance",
            reason="no_accepted_guidance_claims",
            query=setting,
            context={
                "canonical_id": explanation.canonical_id,
                "model_id": motherboard_model_id,
                "cpu_model_id": cpu_model_id,
            },
        )
        referral_pointers = refusal_gaps.referrals_for_matches(
            session,
            gap.nearest_matches if gap else (),
            canonical_id=explanation.canonical_id,
        )
    groups = []
    if explanation is not None:
        for claim_type in guidance_read.CLAIM_TYPE_ORDER:
            facts = tuple(fact for fact in explanation.facts if fact.claim_type == claim_type)
            if facts:
                groups.append((claim_type, facts))
    return templates.TemplateResponse(
        request,
        "guidance.html",
        {
            "setting": setting,
            "motherboard_model_id": motherboard_model_id,
            "cpu_model_id": cpu_model_id,
            "system_context": context,
            "explanation": explanation,
            "groups": groups,
            "coverage": guidance_read.list_coverage(session, approved_only=True),
            "error": error,
            "referral_pointers": referral_pointers,
        },
        status_code=status_code,
    )
