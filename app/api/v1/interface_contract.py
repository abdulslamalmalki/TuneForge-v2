"""Provider-neutral contract for any future language-model interface."""

from fastapi import APIRouter, HTTPException, Query

from app.api.v1.deps import SessionDep
from app.api.v1.schemas import (
    ErrorOut,
    GuidedAnswerOut,
    GuidedQuestionOut,
    InterfaceContractOut,
    InterfacePresentationOut,
    QuestionRouteOut,
    TrustedWebResearchOut,
)
from app.domain import guided_answers, interface_lookup, question_routing
from app.domain import system_context as system_context_domain
from app.domain.errors import GuidanceReadError, GuidedAnswerError, SystemContextError
from app.normalization.interface_contract import interface_contract
from app.research import trusted_web

router = APIRouter(tags=["interface contract"])


@router.get(
    "/interface-contract",
    response_model=InterfaceContractOut,
    summary="Get the provider-neutral LLM presentation and validation policy",
)
def get_interface_contract() -> InterfaceContractOut:
    return InterfaceContractOut.model_validate(interface_contract())


@router.get(
    "/interface/route",
    response_model=QuestionRouteOut,
    summary="Route one simple question without fuzzy matching or a model",
    responses={400: {"model": ErrorOut, "description": "Question validation failed"}},
)
def route_question(question: str = Query(min_length=1, max_length=300)) -> QuestionRouteOut:
    try:
        decision = question_routing.route(question)
    except question_routing.QuestionRoutingError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None
    return QuestionRouteOut(
        version=decision.version,
        kind=decision.kind,
        original_query=decision.original_query,
        routed_value=decision.routed_value,
        rule=decision.rule,
        question_id=decision.question_id,
        transformed=decision.transformed,
    )


@router.get(
    "/interface/presentation",
    response_model=InterfacePresentationOut,
    summary="Build a validated citation-safe presentation envelope",
    responses={400: {"model": ErrorOut, "description": "Lookup validation failed"}},
)
def get_interface_presentation(
    session: SessionDep,
    setting: str = Query(min_length=1),
    motherboard_model_id: str = "",
    cpu_model_id: str = "",
    bios_version: str = "",
) -> InterfacePresentationOut:
    try:
        context = system_context_domain.resolve_optional(
            session,
            motherboard_model_id=motherboard_model_id,
            cpu_model_id=cpu_model_id,
            bios_version=bios_version,
        )
    except SystemContextError as error:
        raise HTTPException(status_code=400, detail=error.message) from None
    try:
        presentation = interface_lookup.build_presentation(
            session,
            setting,
            system_context=context,
            motherboard_model_id=motherboard_model_id,
            cpu_model_id=cpu_model_id,
        )
    except GuidanceReadError as error:
        raise HTTPException(status_code=400, detail=error.message) from None
    return InterfacePresentationOut.from_envelope(presentation)


@router.get(
    "/interface/trusted-web-research",
    response_model=TrustedWebResearchOut,
    summary="Run opt-in BIOS research over allowlisted official websites",
    responses={400: {"model": ErrorOut, "description": "Question validation failed"}},
)
def get_trusted_web_research(
    session: SessionDep,
    question: str = Query(min_length=1, max_length=300),
) -> TrustedWebResearchOut:
    try:
        result = trusted_web.research(session, question)
    except trusted_web.TrustedWebResearchError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None
    return TrustedWebResearchOut.from_view(result)


@router.get(
    "/interface/questions",
    response_model=list[GuidedQuestionOut],
    summary="List the fixed reviewed AM5 guided questions and current outcomes",
)
def get_guided_questions(session: SessionDep) -> list[GuidedQuestionOut]:
    return [
        GuidedQuestionOut.from_question(question)
        for question in guided_answers.list_questions(session)
    ]


@router.get(
    "/interface/questions/{question_id}/answer",
    response_model=GuidedAnswerOut,
    summary="Return one deterministic cited answer or capability refusal",
    responses={
        400: {"model": ErrorOut, "description": "Hardware context validation failed"},
        404: {"model": ErrorOut, "description": "Guided question not found"},
    },
)
def get_guided_answer(
    question_id: str,
    session: SessionDep,
    motherboard_model_id: str = "",
    cpu_model_id: str = "",
    bios_version: str = "",
) -> GuidedAnswerOut:
    try:
        context = system_context_domain.resolve_optional(
            session,
            motherboard_model_id=motherboard_model_id,
            cpu_model_id=cpu_model_id,
            bios_version=bios_version,
        )
        answer = guided_answers.build_answer(
            session,
            question_id,
            system_context=context,
        )
    except SystemContextError as error:
        raise HTTPException(status_code=400, detail=error.message) from None
    except GuidedAnswerError as error:
        raise HTTPException(status_code=404, detail=error.message) from None
    return GuidedAnswerOut.from_answer(answer)
