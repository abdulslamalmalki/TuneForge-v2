import io

from sqlalchemy import select

from app.db.models import (
    ExtractionRun,
    GuidanceCandidate,
    GuidanceCandidateEvent,
    Setting,
)
from app.domain import (
    canonical_settings,
    guidance_candidates,
    guidance_claims,
    guidance_read,
    manuals,
)


def _approved_setting(
    db_session,
    pdf_bytes_factory,
    *,
    name="PBO Scalar",
    description="Controls the scalar used by Precision Boost Overdrive.",
    mapped=True,
):
    manual = manuals.create_manual(
        db_session,
        io.BytesIO(pdf_bytes_factory(texts=[f"{description} " * 30])),
        original_filename=f"{name.casefold().replace(' ', '-')}.pdf",
        vendor="ASUS",
        model_name="AMD 800 Series",
        document_title="ASUS AMD 800 Series BIOS Guide",
        source_url="https://www.asus.com/supportonly/test/helpdesk_manual/",
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
    identity = None
    if mapped:
        identity = canonical_settings.create_identity(
            db_session,
            name="Precision Boost Overdrive Scalar",
        )
        canonical_settings.map_setting(
            db_session,
            setting,
            canonical_id=identity.canonical_id,
            note="Official source mapping reviewed.",
        )
    return setting, identity


def test_generation_is_exact_text_audited_and_idempotent(
    db_session, pdf_bytes_factory
):
    setting, identity = _approved_setting(db_session, pdf_bytes_factory)

    first = guidance_candidates.generate_candidates(db_session)
    second = guidance_candidates.generate_candidates(db_session)

    assert first.processed_settings == 1
    assert first.created_candidates == 1
    assert first.pending_candidates == 1
    assert second.created_candidates == 0
    candidate = db_session.scalar(select(GuidanceCandidate))
    assert candidate.setting_id == setting.id
    assert candidate.canonical_id == identity.canonical_id
    assert candidate.claim_text == setting.extracted_description
    assert candidate.source_excerpt == setting.extracted_description
    assert candidate.status == "pending"
    assert candidate.source_trust == "trusted_official_manual"
    assert candidate.promoted_claim_id is None
    events = list(
        db_session.scalars(
            select(GuidanceCandidateEvent).where(
                GuidanceCandidateEvent.candidate_id == candidate.id
            )
        )
    )
    assert [event.action for event in events] == ["generated"]


def test_unmapped_setting_is_routed_to_research_not_guessed(
    db_session, pdf_bytes_factory
):
    setting, _ = _approved_setting(
        db_session,
        pdf_bytes_factory,
        name="Vendor Specific Mystery Control",
        mapped=False,
    )

    report = guidance_candidates.generate_candidates(db_session)

    candidate = db_session.scalar(
        select(GuidanceCandidate).where(GuidanceCandidate.setting_id == setting.id)
    )
    assert report.needs_research_candidates == 1
    assert candidate.status == "needs_research"
    assert candidate.canonical_id is None
    assert candidate.confidence_basis_points < 10000


def test_promotion_creates_draft_and_publication_still_requires_human_approval(
    db_session, pdf_bytes_factory
):
    setting, identity = _approved_setting(db_session, pdf_bytes_factory)
    guidance_candidates.generate_candidates(db_session)
    candidate = db_session.scalar(select(GuidanceCandidate))

    claim = guidance_candidates.promote_candidate(
        db_session,
        candidate.id,
        note="Exact official-manual statement and mapping reviewed.",
    )

    assert claim.status == "draft"
    assert guidance_read.explain_canonical_id(db_session, identity.canonical_id).facts == ()
    detail = guidance_claims.get_detail(db_session, claim.id)
    assert [citation.setting.id for citation in detail.source_settings] == [setting.id]
    assert candidate.promoted_claim_id == claim.id

    guidance_claims.review_claim(
        db_session,
        claim,
        decision="approved",
        note="Human reviewer confirmed the exact source statement.",
    )
    fact = guidance_read.explain_canonical_id(db_session, identity.canonical_id).facts[0]
    assert fact.claim_text == setting.extracted_description
    assert fact.citations[0].source_kind == "approved_source_setting"
    assert fact.citations[0].setting_id == setting.id
    assert fact.citations[0].passage_id is None


def test_candidate_review_state_requires_note_and_is_append_audited(
    db_session, pdf_bytes_factory
):
    _approved_setting(db_session, pdf_bytes_factory)
    guidance_candidates.generate_candidates(db_session)
    candidate = db_session.scalar(select(GuidanceCandidate))

    guidance_candidates.set_review_state(
        db_session,
        candidate.id,
        status="needs_research",
        note="The behavior statement needs a second official source.",
    )

    assert candidate.status == "needs_research"
    events = list(
        db_session.scalars(
            select(GuidanceCandidateEvent)
            .where(GuidanceCandidateEvent.candidate_id == candidate.id)
            .order_by(GuidanceCandidateEvent.id)
        )
    )
    assert [event.action for event in events] == ["generated", "needs_research"]


def test_candidate_admin_queue_and_detail_are_protected_review_surfaces(
    client, db_session, pdf_bytes_factory
):
    _approved_setting(db_session, pdf_bytes_factory)
    guidance_candidates.generate_candidates(db_session)
    candidate = db_session.scalar(select(GuidanceCandidate))

    queue = client.get("/guidance-candidates")
    detail = client.get(f"/guidance-candidates/{candidate.id}")

    assert queue.status_code == 200
    assert "Generated candidates preserve official-manual text" in queue.text
    assert "Promote to draft claim" in detail.text
    assert "Original reviewed excerpt" in detail.text
