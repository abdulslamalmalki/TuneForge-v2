"""Human-review boundary from official research leads to draft claims."""

import hashlib

import pytest
from sqlalchemy import func, select

from app.db.models import (
    EvidenceExtractionRun,
    EvidencePassage,
    EvidenceSource,
    GuidanceClaim,
    ResearchEvidenceIntakeEvent,
    TrustedWebResearchResult,
    TrustedWebResearchRun,
)
from app.domain import canonical_settings, guidance_read, research_evidence_intake
from app.research.trusted_web import TrustedWebResearchError

NOW = "2026-08-14T00:00:00+00:00"


def _research_result(db_session, *, publisher_id="amd"):
    run = TrustedWebResearchRun(
        query_text="What does PBO do in BIOS?",
        normalized_query="what does pbo do in bios",
        status="succeeded",
        policy_version="official-web-1.0",
        result_count=1,
        reason=None,
        created_at=NOW,
        finished_at=NOW,
    )
    db_session.add(run)
    db_session.flush()
    result = TrustedWebResearchResult(
        run_id=run.id,
        ordinal=1,
        publisher_id=publisher_id,
        publisher_name="Advanced Micro Devices, Inc.",
        title="Precision Boost Overdrive",
        source_url="https://www.amd.com/en/resources/pbo.html",
        evidence_kind="official_page_excerpt",
        excerpt_text="Precision Boost Overdrive extends automatic boost behavior.",
        content_sha256=hashlib.sha256(b"official page").hexdigest(),
        retrieved_at=NOW,
    )
    db_session.add(result)
    db_session.commit()
    return result


def _approved_evidence(db_session, *, publisher_id="amd", passages=1):
    source = EvidenceSource(
        publisher_id=publisher_id,
        publisher_name="Advanced Micro Devices, Inc.",
        title="AMD Official Tuning Guide",
        source_type="official_document",
        trust_tier="primary_official",
        source_url="https://docs.amd.com/r/en-US/official-guide",
        document_id="AMD-TEST",
        document_version="1.0",
        published_on="2026-01-01",
        retrieved_at=NOW,
        language="en",
        original_filename="amd-guide.pdf",
        stored_filename=f"{publisher_id}-guide.pdf",
        sha256=hashlib.sha256(f"{publisher_id}-guide".encode()).hexdigest(),
        file_size_bytes=1024,
        page_count=passages,
        status="approved",
        reviewer="test-reviewer",
        reviewed_at=NOW,
        review_note="Official provenance verified.",
        created_at=NOW,
    )
    db_session.add(source)
    db_session.flush()
    extraction = EvidenceExtractionRun(
        source_id=source.id,
        status="succeeded",
        extractor_version="test",
        pages_processed=passages,
        passages_found=passages,
        error_message=None,
        started_at=NOW,
        finished_at=NOW,
        created_at=NOW,
    )
    db_session.add(extraction)
    db_session.flush()
    rows = []
    for ordinal in range(1, passages + 1):
        text = f"Official reviewed PBO statement {ordinal}."
        row = EvidencePassage(
            source_id=source.id,
            extraction_run_id=extraction.id,
            ordinal=ordinal,
            page_number=ordinal,
            section_heading="Precision Boost Overdrive",
            passage_text=text,
            text_sha256=hashlib.sha256(text.encode()).hexdigest(),
            created_at=NOW,
        )
        db_session.add(row)
        rows.append(row)
    db_session.commit()
    return source, rows


def _triage(db_session, result, canonical_id):
    return research_evidence_intake.triage_result(
        db_session,
        result.id,
        status="evidence_intake",
        note="Official result is relevant; complete source review is still required.",
        canonical_id=canonical_id,
        proposed_claim_text="Precision Boost Overdrive extends automatic boost behavior.",
        proposed_claim_type="behavior",
    )


