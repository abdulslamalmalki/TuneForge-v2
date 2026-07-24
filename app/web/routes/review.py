"""Draft review queue page (FR-17, plan section 12 item 4)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.domain import manuals as manuals_domain
from app.domain import review as review_domain
from app.web.templating import templates

router = APIRouter()

SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/review", response_class=HTMLResponse)
def review_queue(request: Request, session: SessionDep, manual_id: str = "") -> HTMLResponse:
    # The filter arrives as a form string; anything but a plain integer
    # (including the dropdown's "All manuals" empty value) means unfiltered.
    selected_manual_id = int(manual_id) if manual_id.isdigit() else None
    context = {
        "rows": review_domain.list_draft_queue(session, manual_id=selected_manual_id),
        "manuals": manuals_domain.list_manuals(session),
        "selected_manual_id": selected_manual_id,
    }
    return templates.TemplateResponse(request, "review_queue.html", context)
