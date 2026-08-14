"""Simple public TuneForge experience over reviewed citation-safe knowledge."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.domain import (
    assistant_retrieval,
    guidance_read,
    guided_answers,
    interface_generation,
    interface_lookup,
    question_routing,
)
from app.domain import system_context as system_context_domain
from app.domain.errors import GuidanceReadError, SystemContextError
from app.llm.runtime import configured_provider
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
    use_case: str = "",
    trusted_web: str = "",
) -> HTMLResponse:
    return _render_answer(
        request,
        session,
        setting=setting,
        motherboard_model_id=motherboard_model_id,
        cpu_model_id=cpu_model_id,
        bios_version=bios_version,
        use_case=use_case,
        trusted_web=False,
        allow_external=False,
    )


@router.post("/answer", response_class=HTMLResponse)
def public_answer_submit(
    request: Request,
    session: SessionDep,
    setting: Annotated[str, Form()] = "",
    motherboard_model_id: Annotated[str, Form()] = "",
    cpu_model_id: Annotated[str, Form()] = "",
    bios_version: Annotated[str, Form()] = "",
    use_case: Annotated[str, Form()] = "",
    trusted_web: Annotated[str, Form()] = "",
) -> HTMLResponse:
    return _render_answer(
        request,
        session,
        setting=setting,
        motherboard_model_id=motherboard_model_id,
        cpu_model_id=cpu_model_id,
        bios_version=bios_version,
        use_case=use_case,
        trusted_web=trusted_web == "1",
        allow_external=True,
    )


def _render_answer(
    request: Request,
    session: Session,
    *,
    setting: str,
    motherboard_model_id: str,
    cpu_model_id: str,
    bios_version: str,
    use_case: str,
    trusted_web: bool,
    allow_external: bool,
) -> HTMLResponse:
    context = None
    presentation = None
    routing = None
    error = None
    status_code = 200
    library_matches = None
    web_research = None
    generation = None
    try:
        context = system_context_domain.resolve_optional(
            session,
            motherboard_model_id=motherboard_model_id,
            cpu_model_id=cpu_model_id,
            bios_version=bios_version,
            use_case=use_case,
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
                if trusted_web and allow_external:
                    web_research = trusted_web_research.research(
                        session, routing.original_query
                    )
            generation = interface_generation.generate_presentation(
                presentation, configured_provider() if allow_external else None
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
            "use_case": use_case,
            "system_context": context,
            "presentation": presentation,
            "routing": routing,
            "covered_settings": guidance_read.list_coverage(session, approved_only=True),
            "guided_questions": guided_answers.list_questions(session),
            "error": error,
            "trusted_web": trusted_web,
            "library_matches": library_matches,
            "web_research": web_research,
            "generation": generation,
        },
        status_code=status_code,
    )
