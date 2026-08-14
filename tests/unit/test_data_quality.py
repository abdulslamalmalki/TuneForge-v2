"""Regression coverage for the reusable read-only data-quality audit."""

import hashlib
from datetime import UTC, datetime

from app import config
from app.db.models import Manual
from app.domain import data_quality

NOW = "2026-08-14T00:00:00+00:00"


def test_audit_detects_missing_relations_and_file_hash_without_mutating(db_session):
    payload = b"not the recorded bytes"
    stored_filename = "quality-audit-manual.pdf"
    path = config.UPLOADS_DIR / stored_filename
    path.write_bytes(payload)
    manual = Manual(
        original_filename="quality.pdf",
        stored_filename=stored_filename,
        sha256=hashlib.sha256(b"different").hexdigest(),
        file_size_bytes=len(payload),
        page_count=1,
        vendor="ASUS",
        vendor_id="asus",
        model_name="Test board",
        document_title="Quality audit manual",
        source_url="https://www.asus.com/support/",
        document_version="1",
        published_on=None,
        retrieved_at=NOW,
        language="en",
        board_revision=None,
        chipset="AMD X870",
        document_type="bios_guide",
        eligibility_status="pending",
        eligibility_confidence=None,
        eligibility_reasons=None,
        eligibility_reviewed_at=None,
        eligibility_reviewer=None,
        eligibility_note=None,
        acceptance_reviewed_at=None,
        acceptance_reviewer=None,
        acceptance_notes=None,
        created_at=NOW,
    )
    db_session.add(manual)
    db_session.commit()

    report = data_quality.build_report(
        db_session,
        now=datetime(2026, 8, 14, tzinfo=UTC),
    )
    codes = {item.code for item in report.findings}
    assert report.status == "fail"
    assert "manual_missing_extraction_run" in codes
    assert "manual_missing_applicability" in codes
    assert "stored_file_hash" in codes
    assert db_session.get(Manual, manual.id) is not None

    path.unlink()


class _BiosScope:
    claim_id = 7
    valid_from = "3.10"
    valid_until = "3.2"
    comparison_mode = "dotted_numeric"


def test_bios_range_check_is_numeric_not_lexical():
    assert "starts" in data_quality._bios_range_issue(_BiosScope())
