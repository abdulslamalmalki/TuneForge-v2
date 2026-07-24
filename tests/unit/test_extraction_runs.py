import io

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.models import ExtractionRun, Manual
from app.domain import extraction_runs
from app.domain import manuals as manuals_domain


def _upload(db_session, pdf_bytes):
    return manuals_domain.create_manual(
        db_session,
        io.BytesIO(pdf_bytes),
        original_filename="manual.pdf",
        vendor="",
        model_name="",
    )


def _runs_for(db_session, manual_id):
    return list(
        db_session.scalars(select(ExtractionRun).where(ExtractionRun.manual_id == manual_id))
    )


def _insert_manual_without_run(db_session, sha256: str) -> Manual:
    # Simulates a manual uploaded before run creation existed (M1 data).
    manual = Manual(
        original_filename="legacy.pdf",
        stored_filename=f"manual_legacy_{sha256[:8]}.pdf",
        sha256=sha256,
        file_size_bytes=10,
        page_count=1,
        document_title="legacy.pdf",
        retrieved_at="2026-01-01T00:00:00+00:00",
        language="en",
        created_at="2026-01-01T00:00:00+00:00",
    )
    db_session.add(manual)
    db_session.commit()
    return manual


def test_upload_creates_exactly_one_pending_run(db_session, pdf_bytes_factory):
    manual = _upload(db_session, pdf_bytes_factory())
    runs = _runs_for(db_session, manual.id)
    assert len(runs) == 1
    run = runs[0]
    assert run.status == extraction_runs.STATUS_PENDING
    assert run.parser_version is None
    assert run.started_at is None
    assert run.finished_at is None
    assert run.pages_processed == 0
    assert run.settings_found == 0


def test_ensure_creates_pending_run_for_manual_without_one(db_session):
    manual = _insert_manual_without_run(db_session, "a" * 64)
    assert extraction_runs.ensure_every_manual_has_run(db_session) == 1
    runs = _runs_for(db_session, manual.id)
    assert len(runs) == 1
    assert runs[0].status == extraction_runs.STATUS_PENDING
    assert runs[0].parser_version is None


def test_ensure_is_idempotent(db_session):
    manual = _insert_manual_without_run(db_session, "b" * 64)
    assert extraction_runs.ensure_every_manual_has_run(db_session) == 1
    assert extraction_runs.ensure_every_manual_has_run(db_session) == 0
    assert len(_runs_for(db_session, manual.id)) == 1


def test_ensure_never_touches_existing_runs(db_session, pdf_bytes_factory):
    manual = _upload(db_session, pdf_bytes_factory())
    before = _runs_for(db_session, manual.id)[0]
    snapshot = (before.id, before.status, before.parser_version, before.created_at)

    assert extraction_runs.ensure_every_manual_has_run(db_session) == 0

    after = _runs_for(db_session, manual.id)[0]
    assert (after.id, after.status, after.parser_version, after.created_at) == snapshot


def test_database_rejects_duplicate_run_for_same_manual(db_session, pdf_bytes_factory):
    manual = _upload(db_session, pdf_bytes_factory())
    extraction_runs.create_pending_run(db_session, manual)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
    assert len(_runs_for(db_session, manual.id)) == 1


def _set_run_status(db_session, manual_id, status, error_message=None):
    run = _runs_for(db_session, manual_id)[0]
    run.status = status
    run.error_message = error_message
    db_session.commit()
    return run


def test_interrupted_running_run_is_failed_at_cleanup(db_session, pdf_bytes_factory):
    manual = _upload(db_session, pdf_bytes_factory())
    _set_run_status(db_session, manual.id, extraction_runs.STATUS_RUNNING)

    assert extraction_runs.fail_interrupted_runs(db_session) == 1

    run = _runs_for(db_session, manual.id)[0]
    assert run.status == extraction_runs.STATUS_FAILED
    assert "interrupted" in run.error_message
    assert run.finished_at is not None


def test_cleanup_leaves_completed_and_pending_runs_untouched(db_session):
    manuals = [_insert_manual_without_run(db_session, letter * 64) for letter in ("e", "f", "g")]
    extraction_runs.ensure_every_manual_has_run(db_session)
    _set_run_status(db_session, manuals[0].id, extraction_runs.STATUS_SUCCEEDED)
    _set_run_status(db_session, manuals[1].id, extraction_runs.STATUS_FAILED, "original failure")
    # manuals[2] stays pending
    snapshots = {
        manual.id: (
            _runs_for(db_session, manual.id)[0].status,
            _runs_for(db_session, manual.id)[0].error_message,
            _runs_for(db_session, manual.id)[0].finished_at,
        )
        for manual in manuals
    }

    assert extraction_runs.fail_interrupted_runs(db_session) == 0

    for manual in manuals:
        run = _runs_for(db_session, manual.id)[0]
        assert (run.status, run.error_message, run.finished_at) == snapshots[manual.id]


def test_cleanup_is_idempotent(db_session, pdf_bytes_factory):
    manual = _upload(db_session, pdf_bytes_factory())
    _set_run_status(db_session, manual.id, extraction_runs.STATUS_RUNNING)

    assert extraction_runs.fail_interrupted_runs(db_session) == 1
    first = _runs_for(db_session, manual.id)[0]
    snapshot = (first.status, first.error_message, first.finished_at)

    assert extraction_runs.fail_interrupted_runs(db_session) == 0
    second = _runs_for(db_session, manual.id)[0]
    assert (second.status, second.error_message, second.finished_at) == snapshot


def test_deleting_manual_cascades_its_run(db_session, pdf_bytes_factory):
    manual = _upload(db_session, pdf_bytes_factory())
    manual_id = manual.id
    manuals_domain.delete_manual(db_session, manual)
    assert _runs_for(db_session, manual_id) == []
