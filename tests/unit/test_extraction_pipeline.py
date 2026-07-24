import io

import pytest
from sqlalchemy import func, select

from app import config
from app.db.models import ExtractionRun, Manual, Setting
from app.domain import extraction_pipeline
from app.domain import manuals as manuals_domain
from app.domain.errors import ManualNotFoundError
from app.extraction import pdf_text, settings_parser


def _upload(db_session, pdf_bytes):
    return manuals_domain.create_manual(
        db_session,
        io.BytesIO(pdf_bytes),
        original_filename="manual.pdf",
        vendor="",
        model_name="",
    )


def test_pipeline_returns_page_text_for_manual(db_session, pdf_bytes_factory):
    manual = _upload(db_session, pdf_bytes_factory(texts=["Alpha page", "Bravo page"]))
    result = extraction_pipeline.extract_manual_pages(db_session, manual.id)
    assert result.page_count == 2
    assert [page.page_number for page in result.pages] == [1, 2]
    assert "Alpha page" in result.pages[0].text
    assert "Bravo page" in result.pages[1].text


def test_pipeline_invokes_reader_with_stored_path(db_session, pdf_bytes_factory, monkeypatch):
    manual = _upload(db_session, pdf_bytes_factory())
    sentinel = pdf_text.PdfText(page_count=0, pages=[])
    seen_paths = []

    def _spy(path):
        seen_paths.append(path)
        return sentinel

    monkeypatch.setattr(pdf_text, "read_pdf_text", _spy)
    result = extraction_pipeline.extract_manual_pages(db_session, manual.id)

    assert result is sentinel  # reader output passes through verbatim
    assert seen_paths == [config.UPLOADS_DIR / f"manual_{manual.id}.pdf"]


def test_unknown_manual_raises_structured_error(db_session):
    with pytest.raises(ManualNotFoundError) as excinfo:
        extraction_pipeline.extract_manual_pages(db_session, 9999)
    assert excinfo.value.manual_id == 9999
    assert excinfo.value.message


def test_missing_stored_file_error_propagates(db_session, pdf_bytes_factory):
    manual = _upload(db_session, pdf_bytes_factory())
    (config.UPLOADS_DIR / manual.stored_filename).unlink()
    with pytest.raises(pdf_text.PdfReadError) as excinfo:
        extraction_pipeline.extract_manual_pages(db_session, manual.id)
    assert excinfo.value.reason == pdf_text.REASON_MISSING_FILE


def test_unreadable_stored_file_error_propagates(db_session, pdf_bytes_factory):
    manual = _upload(db_session, pdf_bytes_factory())
    (config.UPLOADS_DIR / manual.stored_filename).write_bytes(b"%PDF-1.7\nnot really a pdf")
    with pytest.raises(pdf_text.PdfReadError) as excinfo:
        extraction_pipeline.extract_manual_pages(db_session, manual.id)
    assert excinfo.value.reason == pdf_text.REASON_UNREADABLE


def _run_for(db_session, manual_id):
    db_session.expire_all()
    return db_session.scalars(
        select(ExtractionRun).where(ExtractionRun.manual_id == manual_id)
    ).one()


# Enough text per page to stay clearly above the FR-10 threshold.
_RICH_PAGE = "Advanced BIOS configuration reference text for extraction tests. " * 3


def test_execute_pending_run_succeeds(db_session, pdf_bytes_factory):
    manual = _upload(db_session, pdf_bytes_factory(texts=[_RICH_PAGE] * 3))
    extraction_pipeline.execute_pending_run(manual.id)

    run = _run_for(db_session, manual.id)
    assert run.status == "succeeded"
    assert run.pages_processed == 3
    assert run.settings_found == 0  # prose-only pages yield no candidates
    assert run.parser_version == settings_parser.PARSER_VERSION  # parser ran
    assert run.started_at is not None
    assert run.finished_at is not None
    assert run.error_message is None


# One heading (title-case run) plus body/description lines per page; padded
# so two pages comfortably clear the FR-10 threshold.
_SETTINGS_PAGE_ONE = (
    "Ai Overclock Tuner\n"
    "Allows you to select the memory overclocking profile for this system.\n"
    "Configuration options: [Auto] [Manual]"
)
_SETTINGS_PAGE_TWO = (
    "DRAM Voltage Control\nSets the voltage supplied to the installed memory modules on this board."
)


