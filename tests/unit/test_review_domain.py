"""Review state machine, audit events, and effective values (plan section 11).

Every allowed transition succeeds and writes the right audit action; every
disallowed transition raises and writes nothing; reject without a reason
raises; originals stay immutable through edits (FR-16, FR-19 to FR-23).
"""

import io
import json

import pytest
from sqlalchemy import text

from app.domain import manuals as manuals_domain
from app.domain import review
from app.domain import settings as settings_domain
from app.domain.errors import ReviewActionError
from app.domain.extraction_runs import get_run_for_manual
from app.extraction.settings_parser import SettingCandidate

EXTRACTED_NAME = "Ai Overclock Tuner"
EXTRACTED_DESCRIPTION = "Selects the memory overclocking profile."


def _draft(db_session, pdf_bytes_factory):
    manual = manuals_domain.create_manual(
        db_session,
        io.BytesIO(pdf_bytes_factory()),
        original_filename="manual.pdf",
        vendor="",
        model_name="",
    )
    run = get_run_for_manual(db_session, manual.id)
    (setting,) = settings_domain.create_draft_settings(
        db_session,
        manual_id=manual.id,
        extraction_run_id=run.id,
        candidates=[SettingCandidate(EXTRACTED_NAME, EXTRACTED_DESCRIPTION, 3)],
    )
    db_session.commit()
    return setting


def _manual_with_drafts(db_session, pdf_bytes, candidates):
    manual = manuals_domain.create_manual(
        db_session,
        io.BytesIO(pdf_bytes),
        original_filename="manual.pdf",
        vendor="",
        model_name="",
    )
    run = get_run_for_manual(db_session, manual.id)
    settings = settings_domain.create_draft_settings(
        db_session, manual_id=manual.id, extraction_run_id=run.id, candidates=candidates
    )
    db_session.commit()
    return manual, settings


def _events(db_session, setting):
    return review.list_review_events(db_session, setting.id)


def test_approve_draft_without_note(db_session, pdf_bytes_factory):
    setting = _draft(db_session, pdf_bytes_factory)
    created_updated_at = setting.updated_at

    review.approve_setting(db_session, setting)

    assert setting.status == review.STATUS_APPROVED
    assert setting.review_note is None
    assert setting.updated_at >= created_updated_at
    (event,) = _events(db_session, setting)
    assert (event.action, event.actor, event.detail) == ("approved", review.ACTOR, None)
    assert event.created_at


def test_approve_draft_with_note(db_session, pdf_bytes_factory):
    setting = _draft(db_session, pdf_bytes_factory)
    review.approve_setting(db_session, setting, note="  matches page 3  ")
    assert setting.review_note == "matches page 3"
    (event,) = _events(db_session, setting)
    assert event.detail == "matches page 3"


def test_reject_draft_requires_reason(db_session, pdf_bytes_factory):
    setting = _draft(db_session, pdf_bytes_factory)
    for reason in ("", "   "):
        with pytest.raises(ReviewActionError, match="reason is required"):
            review.reject_setting(db_session, setting, reason)
    assert setting.status == settings_domain.STATUS_DRAFT
    assert _events(db_session, setting) == []


def test_reject_draft_with_reason(db_session, pdf_bytes_factory):
    setting = _draft(db_session, pdf_bytes_factory)
    review.reject_setting(db_session, setting, "Garbled table text.")
    assert setting.status == review.STATUS_REJECTED
    assert setting.review_note == "Garbled table text."
    (event,) = _events(db_session, setting)
    assert (event.action, event.detail) == ("rejected", "Garbled table text.")


def test_reopen_approved_setting(db_session, pdf_bytes_factory):
    setting = _draft(db_session, pdf_bytes_factory)
    review.approve_setting(db_session, setting, note="fine")
    review.reopen_setting(db_session, setting)
    assert setting.status == settings_domain.STATUS_DRAFT
    assert setting.review_note is None
    assert [event.action for event in _events(db_session, setting)] == ["approved", "reopened"]


def test_reopen_rejected_setting_keeps_reason_in_audit_history(db_session, pdf_bytes_factory):
    setting = _draft(db_session, pdf_bytes_factory)
    review.reject_setting(db_session, setting, "Not a real setting.")
    review.reopen_setting(db_session, setting)
    assert setting.status == settings_domain.STATUS_DRAFT
    assert setting.review_note is None  # cleared from the fresh draft...
    rejected, reopened = _events(db_session, setting)
    assert rejected.detail == "Not a real setting."  # ...but permanent in the audit trail
    assert reopened.action == "reopened"


