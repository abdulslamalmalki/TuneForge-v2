import io

from app.db.models import ExtractionRun, Setting
from app.domain import am5_coverage, canonical_settings
from app.domain import manuals as manuals_domain


def test_empty_report_keeps_catalog_identity_separate_from_reviewed_coverage(db_session):
    report = am5_coverage.build_report(db_session)

    assert report.summary.major_vendor_count == 4
    assert report.summary.selectable_model_count == 4
    assert report.summary.manual_count == 0
    assert report.summary.observed_canonical_setting_count == 0
    assert report.summary.expansion_status == "hold"
    assert report.canonical_settings == ()
    assert "not automatically an AM5 gap" in report.scope_notice


def test_report_counts_positive_exact_model_evidence_and_reviewed_mapping(
    db_session,
    pdf_bytes_factory,
):
    manual = manuals_domain.create_manual(
        db_session,
        io.BytesIO(pdf_bytes_factory()),
        original_filename="x870e-taichi-user-guide.pdf",
        vendor="ASRock",
        model_name="X870E Taichi White",
        document_title="X870E Taichi White Motherboard User Guide",
        chipset="X870E",
        eligibility_required=False,
    )
    manual.acceptance_reviewed_at = "2026-07-19T00:00:00+00:00"
    run = db_session.query(ExtractionRun).filter_by(manual_id=manual.id).one()
    setting = Setting(
        manual_id=manual.id,
        extraction_run_id=run.id,
        extracted_name="Precision Boost Overdrive",
        extracted_description="Controls Precision Boost Overdrive.",
        page_number=10,
        status="approved",
        page_review_status="verified",
        created_at="2026-07-19T00:00:00+00:00",
        updated_at="2026-07-19T00:00:00+00:00",
    )
    db_session.add(setting)
    db_session.commit()
    identity = canonical_settings.create_identity(
        db_session,
        name="Precision Boost Overdrive",
    )
    canonical_settings.map_setting(
        db_session,
        setting,
        canonical_id=identity.canonical_id,
    )

    report = am5_coverage.build_report(db_session)

    assert report.summary.manual_count == 1
    assert report.summary.certified_manual_count == 1
    assert report.summary.approved_setting_count == 1
    assert report.summary.mapped_setting_count == 1
    assert report.summary.observed_canonical_setting_count == 1
    asrock = next(row for row in report.vendors if row.vendor_id == "asrock")
    assert asrock.approved_setting_count == 1
    assert asrock.canonical_setting_count == 1
    taichi = next(row for row in report.models if row.model_id == "asrock-x870e-taichi-white")
    assert taichi.evidence_level == "exact_model"
    assert taichi.exact_approved_setting_count == 1
    exact_gate = next(row for row in report.gates if row.gate_id == "exact_model_validation")
    assert exact_gate.passed is True


def test_broad_manual_coverage_remains_motherboard_vendor_scoped(
    db_session,
    pdf_bytes_factory,
):
    manual = manuals_domain.create_manual(
        db_session,
        io.BytesIO(pdf_bytes_factory()),
        original_filename="msi-am5-bios-guide.pdf",
        vendor="MSI",
        model_name="AMD AM5 series",
        document_title="AMD AM5 series BIOS User Guide",
        chipset="AMD AM5 series",
        eligibility_required=False,
    )
    run = db_session.query(ExtractionRun).filter_by(manual_id=manual.id).one()
    db_session.add(
        Setting(
            manual_id=manual.id,
            extraction_run_id=run.id,
            extracted_name="Precision Boost Overdrive",
            extracted_description="Controls Precision Boost Overdrive.",
            page_number=10,
            status="approved",
            page_review_status="verified",
            created_at="2026-07-19T00:00:00+00:00",
            updated_at="2026-07-19T00:00:00+00:00",
        )
    )
    db_session.commit()

    report = am5_coverage.build_report(db_session)

    taichi = next(row for row in report.models if row.model_id == "asrock-x870e-taichi-white")
    msi = next(row for row in report.models if row.model_id == "msi-mpg-x670e-carbon-wifi")
    assert taichi.compatible_approved_setting_count == 0
    assert taichi.evidence_level == "none"
    assert msi.compatible_approved_setting_count == 1
    assert msi.evidence_level == "broad"
