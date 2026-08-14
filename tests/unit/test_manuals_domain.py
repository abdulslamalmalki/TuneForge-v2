import io
import json
from datetime import datetime

import pytest

from app import config
from app.domain import manuals as manuals_domain
from app.domain.errors import (
    DuplicateManualError,
    ManualDeletionError,
    ManualProvenanceError,
    UploadRejectedError,
)
from app.storage import files

ASUS_SUPPORT_URL = "https://www.asus.com/supportonly/b650-test/helpdesk_manual/"


def _create(db_session, pdf_bytes, filename="manual.pdf", vendor="", model_name=""):
    return manuals_domain.create_manual(
        db_session,
        io.BytesIO(pdf_bytes),
        original_filename=filename,
        vendor=vendor,
        model_name=model_name,
    )


def test_create_manual_records_metadata(db_session, pdf_bytes_factory):
    pdf = pdf_bytes_factory(3)
    manual = _create(db_session, pdf, filename="board.pdf", vendor="ASUS", model_name="Test Board")
    assert manual.page_count == 3
    assert manual.file_size_bytes == len(pdf)
    assert manual.original_filename == "board.pdf"
    assert manual.vendor == "ASUS"
    assert manual.model_name == "Test Board"
    assert manual.document_title == "board.pdf"
    assert manual.language == "en"
    assert manual.retrieved_at == manual.created_at
    datetime.fromisoformat(manual.created_at)  # UTC ISO-8601 per plan section 9
    events = manuals_domain.list_manual_events(db_session, manual.id)
    assert [event.action for event in events] == [
        manuals_domain.ACTION_PROVENANCE_CREATED,
        "hardware_applicability_normalized",
    ]
    assert json.loads(events[0].detail)["after"]["document_title"] == "board.pdf"


def test_create_manual_records_complete_provenance(db_session, pdf_bytes_factory):
    manual = manuals_domain.create_manual(
        db_session,
        io.BytesIO(pdf_bytes_factory()),
        original_filename="board.pdf",
        vendor=" ASUS ",
        model_name=" B650 Test ",
        document_title=" BIOS Setup Guide ",
        source_url=ASUS_SUPPORT_URL,
        document_version="1.2",
        published_on="2026-06-01",
        language="en-us",
        board_revision="1.0",
        chipset="B650",
    )
    assert manual.vendor == "ASUS"
    assert manual.vendor_id == "asus"
    assert manual.model_name == "B650 Test"
    assert manual.document_title == "BIOS Setup Guide"
    assert manual.language == "en-US"
    assert manuals_domain.missing_provenance_fields(manual) == []
    event = manuals_domain.list_manual_events(db_session, manual.id)[0]
    normalization = json.loads(event.detail)["vendor_normalization"]
    assert normalization["raw_value"] == " ASUS "
    assert normalization["vendor_id"] == "asus"
    assert normalization["canonical_name"] == "ASUS"


def test_vendor_alias_is_canonicalized_and_raw_value_is_audited(db_session, pdf_bytes_factory):
    manual = _create(
        db_session,
        pdf_bytes_factory(),
        vendor="Gigabyte Technology",
    )
    assert (manual.vendor, manual.vendor_id) == ("GIGABYTE", "gigabyte")
    detail = json.loads(manuals_domain.list_manual_events(db_session, manual.id)[0].detail)
    assert detail["vendor_normalization"]["raw_value"] == "Gigabyte Technology"
    assert detail["vendor_normalization"]["matched_alias"] == "Gigabyte Technology"


def test_unknown_vendor_is_rejected_but_blank_intake_remains_allowed(db_session, pdf_bytes_factory):
    with pytest.raises(UploadRejectedError, match="supported vendors"):
        _create(db_session, pdf_bytes_factory(), vendor="Example Boards")
    blank = _create(
        db_session,
        pdf_bytes_factory(),
        filename="blank-vendor.pdf",
        vendor="",
    )
    assert blank.vendor is None
    assert blank.vendor_id is None


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("source_url", "file:///manual.pdf", "HTTP or HTTPS"),
        ("source_url", "https://user:secret@example.com/manual.pdf", "credentials"),
        ("source_url", "https://example.com/manual file.pdf", "whitespace"),
        ("source_url", "https://example.com:444/manual.pdf", "standard HTTP"),
        ("source_url", "https://example.com:notaport/manual.pdf", "valid HTTP"),
        ("source_url", "https://example.com\\@evil.test/manual.pdf", "backslashes"),
        ("published_on", "06/01/2026", "YYYY-MM-DD"),
        ("language", "english", "valid tag"),
    ],
)
def test_invalid_provenance_rejects_upload_without_files(
    db_session, pdf_bytes_factory, field, value, message
):
    kwargs = {field: value}
    with pytest.raises(UploadRejectedError, match=message):
        manuals_domain.create_manual(
            db_session,
            io.BytesIO(pdf_bytes_factory()),
            original_filename="board.pdf",
            vendor="ASUS",
            model_name="Test",
            **kwargs,
        )
    assert list(config.UPLOADS_DIR.glob("*")) == []


