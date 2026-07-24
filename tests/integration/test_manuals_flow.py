from pathlib import Path

from sqlalchemy import select

from app import config
from app.db.models import ExtractionRun
from app.db.session import SessionLocal
from app.extraction import settings_parser
from app.storage import files


def _upload(client, content, filename="manual.pdf", vendor="", model_name=""):
    return client.post(
        "/manuals",
        files={"file": (filename, content, "application/pdf")},
        data={
            "vendor": vendor,
            "model_name": model_name,
            "eligibility_workflow": "legacy",
        },
        follow_redirects=False,
    )


def test_upload_happy_path(client, pdf_bytes_factory):
    pdf = pdf_bytes_factory(3)
    response = _upload(client, pdf, vendor="ASUS", model_name="Test Board")
    assert response.status_code == 303
    detail_url = response.headers["location"]

    detail = client.get(detail_url)
    assert detail.status_code == 200
    assert "ASUS" in detail.text
    assert "Test Board" in detail.text
    assert "manual.pdf" in detail.text
    assert ">3<" in detail.text  # page count

    listing = client.get("/manuals")
    assert detail_url in listing.text
    assert "No manuals yet" not in listing.text

    served = client.get(f"{detail_url}/file")
    assert served.status_code == 200
    assert served.headers["content-type"] == "application/pdf"
    assert served.content == pdf

    manual_id = int(detail_url.rsplit("/", 1)[1])
    assert (config.UPLOADS_DIR / f"manual_{manual_id}.pdf").is_file()


def test_upload_triggers_background_extraction(client, pdf_bytes_factory):
    # The test client executes background tasks as part of the request cycle.
    rich_page = "Advanced BIOS configuration reference text for extraction tests. " * 3
    response = _upload(client, pdf_bytes_factory(texts=[rich_page] * 2))
    manual_id = int(response.headers["location"].rsplit("/", 1)[1])

    with SessionLocal() as session:
        run = session.scalars(
            select(ExtractionRun).where(ExtractionRun.manual_id == manual_id)
        ).one()
        assert run.status == "succeeded"
        assert run.pages_processed == 2
        assert run.settings_found == 0  # prose-only pages yield no candidates
        assert run.parser_version == settings_parser.PARSER_VERSION


def test_uploading_scanned_fixture_fails_run_with_no_text_layer(client):
    fixture = (Path(__file__).parent.parent / "fixtures" / "pdfs" / "scanned_like.pdf").read_bytes()
    response = _upload(client, fixture, filename="scanned_like.pdf")
    manual_id = int(response.headers["location"].rsplit("/", 1)[1])

    with SessionLocal() as session:
        run = session.scalars(
            select(ExtractionRun).where(ExtractionRun.manual_id == manual_id)
        ).one()
        assert run.status == "failed"
        assert "usable text layer" in run.error_message


def test_upload_rejects_non_pdf_content(client):
    response = _upload(client, b"definitely not a pdf", filename="fake.pdf")
    assert response.status_code == 400
    assert "could not be read as a PDF" in response.text
    assert list(config.UPLOADS_DIR.glob("*")) == []


def test_upload_rejects_oversized_file(client, pdf_bytes_factory, monkeypatch):
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 10)
    response = _upload(client, pdf_bytes_factory())
    assert response.status_code == 400
    assert "maximum allowed size" in response.text
    assert list(config.UPLOADS_DIR.glob("*")) == []


def test_upload_rejects_duplicate_and_links_existing(client, pdf_bytes_factory):
    pdf = pdf_bytes_factory()
    first = _upload(client, pdf)
    existing_url = first.headers["location"]

    duplicate = _upload(client, pdf, filename="same-content.pdf")
    assert duplicate.status_code == 400
    assert "already been uploaded" in duplicate.text
    assert f'href="{existing_url}"' in duplicate.text


def test_upload_rejects_overlong_vendor_label(client, pdf_bytes_factory):
    response = _upload(client, pdf_bytes_factory(), vendor="x" * 101)
    assert response.status_code == 400
    assert "100 characters or fewer" in response.text


def test_missing_manual_returns_404_page(client):
    response = client.get("/manuals/9999")
    assert response.status_code == 404
    assert "Manual not found" in response.text


def test_missing_stored_file_returns_500_page(client, pdf_bytes_factory):
    detail_url = _upload(client, pdf_bytes_factory()).headers["location"]
    manual_id = int(detail_url.rsplit("/", 1)[1])
    (config.UPLOADS_DIR / f"manual_{manual_id}.pdf").unlink()

    response = client.get(f"{detail_url}/file")
    assert response.status_code == 500
    assert "missing" in response.text
    assert "Traceback" not in response.text


def test_delete_requires_explicit_confirmation(client, pdf_bytes_factory):
    detail_url = _upload(client, pdf_bytes_factory()).headers["location"]

    response = client.post(f"{detail_url}/delete")
    assert response.status_code == 200
    assert "permanently deletes" in response.text
    assert client.get(detail_url).status_code == 200  # still exists


def test_delete_filesystem_failure_shows_error_and_keeps_manual(
    client, pdf_bytes_factory, monkeypatch
):
    detail_url = _upload(client, pdf_bytes_factory()).headers["location"]
    manual_id = int(detail_url.rsplit("/", 1)[1])

    def _raise_os_error(stored_filename):
        raise OSError("simulated filesystem failure")

    monkeypatch.setattr(files, "stage_file_deletion", _raise_os_error)
    response = client.post(f"{detail_url}/delete", data={"confirm": "yes"}, follow_redirects=False)
    monkeypatch.undo()

    assert response.status_code == 500
    assert "was not deleted" in response.text
    assert "Traceback" not in response.text
    assert "OSError" not in response.text
    assert client.get(detail_url).status_code == 200  # record preserved
    assert (config.UPLOADS_DIR / f"manual_{manual_id}.pdf").is_file()


def test_delete_with_confirmation_removes_row_and_file(client, pdf_bytes_factory):
    detail_url = _upload(client, pdf_bytes_factory()).headers["location"]
    manual_id = int(detail_url.rsplit("/", 1)[1])

    response = client.post(f"{detail_url}/delete", data={"confirm": "yes"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/manuals"
    assert client.get(detail_url).status_code == 404
    assert not (config.UPLOADS_DIR / f"manual_{manual_id}.pdf").exists()
    assert "No manuals yet" in client.get("/manuals").text
