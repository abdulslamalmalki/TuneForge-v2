import io
import json

import pytest

from app.db.models import ExtractionRun, Setting
from app.domain import acceptance, manuals, review
from app.domain.errors import AcceptanceError

ASUS_SUPPORT_URL = "https://www.asus.com/supportonly/b650-test/helpdesk_manual/"


def _manual(db_session, pdf_bytes_factory, *, complete=True):
    manual = manuals.create_manual(
        db_session,
        io.BytesIO(pdf_bytes_factory(5)),
        original_filename="manual.pdf",
        vendor="ASUS" if complete else "",
        model_name="B650 Test",
        document_title="Official BIOS Guide",
        source_url=ASUS_SUPPORT_URL if complete else "",
        document_version="1.0",
        published_on="2026-01-01",
        language="en",
        board_revision="1.0",
        chipset="B650",
    )
    run = db_session.query(ExtractionRun).filter_by(manual_id=manual.id).one()
    run.status = "succeeded"
    run.parser_version = "test-parser"
    run.pages_processed = 5
    run.settings_found = 0
    db_session.commit()
    return manual, run


def _setting(db_session, manual, run, name, status, *, edited=False, description="Description"):
    setting = Setting(
        manual_id=manual.id,
        extraction_run_id=run.id,
        extracted_name=name,
        extracted_description=description,
        edited_name=f"{name} corrected" if edited else None,
        page_number=2,
        status=status,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )
    db_session.add(setting)
    db_session.commit()
    return setting


def test_report_derives_review_precision_and_estimated_recall(db_session, pdf_bytes_factory):
    manual, run = _manual(db_session, pdf_bytes_factory)
    _setting(db_session, manual, run, "Approved", "approved")
    _setting(db_session, manual, run, "Corrected", "approved", edited=True)
    _setting(db_session, manual, run, "Noise", "rejected", description="")
    _setting(db_session, manual, run, "Pending", "draft")
    acceptance.add_missed_setting(
        db_session,
        manual,
        name="Completely Missed",
        page_number=4,
        note="Visible in the BIOS chapter.",
    )

    report = acceptance.build_report(db_session, manual)
    assert report.total_candidates == 4
    assert report.reviewed_count == 3
    assert report.draft_count == 1
    assert report.approved_count == 2
    assert report.corrected_count == 1
    assert report.rejected_count == 1
    assert report.empty_description_count == 1
    assert report.page_reviewed_count == 0
    assert report.page_pending_count == 2
    assert report.page_accuracy is None
    assert report.missed_count == 1
    assert report.review_completion == pytest.approx(0.75)
    assert report.precision == pytest.approx(2 / 3)
    assert report.estimated_recall == pytest.approx(2 / 3)
    assert report.acceptance_complete is False


def test_missed_setting_validation_duplicate_and_dismissal(db_session, pdf_bytes_factory):
    manual, _run = _manual(db_session, pdf_bytes_factory)
    with pytest.raises(AcceptanceError, match="name is required"):
        acceptance.add_missed_setting(db_session, manual, name=" ", page_number=1)
    with pytest.raises(AcceptanceError, match="between 1"):
        acceptance.add_missed_setting(db_session, manual, name="Valid", page_number=6)

    missed = acceptance.add_missed_setting(
        db_session, manual, name="Memory Context Restore", page_number=3
    )
    with pytest.raises(AcceptanceError, match="already recorded"):
        acceptance.add_missed_setting(
            db_session, manual, name="memory context restore", page_number=3
        )
    with pytest.raises(AcceptanceError, match="reason is required"):
        acceptance.dismiss_missed_setting(db_session, manual, missed.id, reason=" ")

    acceptance.dismiss_missed_setting(db_session, manual, missed.id, reason="Entry mistake")
    assert acceptance.list_missed_settings(db_session, manual.id) == []
    (dismissed,) = acceptance.list_missed_settings(db_session, manual.id, include_dismissed=True)
    assert dismissed.status == acceptance.STATUS_DISMISSED
    assert dismissed.dismissal_reason == "Entry mistake"
    with pytest.raises(AcceptanceError, match="already been dismissed"):
        acceptance.dismiss_missed_setting(db_session, manual, missed.id, reason="Again")


