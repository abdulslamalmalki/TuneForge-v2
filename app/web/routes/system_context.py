"""Request-scoped My System page for the AMD AM5 product pilot."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.domain import am5_coverage, am5_question_acceptance
from app.domain import system_context as system_context_domain
from app.domain.errors import SystemContextError
from app.web.templating import templates

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/am5-coverage", response_class=HTMLResponse)
def am5_coverage_page(request: Request, session: SessionDep) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "am5_coverage.html",
        {"report": am5_coverage.build_report(session)},
    )


@router.get("/am5-question-acceptance", response_class=HTMLResponse)
def am5_question_acceptance_page(request: Request, session: SessionDep) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "am5_question_acceptance.html",
        {"report": am5_question_acceptance.build_report(session)},
    )


@router.get("/system-context", response_class=HTMLResponse)
def system_context_page(
    request: Request,
    session: SessionDep,
    motherboard_model_id: str = "",
    cpu_model_id: str = "",
    bios_version: str = "",
    use_case: str = "",
) -> HTMLResponse:
    context = None
    error = None
    status_code = 200
    try:
        context = system_context_domain.resolve_optional(
            session,
            motherboard_model_id=motherboard_model_id,
            cpu_model_id=cpu_model_id,
            bios_version=bios_version,
            use_case=use_case,
        )
    except SystemContextError as exception:
        error = exception.message
        status_code = 400
    return templates.TemplateResponse(
        request,
        "system_context.html",
        {
            "motherboard_model_id": motherboard_model_id,
            "cpu_model_id": cpu_model_id,
            "bios_version": bios_version,
            "use_case": use_case,
            "use_cases": system_context_domain.USE_CASE_LABELS.items(),
            "motherboards": system_context_domain.list_pilot_motherboards(session),
            "cpus": system_context_domain.list_pilot_cpus(session),
            "system_context": context,
            "error": error,
        },
        status_code=status_code,
    )
