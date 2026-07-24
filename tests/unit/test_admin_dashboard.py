"""Read-only admin operational projection."""

import json

from app.db.models import (
    CanonicalMappingSuggestion,
    CanonicalSetting,
    EvidenceExtractionRun,
    EvidenceSource,
    ExtractionRun,
    GuidanceClaim,
    Manual,
    ManualEvent,
    Setting,
    UnansweredLookup,
)
from app.domain.admin_dashboard import build_dashboard

NOW = "2026-07-19T08:00:00+00:00"


def _seed_attention_state(session):
    manual = Manual(
        original_filename="pending.pdf",
        stored_filename="manual_attention.pdf",
        sha256="a" * 64,
        file_size_bytes=100,
        page_count=1,
        vendor=None,
        vendor_id=None,
        model_name=None,
        document_title="",
        source_url=None,
        document_version=None,
        published_on=None,
        retrieved_at=NOW,
        language="en",
        board_revision=None,
        chipset=None,
        document_type=None,
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
    session.add(manual)
    session.flush()
    run = ExtractionRun(
        manual_id=manual.id,
        status="failed",
        error_message="Safe test failure.",
        pages_processed=0,
        settings_found=0,
        parser_version=None,
        started_at=NOW,
        finished_at=NOW,
        created_at=NOW,
    )
    session.add(run)
    session.flush()
    setting = Setting(
        manual_id=manual.id,
        extraction_run_id=run.id,
        extracted_name="Test Setting",
        extracted_description="Test description.",
        edited_name=None,
        edited_description=None,
        page_number=1,
        edited_page_number=None,
        page_review_status="pending",
        status="draft",
        review_note=None,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(setting)
    canonical = CanonicalSetting(
        canonical_id="test-setting",
        canonical_name="Test Setting",
        description="Test identity.",
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(canonical)
    session.flush()
    session.add(
        CanonicalMappingSuggestion(
            setting_id=setting.id,
            canonical_id=canonical.canonical_id,
            match_type="exact_alias",
            matched_alias="Test Setting",
            status="pending",
            review_note=None,
            actor=None,
            created_at=NOW,
            reviewed_at=None,
        )
    )
    source = EvidenceSource(
        publisher_id="amd",
        publisher_name="Advanced Micro Devices, Inc.",
        title="Pending Evidence",
        source_type="official_document",
        trust_tier="primary_official",
        source_url="https://www.amd.com/en/resources/support-articles.html",
        document_id="TEST",
        document_version="1",
        published_on="2026-01-01",
        retrieved_at=NOW,
        language="en",
        original_filename="evidence.pdf",
        stored_filename="evidence_attention.pdf",
        sha256="b" * 64,
        file_size_bytes=100,
        page_count=1,
        status="pending",
        reviewer=None,
        reviewed_at=None,
        review_note=None,
        created_at=NOW,
    )
    session.add(source)
    session.flush()
    session.add(
        EvidenceExtractionRun(
            source_id=source.id,
            status="failed",
            extractor_version=None,
            pages_processed=0,
            passages_found=0,
            error_message="Safe evidence failure.",
            started_at=NOW,
            finished_at=NOW,
            created_at=NOW,
        )
    )
    session.add(
        GuidanceClaim(
            claim_text="Test draft claim.",
            claim_type="definition",
            applicability_scope="source_scope",
            cpu_model_id=None,
            status="draft",
            review_note=None,
            reviewer=None,
            reviewed_at=None,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    session.add(
        UnansweredLookup(
            surface="guidance",
            reason="no_exact_guidance_match",
            query_text="unknown test setting",
            normalized_query="unknown test setting",
            nearest_matches_json=json.dumps([]),
            context_json=json.dumps({}),
            created_at=NOW,
        )
    )
    session.add(
        ManualEvent(
            manual_id=manual.id,
            action="provenance_created",
            actor="local-reviewer",
            detail=None,
            created_at=NOW,
        )
    )
    session.commit()


def test_dashboard_projects_priority_queues_and_health_without_writes(db_session):
    _seed_attention_state(db_session)
    dashboard = build_dashboard(db_session)
    metrics = {row.label: row for row in dashboard.metrics}
    tasks = {row.task_id: row for row in dashboard.tasks}

    assert metrics["Official manuals"].value == 1
    assert metrics["Approved settings"].value == 0
    assert metrics["Canonical settings"].value == 1
    assert metrics["Trusted sources"].value == 0
    assert metrics["Accepted claims"].value == 0
    assert metrics["Refusal demand"].value == 1

    assert tasks["manual-provenance"].count == 1
    assert tasks["manual-eligibility"].count == 1
    assert tasks["extraction-failures"].count == 2
    assert tasks["extraction-failures"].severity == "critical"
    assert tasks["setting-review"].count == 1
    assert tasks["evidence-review"].count == 1
    assert tasks["claim-review"].count == 1
    assert tasks["mapping-suggestions"].count == 1
    assert tasks["refusal-gaps"].count == 1
    assert dashboard.active_task_count == 8
    assert dashboard.health.status == "critical"
    assert dashboard.activities[0].subject.startswith("Manual ")

    assert db_session.query(Manual).count() == 1
    assert db_session.query(UnansweredLookup).count() == 1
