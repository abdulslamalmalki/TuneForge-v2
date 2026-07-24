"""Server-rendered deterministic answers for the fixed AM5 question set."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.domain import guided_answers
from app.domain import system_context as system_context_domain
from app.domain.errors import GuidedAnswerError, SystemContextError
from app.web.templating import templates

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/assistant", response_class=HTMLResponse)
def guided_answers_page(
    request: Request,
    session: SessionDep,
    question_id: str = "",
    motherboard_model_id: str = "",
    cpu_model_id: str = "",
) -> HTMLResponse:
    answer = None
    context = None
    error = None
    status_code = 200
    try:
        context = system_context_domain.resolve_optional(
            session,
            motherboard_model_id=motherboard_model_id,
            cpu_model_id=cpu_model_id,
        )
        if question_id.strip():
            answer = guided_answers.build_answer(
                session,
                question_id,
                system_context=context,
            )
    except SystemContextError as exception:
        error = exception.message
        status_code = 400
    except GuidedAnswerError as exception:
        error = exception.message
        status_code = 404
    return templates.TemplateResponse(
        request,
        "guided_answers.html",
        {
            "questions": guided_answers.list_questions(session),
            "question_id": question_id,
            "motherboard_model_id": motherboard_model_id,
            "cpu_model_id": cpu_model_id,
            "system_context": context,
            "answer": answer,
            "error": error,
        },
        status_code=status_code,
    )
