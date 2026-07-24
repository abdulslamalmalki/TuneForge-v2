"""Draft creation domain function (FR-14, FR-15): fields, transaction ownership."""

import io

from sqlalchemy import select

from app.db.models import Setting
from app.domain import manuals as manuals_domain
from app.domain import settings as settings_domain
from app.domain.extraction_runs import get_run_for_manual
from app.extraction.settings_parser import SettingCandidate


def _manual_and_run(db_session, pdf_bytes_factory):
    manual = manuals_domain.create_manual(
        db_session,
        io.BytesIO(pdf_bytes_factory()),
        original_filename="manual.pdf",
        vendor="",
        model_name="",
    )
    return manual, get_run_for_manual(db_session, manual.id)


def _stored(db_session):
    db_session.expire_all()
    return db_session.scalars(select(Setting).order_by(Setting.id)).all()


def test_creates_one_draft_row_per_candidate(db_session, pdf_bytes_factory):
    manual, run = _manual_and_run(db_session, pdf_bytes_factory)
    candidates = [
        SettingCandidate(
            extracted_name="Ai Overclock Tuner",
            extracted_description="Selects the memory profile.",
            page_number=3,
        ),
        SettingCandidate(extracted_name="DRAM Voltage", extracted_description="", page_number=5),
    ]
    settings_domain.create_draft_settings(
        db_session, manual_id=manual.id, extraction_run_id=run.id, candidates=candidates
    )
    db_session.commit()

    first, second = _stored(db_session)
    assert (first.extracted_name, first.extracted_description, first.page_number) == (
        "Ai Overclock Tuner",
        "Selects the memory profile.",
        3,
    )
    # FR-14: a candidate without a description is still created, empty.
    assert (second.extracted_name, second.extracted_description, second.page_number) == (
        "DRAM Voltage",
        "",
        5,
    )
    for row in (first, second):
        assert row.status == settings_domain.STATUS_DRAFT
        assert (row.manual_id, row.extraction_run_id) == (manual.id, run.id)
        assert row.edited_name is None and row.edited_description is None
        assert row.created_at and row.updated_at == row.created_at


def test_no_candidates_creates_no_rows(db_session, pdf_bytes_factory):
    manual, run = _manual_and_run(db_session, pdf_bytes_factory)
    created = settings_domain.create_draft_settings(
        db_session, manual_id=manual.id, extraction_run_id=run.id, candidates=[]
    )
    db_session.commit()
    assert created == []
    assert _stored(db_session) == []


def test_caller_owns_the_transaction(db_session, pdf_bytes_factory):
    manual, run = _manual_and_run(db_session, pdf_bytes_factory)
    settings_domain.create_draft_settings(
        db_session,
        manual_id=manual.id,
        extraction_run_id=run.id,
        candidates=[SettingCandidate("Alpha Beta", "", 1)],
    )
    db_session.rollback()  # nothing committed by the function itself
    assert _stored(db_session) == []