def _settings_for(db_session, manual_id):
    db_session.expire_all()
    return db_session.scalars(
        select(Setting).where(Setting.manual_id == manual_id).order_by(Setting.page_number)
    ).all()


def test_execute_pending_run_creates_draft_settings(db_session, pdf_bytes_factory):
    manual = _upload(db_session, pdf_bytes_factory(texts=[_SETTINGS_PAGE_ONE, _SETTINGS_PAGE_TWO]))
    extraction_pipeline.execute_pending_run(manual.id)

    run = _run_for(db_session, manual.id)
    drafts = _settings_for(db_session, manual.id)

    assert run.status == "succeeded"
    assert run.settings_found == 2 == len(drafts)
    assert run.parser_version == settings_parser.PARSER_VERSION
    first, second = drafts
    assert first.extracted_name == "Ai Overclock Tuner"
    assert first.extracted_description == (
        "Allows you to select the memory overclocking profile for this system.\n"
        "Configuration options: [Auto] [Manual]"
    )
    assert (first.page_number, second.page_number) == (1, 2)
    assert second.extracted_name == "DRAM Voltage Control"
    assert second.extracted_description == (
        "Sets the voltage supplied to the installed memory modules on this board."
    )
    for draft in drafts:
        assert draft.status == "draft"
        assert draft.extraction_run_id == run.id
        assert draft.edited_name is None and draft.edited_description is None
        assert draft.created_at and draft.updated_at


def test_failed_run_creates_no_drafts_and_no_parser_version(db_session, pdf_bytes_factory):
    manual = _upload(db_session, pdf_bytes_factory(3))  # no text layer -> FR-10 failure
    extraction_pipeline.execute_pending_run(manual.id)

    run = _run_for(db_session, manual.id)
    assert run.status == "failed"
    assert run.parser_version is None  # no parser processed the document
    assert run.settings_found == 0
    assert _settings_for(db_session, manual.id) == []


def test_parser_crash_fails_run_without_partial_drafts(db_session, pdf_bytes_factory, monkeypatch):
    manual = _upload(db_session, pdf_bytes_factory(texts=[_SETTINGS_PAGE_ONE, _SETTINGS_PAGE_TWO]))

    def _boom(pdf):
        raise RuntimeError("secret parser detail")

    monkeypatch.setattr(extraction_pipeline.settings_parser, "parse_settings", _boom)
    extraction_pipeline.execute_pending_run(manual.id)

    run = _run_for(db_session, manual.id)
    assert run.status == "failed"
    assert run.error_message == extraction_pipeline.UNEXPECTED_FAILURE_MESSAGE
    assert "secret" not in run.error_message
    assert run.pages_processed == 2  # preserved through the rollback
    assert run.parser_version is None
    assert _settings_for(db_session, manual.id) == []


def test_scanned_pdf_without_text_fails_with_explicit_reason(db_session, pdf_bytes_factory):
    manual = _upload(db_session, pdf_bytes_factory(3))  # image-like: pages with no text
    extraction_pipeline.execute_pending_run(manual.id)

    run = _run_for(db_session, manual.id)
    assert run.status == "failed"
    assert "usable text layer" in run.error_message
    assert run.pages_processed == 3  # pages were read before the judgment
    assert run.finished_at is not None


def test_nearly_empty_pdf_fails_threshold(db_session, pdf_bytes_factory):
    manual = _upload(db_session, pdf_bytes_factory(texts=["Hi"]))
    extraction_pipeline.execute_pending_run(manual.id)
    run = _run_for(db_session, manual.id)
    assert run.status == "failed"
    assert "usable text layer" in run.error_message


def test_text_exactly_at_threshold_succeeds(db_session, pdf_bytes_factory):
    manual = _upload(db_session, pdf_bytes_factory(texts=["x" * config.MIN_EXTRACTABLE_TEXT_CHARS]))
    extraction_pipeline.execute_pending_run(manual.id)
    assert _run_for(db_session, manual.id).status == "succeeded"