def test_certification_requires_success_review_and_provenance(db_session, pdf_bytes_factory):
    manual, run = _manual(db_session, pdf_bytes_factory, complete=False)
    draft = _setting(db_session, manual, run, "Draft", "draft")

    with pytest.raises(AcceptanceError, match="drafts remain"):
        acceptance.certify_ground_truth(db_session, manual)
    draft.status = "rejected"
    db_session.commit()
    with pytest.raises(AcceptanceError, match="Complete the manual provenance"):
        acceptance.certify_ground_truth(db_session, manual)

    manuals.update_manual_provenance(
        db_session,
        manual,
        vendor="ASUS",
        model_name="B650 Test",
        document_title="Official BIOS Guide",
        source_url="https://example.com/manual.pdf",
        document_version="1.0",
        published_on="2026-01-01",
        language="en",
        board_revision="1.0",
        chipset="B650",
    )
    with pytest.raises(AcceptanceError, match="official_support_page"):
        acceptance.certify_ground_truth(db_session, manual)

    manuals.update_manual_provenance(
        db_session,
        manual,
        vendor="ASUS",
        model_name="B650 Test",
        document_title="Official BIOS Guide",
        source_url=ASUS_SUPPORT_URL,
        document_version="1.0",
        published_on="2026-01-01",
        language="en",
        board_revision="1.0",
        chipset="B650",
    )
    acceptance.certify_ground_truth(db_session, manual, notes="Full BIOS section checked.")
    report = acceptance.build_report(db_session, manual)
    assert report.ground_truth_reviewed is True
    assert report.acceptance_complete is True
    assert manual.acceptance_notes == "Full BIOS section checked."


def test_ground_truth_changes_invalidate_certification(db_session, pdf_bytes_factory):
    manual, run = _manual(db_session, pdf_bytes_factory)
    approved = _setting(db_session, manual, run, "Approved", "draft")
    review.approve_setting(db_session, approved)
    acceptance.review_page_reference(db_session, manual, approved.id, page_number=2)
    acceptance.certify_ground_truth(db_session, manual)
    assert manual.acceptance_reviewed_at is not None
    assert manuals.list_manual_events(db_session, manual.id)[-1].action == (
        acceptance.ACTION_ACCEPTANCE_CERTIFIED
    )

    review.reopen_setting(db_session, approved)
    assert manual.acceptance_reviewed_at is None
    invalidated = manuals.list_manual_events(db_session, manual.id)[-1]
    assert invalidated.action == acceptance.ACTION_ACCEPTANCE_INVALIDATED
    assert json.loads(invalidated.detail)["reason"] == "setting_reopened"
    review.approve_setting(db_session, approved)
    acceptance.certify_ground_truth(db_session, manual)

    acceptance.add_missed_setting(db_session, manual, name="Missed", page_number=4)
    assert manual.acceptance_reviewed_at is None
    acceptance_actions = [
        event.action
        for event in manuals.list_manual_events(db_session, manual.id)
        if event.action.startswith("acceptance_")
    ]
    assert acceptance_actions == [
        "acceptance_certified",
        "acceptance_invalidated",
        "acceptance_certified",
        "acceptance_invalidated",
    ]


def test_page_reference_review_is_audited_and_required_for_certification(
    db_session, pdf_bytes_factory
):
    manual, run = _manual(db_session, pdf_bytes_factory)
    approved = _setting(db_session, manual, run, "Approved", "approved")

    with pytest.raises(AcceptanceError, match="page reference"):
        acceptance.certify_ground_truth(db_session, manual)
    with pytest.raises(AcceptanceError, match="between 1"):
        acceptance.review_page_reference(db_session, manual, approved.id, page_number=6)

    changed = acceptance.review_page_reference(db_session, manual, approved.id, page_number=3)
    assert changed is True
    assert approved.page_review_status == acceptance.PAGE_CORRECTED
    assert approved.edited_page_number == 3
    report = acceptance.build_report(db_session, manual)
    assert report.page_reviewed_count == 1
    assert report.page_corrected_count == 1
    assert report.page_accuracy == 0.0
    (page_row,) = acceptance.list_page_references(db_session, manual.id)
    assert page_row.extracted_page_number == 2
    assert page_row.effective_page_number == 3
    assert review.list_review_events(db_session, approved.id)[-1].action == (
        acceptance.ACTION_PAGE_REFERENCE_REVIEWED
    )

    acceptance.certify_ground_truth(db_session, manual)
    assert manual.acceptance_reviewed_at is not None
    acceptance.review_page_reference(db_session, manual, approved.id, page_number=2)
    assert approved.page_review_status == acceptance.PAGE_CORRECT
    assert approved.edited_page_number is None
    assert manual.acceptance_reviewed_at is None
