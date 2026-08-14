"""Simple public TuneForge experience over reviewed citation-safe knowledge."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.domain import (
    assistant_retrieval,
    guidance_read,
    guided_answers,
    interface_lookup,
    question_routing,
)
from app.domain import system_context as system_context_domain
from app.domain.errors import GuidanceReadError, SystemContextError
from app.research import trusted_web as trusted_web_research
from app.web.templating import templates

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/", response_class=RedirectResponse)
def public_home(request: Request) -> RedirectResponse:
    """The BIOS Settings Library is TuneForge's public homepage."""
    destination = "/library"
    if request.url.query:
        destination += f"?{request.url.query}"
    return RedirectResponse(destination, status_code=303)


@router.get("/answer", response_class=HTMLResponse)
def public_answer(
    request: Request,
    session: SessionDep,
    setting: str = "",
    motherboard_model_id: str = "",
    cpu_model_id: str = "",
    bios_version: str = "",
    trusted_web: str = "",
) -> HTMLResponse:
    context = None
    presentation = None
    routing = None
    error = None
    status_code = 200
    library_matches = None
    web_research = None
    try:
        context = system_context_domain.resolve_optional(
            session,
            motherboard_model_id=motherboard_model_id,
            cpu_model_id=cpu_model_id,
            bios_version=bios_version,
        )
        if setting.strip():
            routing = question_routing.route(setting)
            if routing.kind == question_routing.KIND_GUIDED_QUESTION:
                presentation = guided_answers.build_answer(
                    session,
                    routing.question_id or "",
                    system_context=context,
                ).presentation
            else:
                presentation = interface_lookup.build_presentation(
                    session,
                    routing.routed_value,
                    system_context=context,
                    motherboard_model_id=motherboard_model_id,
                    cpu_model_id=cpu_model_id,
                    gap_query=routing.original_query,
                )
            if presentation.outcome != "supported":
                library_matches = assistant_retrieval.find_approved_matches(
                    session,
                    routing.original_query,
                    setting_hint=(
                        routing.routed_value
                        if routing.kind == question_routing.KIND_SETTING_LOOKUP
                        else ""
                    ),
                )
                if trusted_web == "1":
                    web_research = trusted_web_research.research(
                        session, routing.original_query
                    )
    except SystemContextError as exception:
        error = exception.message
        status_code = 400
    except GuidanceReadError as exception:
        error = exception.message
        status_code = 400
    except question_routing.QuestionRoutingError as exception:
        error = str(exception)
        status_code = 400
    return templates.TemplateResponse(
        request,
        "public_answer.html",
        {
            "setting": setting,
            "motherboard_model_id": motherboard_model_id,
            "cpu_model_id": cpu_model_id,
            "bios_version": bios_version,
            "system_context": context,
            "presentation": presentation,
            "routing": routing,
            "covered_settings": guidance_read.list_coverage(session, approved_only=True),
            "guided_questions": guided_answers.list_questions(session),
            "error": error,
            "trusted_web": trusted_web == "1",
            "library_matches": library_matches,
            "web_research": web_research,
        },
        status_code=status_code,
    )