@pytest.mark.parametrize(
    ("prepare", "attempt"),
    [
        # approved -> approved / rejected (approved <-> rejected needs draft)
        ("approve", lambda s, x: review.approve_setting(s, x)),
        ("approve", lambda s, x: review.reject_setting(s, x, "reason")),
        # rejected -> rejected / approved
        ("reject", lambda s, x: review.reject_setting(s, x, "reason")),
        ("reject", lambda s, x: review.approve_setting(s, x)),
        # draft -> draft (reopen only applies to reviewed settings)
        (None, lambda s, x: review.reopen_setting(s, x)),
    ],
)
def test_disallowed_transitions_raise_and_write_no_event(
    db_session, pdf_bytes_factory, prepare, attempt
):
    setting = _draft(db_session, pdf_bytes_factory)
    if prepare == "approve":
        review.approve_setting(db_session, setting)
    elif prepare == "reject":
        review.reject_setting(db_session, setting, "initial reason")
    events_before = len(_events(db_session, setting))
    status_before = setting.status

    with pytest.raises(ReviewActionError, match="cannot be moved"):
        attempt(db_session, setting)

    assert setting.status == status_before
    assert len(_events(db_session, setting)) == events_before


def test_edit_draft_sets_overrides_and_keeps_originals(db_session, pdf_bytes_factory):
    setting = _draft(db_session, pdf_bytes_factory)
    review.edit_draft_setting(
        db_session, setting, name="AI Overclock Tuner", description="Cleaned description."
    )

    # FR-16: originals untouched; FR-19: both values exposed.
    assert setting.extracted_name == EXTRACTED_NAME
    assert setting.extracted_description == EXTRACTED_DESCRIPTION
    assert setting.edited_name == "AI Overclock Tuner"
    assert setting.edited_description == "Cleaned description."
    assert setting.status == settings_domain.STATUS_DRAFT  # editing never changes state

    (event,) = _events(db_session, setting)
    assert event.action == "edited"
    assert json.loads(event.detail) == {
        "name": {"before": EXTRACTED_NAME, "after": "AI Overclock Tuner"},
        "description": {"before": EXTRACTED_DESCRIPTION, "after": "Cleaned description."},
    }


def test_edit_only_description_logs_only_that_change(db_session, pdf_bytes_factory):
    setting = _draft(db_session, pdf_bytes_factory)
    review.edit_draft_setting(
        db_session, setting, name=EXTRACTED_NAME, description="New text only."
    )
    assert setting.edited_name is None
    assert setting.edited_description == "New text only."
    (event,) = _events(db_session, setting)
    assert list(json.loads(event.detail)) == ["description"]


def test_edit_back_to_extracted_values_clears_overrides(db_session, pdf_bytes_factory):
    setting = _draft(db_session, pdf_bytes_factory)
    review.edit_draft_setting(db_session, setting, name="Renamed", description="Changed.")
    review.edit_draft_setting(
        db_session, setting, name=EXTRACTED_NAME, description=EXTRACTED_DESCRIPTION
    )
    assert setting.edited_name is None
    assert setting.edited_description is None
    assert len(_events(db_session, setting)) == 2  # both real edits were audited


def test_edit_with_no_changes_is_a_silent_no_op(db_session, pdf_bytes_factory):
    setting = _draft(db_session, pdf_bytes_factory)
    review.edit_draft_setting(
        db_session, setting, name=f"  {EXTRACTED_NAME}  ", description=EXTRACTED_DESCRIPTION
    )
    assert _events(db_session, setting) == []


def test_edit_requires_nonempty_name(db_session, pdf_bytes_factory):
    setting = _draft(db_session, pdf_bytes_factory)
    with pytest.raises(ReviewActionError, match="cannot be empty"):
        review.edit_draft_setting(db_session, setting, name="   ", description="x")
    assert _events(db_session, setting) == []