def test_text_one_char_below_threshold_fails(db_session, pdf_bytes_factory):
    manual = _upload(
        db_session, pdf_bytes_factory(texts=["x" * (config.MIN_EXTRACTABLE_TEXT_CHARS - 1)])
    )
    extraction_pipeline.execute_pending_run(manual.id)
    run = _run_for(db_session, manual.id)
    assert run.status == "failed"
    assert "usable text layer" in run.error_message


def test_threshold_counts_text_cumulatively_across_pages(db_session, pdf_bytes_factory):
    half = config.MIN_EXTRACTABLE_TEXT_CHARS // 2
    manual = _upload(db_session, pdf_bytes_factory(texts=["a" * half, "b" * half]))
    extraction_pipeline.execute_pending_run(manual.id)
    assert _run_for(db_session, manual.id).status == "succeeded"


def test_execute_pending_run_fails_on_missing_file(db_session, pdf_bytes_factory):
    manual = _upload(db_session, pdf_bytes_factory())
    (config.UPLOADS_DIR / manual.stored_filename).unlink()
    extraction_pipeline.execute_pending_run(manual.id)

    run = _run_for(db_session, manual.id)
    assert run.status == "failed"
    assert "missing" in run.error_message
    assert run.finished_at is not None


def test_execute_pending_run_fails_on_unreadable_file(db_session, pdf_bytes_factory):
    manual = _upload(db_session, pdf_bytes_factory())
    (config.UPLOADS_DIR / manual.stored_filename).write_bytes(b"%PDF-1.7\nnot really a pdf")
    extraction_pipeline.execute_pending_run(manual.id)

    run = _run_for(db_session, manual.id)
    assert run.status == "failed"
    assert "could not be read" in run.error_message


def test_execute_pending_run_records_safe_message_on_unexpected_error(
    db_session, pdf_bytes_factory, monkeypatch
):
    manual = _upload(db_session, pdf_bytes_factory())

    def _boom(session, manual_id):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(extraction_pipeline, "extract_manual_pages", _boom)
    extraction_pipeline.execute_pending_run(manual.id)

    run = _run_for(db_session, manual.id)
    assert run.status == "failed"
    assert run.error_message == extraction_pipeline.UNEXPECTED_FAILURE_MESSAGE
    assert "secret" not in run.error_message


def test_execute_skips_run_that_is_not_pending(db_session, pdf_bytes_factory):
    manual = _upload(db_session, pdf_bytes_factory())
    extraction_pipeline.execute_pending_run(manual.id)
    first = _run_for(db_session, manual.id)
    snapshot = (first.status, first.started_at, first.finished_at, first.pages_processed)

    extraction_pipeline.execute_pending_run(manual.id)  # second call is a no-op

    second = _run_for(db_session, manual.id)
    assert (second.status, second.started_at, second.finished_at, second.pages_processed) == (
        snapshot
    )


def test_execute_handles_vanished_manual_without_crashing(db_session, pdf_bytes_factory):
    manual = _upload(db_session, pdf_bytes_factory())
    manual_id = manual.id
    manuals_domain.delete_manual(db_session, manual)  # run cascades away
    extraction_pipeline.execute_pending_run(manual_id)  # must not raise


def test_pipeline_has_no_database_side_effects(db_session, pdf_bytes_factory):
    manual = _upload(db_session, pdf_bytes_factory(texts=["Some page"]))
    counts_before = {
        table: db_session.scalar(select(func.count()).select_from(table))
        for table in (Manual, ExtractionRun, Setting)
    }
    run_before = db_session.scalars(
        select(ExtractionRun).where(ExtractionRun.manual_id == manual.id)
    ).one()
    snapshot = (run_before.status, run_before.parser_version, run_before.pages_processed)

    extraction_pipeline.extract_manual_pages(db_session, manual.id)

    db_session.expire_all()
    counts_after = {
        table: db_session.scalar(select(func.count()).select_from(table))
        for table in (Manual, ExtractionRun, Setting)
    }
    run_after = db_session.scalars(
        select(ExtractionRun).where(ExtractionRun.manual_id == manual.id)
    ).one()

    assert counts_after == counts_before
    assert counts_after[Setting] == 0  # the read-only helper creates no drafts
    assert (run_after.status, run_after.parser_version, run_after.pages_processed) == snapshot
    assert run_after.status == "pending"  # extract_manual_pages never touches the run