def test_triage_is_audited_but_creates_no_evidence_or_claim(db_session):
    result = _research_result(db_session)
    identity = canonical_settings.create_identity(db_session, name="Precision Boost Overdrive")

    intake = _triage(db_session, result, identity.canonical_id)

    assert intake.status == "evidence_intake"
    assert db_session.scalar(select(func.count(EvidenceSource.id))) == 0
    assert db_session.scalar(select(func.count(GuidanceClaim.id))) == 0
    events = list(
        db_session.scalars(
            select(ResearchEvidenceIntakeEvent).where(
                ResearchEvidenceIntakeEvent.result_id == result.id
            )
        )
    )
    assert [event.action for event in events] == ["triaged"]


def test_claim_cannot_be_drafted_before_approved_evidence_is_linked(db_session):
    result = _research_result(db_session)
    identity = canonical_settings.create_identity(db_session, name="Precision Boost Overdrive")
    _triage(db_session, result, identity.canonical_id)

    with pytest.raises(TrustedWebResearchError, match="Link an approved Evidence Source"):
        research_evidence_intake.create_draft_claim(
            db_session, result.id, note="Attempted before evidence review."
        )


def test_evidence_link_rejects_a_different_publisher(db_session):
    result = _research_result(db_session, publisher_id="amd")
    identity = canonical_settings.create_identity(db_session, name="Precision Boost Overdrive")
    _triage(db_session, result, identity.canonical_id)
    source, passages = _approved_evidence(db_session, publisher_id="intel")

    with pytest.raises(TrustedWebResearchError, match="publisher must match"):
        research_evidence_intake.link_reviewed_evidence(
            db_session,
            result.id,
            evidence_source_id=source.id,
            evidence_passage_id=passages[0].id,
            note="This should fail closed.",
        )


def test_linked_reviewed_passage_creates_only_a_draft_claim(db_session):
    result = _research_result(db_session)
    identity = canonical_settings.create_identity(db_session, name="Precision Boost Overdrive")
    _triage(db_session, result, identity.canonical_id)
    source, passages = _approved_evidence(db_session)

    research_evidence_intake.link_reviewed_evidence(
        db_session,
        result.id,
        evidence_source_id=source.id,
        evidence_passage_id=passages[0].id,
        note="The approved passage directly supports the proposed atomic statement.",
    )
    claim = research_evidence_intake.create_draft_claim(
        db_session,
        result.id,
        note="Create a candidate for separate claim review.",
    )

    assert claim.status == "draft"
    assert guidance_read.explain_canonical_id(db_session, identity.canonical_id).facts == ()
    actions = list(
        db_session.scalars(
            select(ResearchEvidenceIntakeEvent.action)
            .where(ResearchEvidenceIntakeEvent.result_id == result.id)
            .order_by(ResearchEvidenceIntakeEvent.id)
        )
    )
    assert actions == ["triaged", "reviewed_evidence_linked", "draft_claim_created"]


def test_rejected_result_cannot_skip_back_into_reviewed_evidence(db_session):
    result = _research_result(db_session)
    identity = canonical_settings.create_identity(db_session, name="Precision Boost Overdrive")
    research_evidence_intake.triage_result(
        db_session,
        result.id,
        status="rejected",
        note="The excerpt does not support the requested setting behavior.",
        canonical_id=identity.canonical_id,
    )
    source, passages = _approved_evidence(db_session)

    with pytest.raises(TrustedWebResearchError, match="Only an evidence-intake"):
        research_evidence_intake.link_reviewed_evidence(
            db_session,
            result.id,
            evidence_source_id=source.id,
            evidence_passage_id=passages[0].id,
            note="A rejected lead must be deliberately retriaged first.",
        )


def test_protected_reviewer_pages_label_results_as_unreviewed(client, db_session):
    result = _research_result(db_session)

    queue = client.get("/trusted-web-research")
    detail = client.get(f"/trusted-web-research/results/{result.id}")

    assert queue.status_code == 200
    assert "research leads—not approved Evidence Sources" in queue.text
    assert detail.status_code == 200
    assert "Official research lead · unreviewed" in detail.text
    assert "This snapshot is not approved evidence" in detail.text
    assert result.content_sha256 in detail.text
    dashboard = client.get("/admin")
    assert "Official research leads" in dashboard.text
    assert "/trusted-web-research?status=new" in dashboard.text