def test_update_provenance_audits_changes_and_preserves_ingestion_facts(
    db_session, pdf_bytes_factory
):
    manual = _create(db_session, pdf_bytes_factory(), filename="board.pdf")
    retrieved_at = manual.retrieved_at
    sha256 = manual.sha256

    changed = manuals_domain.update_manual_provenance(
        db_session,
        manual,
        vendor="ASUS",
        model_name="B650 Test",
        document_title="Official BIOS Guide",
        source_url=ASUS_SUPPORT_URL,
        document_version="1.0",
        published_on="2026-01-02",
        language="en-US",
        board_revision="1.1",
        chipset="B650",
    )

    assert changed is True
    assert manual.retrieved_at == retrieved_at
    assert manual.sha256 == sha256
    assert manuals_domain.missing_provenance_fields(manual) == []
    events = manuals_domain.list_manual_events(db_session, manual.id)
    assert [event.action for event in events] == [
        manuals_domain.ACTION_PROVENANCE_CREATED,
        "hardware_applicability_normalized",
        manuals_domain.ACTION_PROVENANCE_UPDATED,
        "hardware_applicability_normalized",
    ]
    change = json.loads(events[-2].detail)
    assert change["before"]["source_url"] is None
    assert change["after"]["source_url"] == ASUS_SUPPORT_URL

    no_change = manuals_domain.update_manual_provenance(
        db_session,
        manual,
        vendor="ASUS",
        model_name="B650 Test",
        document_title="Official BIOS Guide",
        source_url=ASUS_SUPPORT_URL,
        document_version="1.0",
        published_on="2026-01-02",
        language="en-US",
        board_revision="1.1",
        chipset="B650",
    )
    assert no_change is False
    assert len(manuals_domain.list_manual_events(db_session, manual.id)) == 4


def test_generic_source_url_is_preserved_but_not_certification_ready(db_session, pdf_bytes_factory):
    manual = manuals_domain.create_manual(
        db_session,
        io.BytesIO(pdf_bytes_factory()),
        original_filename="legacy-reference.pdf",
        vendor="ASUS",
        model_name="B650 Test",
        document_title="Legacy reference",
        source_url="https://example.com/reference.pdf",
    )
    assert manual.source_url == "https://example.com/reference.pdf"
    assert manuals_domain.missing_provenance_fields(manual) == ["official_support_page"]


def test_invalid_update_is_atomic(db_session, pdf_bytes_factory):
    manual = _create(db_session, pdf_bytes_factory(), vendor="ASUS")
    before = manuals_domain.provenance_snapshot(manual)
    with pytest.raises(ManualProvenanceError, match="HTTP or HTTPS"):
        manuals_domain.update_manual_provenance(
            db_session,
            manual,
            vendor="MSI",
            model_name="Changed",
            document_title="Changed",
            source_url="not-a-url",
            document_version="",
            published_on="",
            language="en",
            board_revision="",
            chipset="",
        )
    assert manuals_domain.provenance_snapshot(manual) == before
    assert len(manuals_domain.list_manual_events(db_session, manual.id)) == 2


def test_hostile_filename_never_used_as_path(db_session, pdf_bytes_factory):
    manual = _create(db_session, pdf_bytes_factory(), filename="../../evil.pdf")
    assert manual.stored_filename == f"manual_{manual.id}.pdf"
    assert manual.original_filename == "../../evil.pdf"
    stored = config.UPLOADS_DIR / manual.stored_filename
    assert stored.is_file()


def test_duplicate_content_is_rejected_with_existing_id(db_session, pdf_bytes_factory):
    pdf = pdf_bytes_factory()
    first = _create(db_session, pdf)
    with pytest.raises(DuplicateManualError) as excinfo:
        _create(db_session, pdf, filename="other-name.pdf")
    assert excinfo.value.existing_id == first.id


def test_non_pdf_content_is_rejected_and_leaves_no_file(db_session):
    with pytest.raises(UploadRejectedError, match="could not be read as a PDF"):
        _create(db_session, b"this is not a pdf at all")
    assert list(config.UPLOADS_DIR.glob("*")) == []


def test_label_longer_than_limit_is_rejected(db_session, pdf_bytes_factory):
    with pytest.raises(UploadRejectedError, match="100 characters or fewer"):
        _create(db_session, pdf_bytes_factory(), vendor="x" * 101)
    assert list(config.UPLOADS_DIR.glob("*")) == []


def test_blank_labels_are_stored_as_none(db_session, pdf_bytes_factory):
    manual = _create(db_session, pdf_bytes_factory(), vendor="  ", model_name="")
    assert manual.vendor is None
    assert manual.model_name is None


def test_delete_manual_removes_row_and_file(db_session, pdf_bytes_factory):
    manual = _create(db_session, pdf_bytes_factory())
    stored = config.UPLOADS_DIR / manual.stored_filename
    assert stored.is_file()
    manuals_domain.delete_manual(db_session, manual)
    assert manuals_domain.get_manual(db_session, manual.id) is None
    assert not stored.exists()
    assert list(config.UPLOADS_DIR.glob("*.deleting")) == []


def test_delete_keeps_record_when_filesystem_fails(db_session, pdf_bytes_factory, monkeypatch):
    manual = _create(db_session, pdf_bytes_factory())
    stored = config.UPLOADS_DIR / manual.stored_filename

    def _raise_os_error(stored_filename):
        raise OSError("simulated filesystem failure")

    monkeypatch.setattr(files, "stage_file_deletion", _raise_os_error)
    with pytest.raises(ManualDeletionError, match="was not deleted"):
        manuals_domain.delete_manual(db_session, manual)

    assert manuals_domain.get_manual(db_session, manual.id) is not None
    assert stored.is_file()


def test_delete_restores_file_when_database_fails(db_session, pdf_bytes_factory, monkeypatch):
    manual = _create(db_session, pdf_bytes_factory())
    stored = config.UPLOADS_DIR / manual.stored_filename

    def _raise_db_error():
        raise RuntimeError("simulated database failure")

    monkeypatch.setattr(db_session, "commit", _raise_db_error)
    with pytest.raises(ManualDeletionError, match="could not be deleted"):
        manuals_domain.delete_manual(db_session, manual)
    monkeypatch.undo()

    assert manuals_domain.get_manual(db_session, manual.id) is not None
    assert stored.is_file()
    assert list(config.UPLOADS_DIR.glob("*.deleting")) == []
