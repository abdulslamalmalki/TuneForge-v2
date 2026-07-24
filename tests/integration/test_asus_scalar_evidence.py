import io

from sqlalchemy import func, select

from app.db.models import EvidenceSource, ExtractionRun, GuidanceClaim, Setting
from app.domain import canonical_settings
from app.domain import manuals as manuals_domain
from tools import apply_asus_scalar_evidence as scalar_tool

PROSE = "Official ASUS BIOS reference text for deterministic extraction. " * 5
SCALAR_PAGE = (
    "Customized Precision Boost Overdrive Scalar\n"
    "Precision Boost Overdrive increases the maximum boost voltage used and the amount "
    "of time spent at that voltage. The larger the value entered the larger the boost "
    "voltage used and the longer that voltage will be maintained.\n"
    "Configuration options: [1X] - [10X] " + PROSE
)


def test_scalar_evidence_tool_is_reviewed_idempotent_and_closes_its_question(
    db_session,
    pdf_bytes_factory,
    monkeypatch,
):
    pdf = pdf_bytes_factory(texts=[PROSE] * 29 + [SCALAR_PAGE])
    manual = manuals_domain.create_manual(
        db_session,
        io.BytesIO(pdf),
        original_filename="asus-amd800-scalar.pdf",
        vendor="ASUS",
        model_name="AMD 800 Series",
        document_title=scalar_tool.TITLE,
        source_url=scalar_tool.SOURCE_URL,
        document_version=scalar_tool.DOCUMENT_VERSION,
        published_on=scalar_tool.PUBLISHED_ON,
        language=scalar_tool.LANGUAGE,
        chipset="AMD 800 Series",
        eligibility_required=False,
    )
    run = db_session.scalar(select(ExtractionRun).where(ExtractionRun.manual_id == manual.id))
    setting = Setting(
        manual_id=manual.id,
        extraction_run_id=run.id,
        extracted_name="Precision Boost Overdrive Scalar",
        extracted_description="Controls PBO Scalar.",
        page_number=30,
        status="approved",
        page_review_status="verified",
        created_at="2026-07-19T00:00:00+00:00",
        updated_at="2026-07-19T00:00:00+00:00",
    )
    db_session.add(setting)
    db_session.commit()
    identity = canonical_settings.create_identity(db_session, name="PBO Scalar")
    canonical_settings.map_setting(
        db_session,
        setting,
        canonical_id=identity.canonical_id,
        note="Reviewed test mapping.",
    )
    monkeypatch.setattr(scalar_tool, "MANUAL_SHA256", manual.sha256)

    preview = scalar_tool.apply_scalar_evidence(db_session, apply=False)
    assert preview.mode == "dry-run"
    assert preview.source_action == "create_and_approve"
    assert preview.claim_action == "create_and_approve"

    first = scalar_tool.apply_scalar_evidence(db_session, apply=True)
    counts_before = scalar_tool.audit_counts(db_session, first.source_id, first.claim_id)
    second = scalar_tool.apply_scalar_evidence(db_session, apply=True)
    counts_after = scalar_tool.audit_counts(db_session, second.source_id, second.claim_id)

    assert first.source_id is not None
    assert first.passage_id is not None
    assert first.claim_id is not None
    assert first.recommendations_created == 0
    assert second.source_action == "reuse"
    assert second.claim_action == "reuse"
    assert counts_after == counts_before == {"source_events": 2, "claim_events": 2}
    assert db_session.scalar(select(func.count(EvidenceSource.id))) == 1
    assert db_session.scalar(select(func.count(GuidanceClaim.id))) == 1
    claim = db_session.get(GuidanceClaim, first.claim_id)
    assert claim.status == "approved"
    assert claim.claim_type == "behavior"
    assert claim.applicability_scope == "source_scope"
    assert "larger boost voltage" in claim.claim_text
    scalar_question = next(
        row
        for row in scalar_tool.am5_question_acceptance.build_report(db_session).questions
        if row.question_id == "pbo-scalar-behavior"
    )
    assert scalar_question.actual_outcome == "supported"
    assert scalar_question.passed is True
