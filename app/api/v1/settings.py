"""API v1 setting resources — same domain calls as the HTML pages.

The write endpoints translate JSON bodies to the review state machine and
map ReviewActionError to a 400 with the domain's message; every rule
(required rejection reason, draft-only editing, allowed transitions,
audit events, search-index maintenance) lives in the domain.
"""

from fastapi import APIRouter, HTTPException, Response
from sqlalchemy.orm import Session

from app.api.v1.deps import SessionDep
from app.api.v1.pagination import PAGINATED_200, PageDep, paginate
from app.api.v1.schemas import (
    ApproveIn,
    DraftQueueItemOut,
    EditSettingIn,
    ErrorOut,
    RejectIn,
    SettingDetailOut,
)
from app.db.models import Setting
from app.domain import canonical_settings
from app.domain import review as review_domain
from app.domain.errors import ReviewActionError

router = APIRouter(tags=["settings"])

_ACTION_RESPONSES = {
    400: {"model": ErrorOut, "description": "Rejected by the review state machine"},
    404: {"model": ErrorOut, "description": "Setting not found"},
}


@router.get(
    "/review-queue",
    summary="List drafts awaiting review",
    description=(
        "Draft settings ordered by manual then page number, optionally"
        " filtered to one manual (FR-17). Approved and rejected settings"
        " never appear."
    ),
    responses=PAGINATED_200,
)
def review_queue(
    session: SessionDep,
    response: Response,
    page: PageDep,
    manual_id: int | None = None,
) -> list[DraftQueueItemOut]:
    rows = review_domain.list_draft_queue(session, manual_id=manual_id)
    return paginate([DraftQueueItemOut.from_row(row) for row in rows], page, response)


@router.get(
    "/settings/{setting_id}",
    summary="Get one setting",
    description=(
        "The full review record (FR-18): immutable extracted values, edited"
        " overrides, resolved effective values, review note, and the"
        " append-only audit history."
    ),
    responses={404: {"model": ErrorOut, "description": "Setting not found"}},
)
def setting_detail(setting_id: int, session: SessionDep) -> SettingDetailOut:
    setting = _get_setting_or_404(session, setting_id)
    return _detail(session, setting)


@router.patch(
    "/settings/{setting_id}",
    summary="Edit a draft",
    description=(
        "Set the draft's effective name and description (FR-19). The"
        " extracted originals are immutable (FR-16); a value equal to the"
        " extracted one clears the override, and a real change is audited."
    ),
    responses=_ACTION_RESPONSES,
)
def edit_setting(setting_id: int, body: EditSettingIn, session: SessionDep) -> SettingDetailOut:
    setting = _get_setting_or_404(session, setting_id)
    _apply(review_domain.edit_draft_setting, session, setting, body.name, body.description)
    return _detail(session, setting)


@router.post(
    "/settings/{setting_id}/approve",
    summary="Approve a draft",
    description="Draft -> approved with an optional note (FR-20); the setting becomes searchable.",
    responses=_ACTION_RESPONSES,
)
def approve_setting(setting_id: int, body: ApproveIn, session: SessionDep) -> SettingDetailOut:
    setting = _get_setting_or_404(session, setting_id)
    _apply(review_domain.approve_setting, session, setting, body.note)
    return _detail(session, setting)


@router.post(
    "/settings/{setting_id}/reject",
    summary="Reject a draft",
    description="Draft -> rejected; a non-empty reason is required (FR-21).",
    responses=_ACTION_RESPONSES,
)
def reject_setting(setting_id: int, body: RejectIn, session: SessionDep) -> SettingDetailOut:
    setting = _get_setting_or_404(session, setting_id)
    _apply(review_domain.reject_setting, session, setting, body.reason)
    return _detail(session, setting)


@router.post(
    "/settings/{setting_id}/reopen",
    summary="Reopen a reviewed setting",
    description=(
        "Approved/rejected -> draft (FR-22); an approved setting leaves"
        " search immediately (FR-26). The prior review note stays in the"
        " audit history."
    ),
    responses=_ACTION_RESPONSES,
)
def reopen_setting(setting_id: int, session: SessionDep) -> SettingDetailOut:
    setting = _get_setting_or_404(session, setting_id)
    _apply(review_domain.reopen_setting, session, setting)
    return _detail(session, setting)


def _get_setting_or_404(session: Session, setting_id: int) -> Setting:
    setting = review_domain.get_setting(session, setting_id)
    if setting is None:
        raise HTTPException(status_code=404, detail="Setting not found.")
    return setting


def _apply(action, session: Session, setting: Setting, *args) -> None:
    try:
        action(session, setting, *args)
    except ReviewActionError as error:
        raise HTTPException(status_code=400, detail=error.message) from None


def _detail(session: Session, setting: Setting) -> SettingDetailOut:
    events = review_domain.list_review_events(session, setting.id)
    return SettingDetailOut.from_setting(
        setting,
        events,
        canonical_settings.get_mapping(session, setting.id),
        canonical_settings.suggest_for_setting(session, setting),
        canonical_settings.list_mapping_events(session, setting.id),
    )
