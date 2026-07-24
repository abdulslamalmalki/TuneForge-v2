"""Review state machine and audit trail (plan section 11, FR-19 to FR-23).

Allowed transitions: draft -> approved (optional note), draft -> rejected
(non-empty reason required), approved/rejected -> draft (reopen). Nothing
else: approved <-> rejected requires passing through draft. Editing the
effective name/description is allowed only in draft and does not change
state. Every action appends an immutable review_events row and touches
updated_at, committed as one transaction that also maintains the search
index: entering approved indexes the setting, leaving it removes the
entry — so only approved settings are ever searchable (FR-24, FR-26).

The extracted name and description are never written here (FR-16): edits
land in edited_name/edited_description, and an edit equal to the extracted
value clears the override so "edited" always means a real difference.
"""

import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.context import current_actor
from app.db.models import Manual, ReviewEvent, Setting, SettingCanonicalMapping
from app.domain import acceptance as acceptance_domain
from app.domain.errors import ReviewActionError
from app.domain.extraction_runs import utc_now_iso
from app.domain.settings import STATUS_DRAFT
from app.search import index as search_index

STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"

# Single-reviewer v0.1: a fixed actor label (plan section 11). The column
# exists so multi-reviewer support later is a data change, not a redesign.
ACTOR = "local-reviewer"

ACTION_EDITED = "edited"
ACTION_APPROVED = "approved"
ACTION_REJECTED = "rejected"
ACTION_REOPENED = "reopened"

_ALLOWED_TRANSITIONS = {
    (STATUS_DRAFT, STATUS_APPROVED): ACTION_APPROVED,
    (STATUS_DRAFT, STATUS_REJECTED): ACTION_REJECTED,
    (STATUS_APPROVED, STATUS_DRAFT): ACTION_REOPENED,
    (STATUS_REJECTED, STATUS_DRAFT): ACTION_REOPENED,
}


def effective_name(setting: Setting) -> str:
    """Edited name when present, else the extracted name (plan section 9)."""
    return setting.extracted_name if setting.edited_name is None else setting.edited_name


def effective_description(setting: Setting) -> str:
    if setting.edited_description is None:
        return setting.extracted_description
    return setting.edited_description


@dataclass(frozen=True)
class DraftQueueRow:
    """One review-queue row (FR-17): the draft with resolved display values."""

    setting: Setting
    manual: Manual
    effective_name: str
    effective_description: str


def list_draft_queue(session: Session, manual_id: int | None = None) -> list[DraftQueueRow]:
    """Drafts awaiting review, ordered by manual then page number (FR-17).

    With manual_id, only that manual's drafts; an unknown id just yields an
    empty queue. Approved and rejected settings never appear.
    """
    query = (
        select(Setting, Manual)
        .join(Manual, Setting.manual_id == Manual.id)
        .where(Setting.status == STATUS_DRAFT)
        .order_by(Setting.manual_id, Setting.page_number, Setting.id)
    )
    if manual_id is not None:
        query = query.where(Setting.manual_id == manual_id)
    return [
        DraftQueueRow(setting, manual, effective_name(setting), effective_description(setting))
        for setting, manual in session.execute(query)
    ]


def approve_setting(session: Session, setting: Setting, note: str = "") -> None:
    """Approve a draft with an optional note (FR-20)."""
    note = note.strip()
    _transition(session, setting, STATUS_APPROVED, note=note or None)


def reject_setting(session: Session, setting: Setting, reason: str) -> None:
    """Reject a draft; a non-empty reason is required (FR-21)."""
    reason = reason.strip()
    if not reason:
        raise ReviewActionError("A rejection reason is required.")
    _transition(session, setting, STATUS_REJECTED, note=reason)


def reopen_setting(session: Session, setting: Setting) -> None:
    """Reopen an approved or rejected setting back to draft (FR-22).

    Clears review_note: the prior rejection reason stays visible in the
    audit history (plan section 11), not on the fresh draft.
    """
    _transition(session, setting, STATUS_DRAFT, note=None)


def edit_draft_setting(session: Session, setting: Setting, name: str, description: str) -> None:
    """Set the draft's effective name and description (FR-19).

    A submitted value equal to the extracted one clears the override. A
    submission that changes nothing is a no-op and writes no audit event;
    a real change appends an 'edited' event with before/after values.
    """
    if setting.status != STATUS_DRAFT:
        raise ReviewActionError(
            f"Only draft settings can be edited; this one is {setting.status} — reopen it first."
        )
    name = name.strip()
    if not name:
        raise ReviewActionError("The setting name cannot be empty.")
    description = description.strip()

    changes = {}
    if name != effective_name(setting):
        changes["name"] = {"before": effective_name(setting), "after": name}
        setting.edited_name = None if name == setting.extracted_name else name
    if description != effective_description(setting):
        changes["description"] = {"before": effective_description(setting), "after": description}
        setting.edited_description = (
            None if description == setting.extracted_description else description
        )
    if not changes:
        return

    setting.updated_at = utc_now_iso()
    manual = session.get(Manual, setting.manual_id)
    if manual is not None:
        acceptance_domain.invalidate_certification(
            session,
            manual,
            reason="setting_edited",
        )
    _append_event(session, setting, ACTION_EDITED, detail=json.dumps(changes))
    session.commit()


def get_setting(session: Session, setting_id: int) -> Setting | None:
    return session.get(Setting, setting_id)


def list_review_events(session: Session, setting_id: int) -> list[ReviewEvent]:
    """The setting's audit history, oldest first (FR-23)."""
    query = select(ReviewEvent).where(ReviewEvent.setting_id == setting_id).order_by(ReviewEvent.id)
    return list(session.scalars(query))


def _transition(session: Session, setting: Setting, to_status: str, *, note: str | None) -> None:
    """The one validating transition (plan section 11): status change,
    review_note, audit event, updated_at, and the search-index update
    commit atomically."""
    action = _ALLOWED_TRANSITIONS.get((setting.status, to_status))
    if action is None:
        raise ReviewActionError(
            f"A setting in status '{setting.status}' cannot be moved to '{to_status}'."
            + (" Reopen it first." if setting.status != STATUS_DRAFT else "")
        )
    if (
        setting.status == STATUS_APPROVED
        and to_status == STATUS_DRAFT
        and session.get(SettingCanonicalMapping, setting.id) is not None
    ):
        raise ReviewActionError(
            "Remove the canonical mapping before reopening this approved setting."
        )
    setting.status = to_status
    setting.review_note = note
    setting.updated_at = utc_now_iso()
    manual = session.get(Manual, setting.manual_id)
    if manual is not None:
        acceptance_domain.invalidate_certification(
            session,
            manual,
            reason=f"setting_{action}",
        )
    if to_status == STATUS_APPROVED:
        search_index.add_setting(
            session, setting.id, effective_name(setting), effective_description(setting)
        )
    else:
        # Leaving approved removes the entry immediately (FR-26); for a
        # draft being rejected the delete is a harmless no-op.
        search_index.remove_setting(session, setting.id)
    _append_event(session, setting, action, detail=note)
    session.commit()


def _append_event(session: Session, setting: Setting, action: str, *, detail: str | None) -> None:
    session.add(
        ReviewEvent(
            setting_id=setting.id,
            action=action,
            actor=current_actor(),
            detail=detail,
            created_at=utc_now_iso(),
        )
    )