@pytest.mark.parametrize("prepare", ["approve", "reject"])
def test_edit_non_draft_raises_reopen_first(db_session, pdf_bytes_factory, prepare):
    setting = _draft(db_session, pdf_bytes_factory)
    if prepare == "approve":
        review.approve_setting(db_session, setting)
    else:
        review.reject_setting(db_session, setting, "reason")
    with pytest.raises(ReviewActionError, match="reopen it first"):
        review.edit_draft_setting(db_session, setting, name="New", description="")
    assert setting.edited_name is None


def test_draft_queue_orders_by_manual_then_page_and_lists_only_drafts(
    db_session, pdf_bytes_factory
):
    first_manual, (approved, late, early) = _manual_with_drafts(
        db_session,
        pdf_bytes_factory(),
        [
            SettingCandidate("Approved Away", "", 2),
            SettingCandidate("Late Page", "", 9),
            SettingCandidate("Early Page", "", 4),
        ],
    )
    second_manual, _ = _manual_with_drafts(
        db_session,
        pdf_bytes_factory(2),  # different bytes: dedupe (FR-4) allows the upload
        [SettingCandidate("Other Manual Setting", "", 1)],
    )
    review.approve_setting(db_session, approved)

    rows = review.list_draft_queue(db_session)

    assert [(row.manual.id, row.effective_name) for row in rows] == [
        (first_manual.id, "Early Page"),
        (first_manual.id, "Late Page"),
        (second_manual.id, "Other Manual Setting"),
    ]


def test_draft_queue_filters_by_manual(db_session, pdf_bytes_factory):
    _manual_with_drafts(db_session, pdf_bytes_factory(), [SettingCandidate("First", "", 1)])
    second_manual, _ = _manual_with_drafts(
        db_session, pdf_bytes_factory(2), [SettingCandidate("Second", "", 1)]
    )

    rows = review.list_draft_queue(db_session, manual_id=second_manual.id)
    assert [row.effective_name for row in rows] == ["Second"]
    assert review.list_draft_queue(db_session, manual_id=second_manual.id + 999) == []


def test_draft_queue_rows_resolve_effective_values(db_session, pdf_bytes_factory):
    setting = _draft(db_session, pdf_bytes_factory)
    review.edit_draft_setting(db_session, setting, name="Renamed", description="New text.")
    (row,) = review.list_draft_queue(db_session)
    assert (row.effective_name, row.effective_description) == ("Renamed", "New text.")


def _indexed(db_session):
    query = text("SELECT rowid, name, description FROM settings_fts ORDER BY rowid")
    return [tuple(row) for row in db_session.execute(query)]


def test_approve_indexes_the_effective_values(db_session, pdf_bytes_factory):
    setting = _draft(db_session, pdf_bytes_factory)
    review.edit_draft_setting(db_session, setting, name="Renamed", description="Cleaned.")
    assert _indexed(db_session) == []  # drafts are never searchable (FR-24)

    review.approve_setting(db_session, setting)
    assert _indexed(db_session) == [(setting.id, "Renamed", "Cleaned.")]


def test_reopen_removes_the_setting_from_the_index(db_session, pdf_bytes_factory):
    setting = _draft(db_session, pdf_bytes_factory)
    review.approve_setting(db_session, setting)
    review.reopen_setting(db_session, setting)
    assert _indexed(db_session) == []  # FR-26: immediate removal


def test_reject_never_touches_the_index(db_session, pdf_bytes_factory):
    setting = _draft(db_session, pdf_bytes_factory)
    review.reject_setting(db_session, setting, "noise")
    assert _indexed(db_session) == []


def test_get_setting_returns_row_or_none(db_session, pdf_bytes_factory):
    setting = _draft(db_session, pdf_bytes_factory)
    assert review.get_setting(db_session, setting.id) is setting
    assert review.get_setting(db_session, setting.id + 999) is None


def test_effective_values_resolve_edited_over_extracted(db_session, pdf_bytes_factory):
    setting = _draft(db_session, pdf_bytes_factory)
    assert review.effective_name(setting) == EXTRACTED_NAME
    assert review.effective_description(setting) == EXTRACTED_DESCRIPTION

    review.edit_draft_setting(db_session, setting, name="Renamed", description="")
    assert review.effective_name(setting) == "Renamed"
    # An emptied description is a real override, not a fallback to extracted.
    assert setting.edited_description == ""
    assert review.effective_description(setting) == ""
