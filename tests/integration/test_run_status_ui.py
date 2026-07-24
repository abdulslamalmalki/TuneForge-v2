"""FR-27 / FR-28: run status on the manuals list and manual detail pages."""

import io

from app.db.models import ExtractionRun, Manual, Setting
from app.db.session import SessionLocal
from app.domain import manuals as manuals_domain

RICH_PAGE = "Advanced BIOS configuration reference text for extraction tests. " * 3
SETTING_PAGE = (
    "Ai Overclock Tuner\n"
    "Allows you to select the memory overclocking profile for this system.\n"
    "Configuration options: [Auto] [Manual]"
)


def _upload_via_web(client, pdf_bytes):
    response = client.post(
        "/manuals",
        files={"file": ("manual.pdf", pdf_bytes, "application/pdf")},
        data={"vendor": "", "model_name": "", "eligibility_workflow": "legacy"},
        follow_redirects=False,
    )
    return response.headers["location"]


def _create_without_executing(pdf_bytes) -> int:
    # Domain-level upload: the run exists but no background task fires.
    with SessionLocal() as session:
        manual = manuals_domain.create_manual(
            session, io.BytesIO(pdf_bytes), original_filename="m.pdf", vendor="", model_name=""
        )
        return manual.id


def test_detail_shows_succeeded_run(client, pdf_bytes_factory):
    detail_url = _upload_via_web(client, pdf_bytes_factory(texts=[RICH_PAGE] * 2))
    page = client.get(detail_url).text
    assert "Extraction run" in page
    assert "succeeded" in page
    assert "Pages processed" in page
    assert "Settings found" in page
    assert "refresh this page" not in page


def test_detail_flags_zero_settings_success_as_warning(client, pdf_bytes_factory):
    # Plan section 10, step 7: zero-candidate success is a warning state.
    detail_url = _upload_via_web(client, pdf_bytes_factory(texts=[RICH_PAGE] * 2))
    page = client.get(detail_url).text
    assert "succeeded" in page
    assert "found no settings" in page


def test_detail_shows_no_warning_when_settings_were_found(client, pdf_bytes_factory):
    # RICH_PAGE keeps the total text above the FR-10 threshold.
    detail_url = _upload_via_web(client, pdf_bytes_factory(texts=[SETTING_PAGE, RICH_PAGE]))
    page = client.get(detail_url).text
    assert "succeeded" in page
    assert "found no settings" not in page


def test_detail_shows_no_zero_settings_warning_on_failed_run(client, pdf_bytes_factory):
    detail_url = _upload_via_web(client, pdf_bytes_factory(3))  # no text layer
    page = client.get(detail_url).text
    assert "failed" in page
    assert "found no settings" not in page


def test_detail_shows_failed_run_with_full_error(client, pdf_bytes_factory):
    detail_url = _upload_via_web(client, pdf_bytes_factory(3))  # no text layer
    page = client.get(detail_url).text
    assert "failed" in page
    assert "usable text layer" in page  # full error message (FR-28)


def test_detail_shows_pending_run_with_refresh_notice(client, pdf_bytes_factory):
    manual_id = _create_without_executing(pdf_bytes_factory())
    page = client.get(f"/manuals/{manual_id}").text
    assert "pending" in page
    assert "refresh this page" in page


def test_detail_shows_running_run_with_refresh_notice(client, pdf_bytes_factory):
    manual_id = _create_without_executing(pdf_bytes_factory())
    with SessionLocal() as session:
        run = session.query(ExtractionRun).filter_by(manual_id=manual_id).one()
        run.status = "running"
        session.commit()
    page = client.get(f"/manuals/{manual_id}").text
    assert "running" in page
    assert "refresh this page" in page


def test_detail_handles_manual_without_run(client):
    with SessionLocal() as session:
        manual = Manual(
            original_filename="legacy.pdf",
            stored_filename="manual_norun_ui.pdf",
            sha256="d" * 64,
            file_size_bytes=5,
            page_count=1,
            document_title="legacy.pdf",
            retrieved_at="2026-01-01T00:00:00+00:00",
            language="en",
            created_at="2026-01-01T00:00:00+00:00",
        )
        session.add(manual)
        session.commit()
        manual_id = manual.id
    page = client.get(f"/manuals/{manual_id}").text
    assert "No extraction run recorded" in page


def test_list_shows_status_badge_and_settings_counts(client, pdf_bytes_factory):
    detail_url = _upload_via_web(client, pdf_bytes_factory(texts=[RICH_PAGE] * 2))
    manual_id = int(detail_url.rsplit("/", 1)[1])

    # Seed settings rows directly to verify the FR-27 counts display.
    with SessionLocal() as session:
        run = session.query(ExtractionRun).filter_by(manual_id=manual_id).one()
        for status in ("draft", "approved", "approved", "rejected"):
            session.add(
                Setting(
                    manual_id=manual_id,
                    extraction_run_id=run.id,
                    extracted_name="Some Setting",
                    extracted_description="",
                    page_number=1,
                    status=status,
                    created_at="2026-01-01T00:00:00+00:00",
                    updated_at="2026-01-01T00:00:00+00:00",
                )
            )
        session.commit()

    page = client.get("/manuals").text
    assert "succeeded" in page
    assert "1 / 2 / 1" in page
    assert "no settings" not in page  # settings exist, so no warning badge


def test_list_flags_zero_settings_manual_with_warning_badge(client, pdf_bytes_factory):
    _upload_via_web(client, pdf_bytes_factory(texts=[RICH_PAGE] * 2))
    page = client.get("/manuals").text
    assert "succeeded" in page
    assert "no settings" in page


def test_list_shows_no_zero_settings_badge_for_failed_run(client, pdf_bytes_factory):
    _upload_via_web(client, pdf_bytes_factory(3))  # no text layer
    page = client.get("/manuals").text
    assert "failed" in page
    assert "no settings" not in page
