import io

import pytest
from sqlalchemy import select

from app.db.models import (
    CanonicalBacklogAssessment,
    CanonicalBacklogAssessmentEvent,
    ExtractionRun,
    Setting,
    SettingCanonicalMapping,
)
from app.domain import (
    canonical_backlog_assessments,
    canonical_settings,
    manuals,
)
from app.domain.errors import CanonicalSettingError


def _setting(
    db_session,
    pdf_bytes_factory,
    *,
    vendor,
    name,
    description,
    marker,
):
    manual = manuals.create_manual(
        db_session,
        io.BytesIO(pdf_bytes_factory(texts=[f"{marker} {description} " * 30])),
        original_filename=f"{marker}.pdf",
        vendor=vendor,
        model_name="AMD 800 Series",
        document_title=f"{vendor} AMD 800 Series BIOS Guide",
        source_url=f"https://www.{vendor.casefold()}.com/support/{marker}",
        chipset="AMD 800 Series",
    )
    manual.acceptance_reviewed_at = "2026-08-14T00:00:00+00:00"
    manual.acceptance_reviewer = "test-reviewer"
    run = db_session.scalar(select(ExtractionRun).where(ExtractionRun.manual_id == manual.id))
    setting = Setting(
        manual_id=manual.id,
        extraction_run_id=run.id,
        extracted_name=name,
        extracted_description=description,
        page_number=1,
        page_review_status="correct",
        status="approved",
        created_at="2026-08-14T00:00:00+00:00",
        updated_at="2026-08-14T00:00:00+00:00",
    )
    db_session.add(setting)
    db_session.commit()
    return setting


def _candidate_pair(db_session, pdf_bytes_factory):
    description = "Controls the scalar used by Precision Boost Overdrive."
    identity = canonical_settings.create_identity(
        db_session, name="Precision Boost Overdrive Scalar"
    )
    canonical_settings.add_alias(
        db_session, identity, alias="PBO Scalar", vendor_id="asus"
    )
    support = _setting(
        db_session,
        pdf_bytes_factory,
        vendor="MSI",
        name="PBO Scalar",
        description=description,
        marker="assessment-support",
    )
    canonical_settings.map_setting(
        db_session,
        support,
        canonical_id=identity.canonical_id,
        note="Official mapped precedent.",
    )
    candidate = _setting(
        db_session,
        pdf_bytes_factory,
        vendor="ASUS",
        name="PBO Scalar",
        description=description,
        marker="assessment-candidate",
    )
    return identity, candidate


def test_refresh_classifies_every_unmapped_record_without_mapping_it(
    db_session, pdf_bytes_factory
):
    identity, candidate = _candidate_pair(db_session, pdf_bytes_factory)
    mystery = _setting(
        db_session,
        pdf_bytes_factory,
        vendor="ASRock",
        name="Vendor Mystery Switch",
        description="Controls a vendor-specific undocumented switch.",
        marker="assessment-mystery",
    )

    first = canonical_backlog_assessments.refresh_assessments(db_session)
    second = canonical_backlog_assessments.refresh_assessments(db_session)

    assert first.evaluated_count == 2
    assert first.created_count == 2
    assert second.created_count == 0
    assert second.unchanged_count == 2
    assessed_candidate = db_session.get(CanonicalBacklogAssessment, candidate.id)
    assert assessed_candidate.outcome == "strong_candidate"
    assert assessed_candidate.candidate_canonical_id == identity.canonical_id
    assert db_session.get(SettingCanonicalMapping, candidate.id) is None
    assert db_session.get(CanonicalBacklogAssessment, mystery.id).outcome in {
        "needs_research",
        "insufficient_evidence",
    }
    assert canonical_backlog_assessments.metrics(db_session).missing_assessment_count == 0


def test_human_conclusion_is_preserved_and_append_audited(
    db_session, pdf_bytes_factory
):
    identity, candidate = _candidate_pair(db_session, pdf_bytes_factory)
    canonical_backlog_assessments.refresh_assessments(db_session)

    canonical_backlog_assessments.confirm_assessment(
        db_session,
        candidate.id,
        outcome="needs_research",
        candidate_canonical_id=identity.canonical_id,
        note="A reviewed BIOS menu path is still required.",
    )
    rerun = canonical_backlog_assessments.refresh_assessments(db_session)

    row = db_session.get(CanonicalBacklogAssessment, candidate.id)
    assert row.outcome == "needs_research"
    assert row.review_status == "human_confirmed"
    assert rerun.human_confirmed_preserved_count == 1
    events = list(
        db_session.scalars(
            select(CanonicalBacklogAssessmentEvent)
            .where(CanonicalBacklogAssessmentEvent.setting_id == candidate.id)
            .order_by(CanonicalBacklogAssessmentEvent.id)
        )
    )
    assert [event.action for event in events] == ["system_assessed", "human_confirmed"]


def test_verified_mapping_outcome_requires_real_mapping_record(
    db_session, pdf_bytes_factory
):
    identity, candidate = _candidate_pair(db_session, pdf_bytes_factory)
    canonical_backlog_assessments.refresh_assessments(db_session)

    with pytest.raises(CanonicalSettingError, match="existing audited mapping"):
        canonical_backlog_assessments.confirm_assessment(
            db_session,
            candidate.id,
            outcome="verified_exact_mapping",
            candidate_canonical_id=identity.canonical_id,
            note="Attempted without a mapping record.",
        )


def test_assessment_queue_and_detail_render(client, db_session, pdf_bytes_factory):
    _, candidate = _candidate_pair(db_session, pdf_bytes_factory)
    canonical_backlog_assessments.refresh_assessments(db_session)

    queue = client.get("/canonicalization/assessments")
    detail = client.get(f"/canonicalization/assessments/{candidate.id}")

    assert queue.status_code == 200
    assert "Classification organizes evidence" in queue.text
    assert detail.status_code == 200
    assert "Why this classification was assigned" in detail.text
    assert "does not create or alter a canonical mapping" in detail.text
