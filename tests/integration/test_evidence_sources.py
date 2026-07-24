from fastapi.testclient import TestClient

from app import config
from app.db.models import (
    EvidenceExtractionRun,
    EvidencePassage,
    EvidenceSource,
    EvidenceSourceEvent,
)
from app.db.session import SessionLocal
from app.main import create_app

SOURCE_DATA = {
    "publisher_id": "amd",
    "title": "Ryzen Master User Guide",
    "source_url": "https://docs.amd.com/r/en-US/68886-ryzen-master-user-guide",
    "document_id": "68886",
    "document_version": "3.1.0",
    "published_on": "2026-05-20",
    "language": "en",
}


def _source_pdf(pdf_bytes_factory):
    prose = "Official source evidence sentence for deterministic extraction. " * 8
    return pdf_bytes_factory(
        texts=[
            f"Ryzen Master User Guide\nPublication 68886\n{prose}",
            f"5.2.3 Curve Optimizer\n{prose}",
        ]
    )


def test_pending_source_requires_review_before_passage_extraction(client, pdf_bytes_factory):
    created = client.post(
        "/api/v1/evidence-sources",
        files={"file": ("68886.pdf", _source_pdf(pdf_bytes_factory), "application/pdf")},
        data=SOURCE_DATA,
    )
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "pending"
    assert body["publisher_name"] == "Advanced Micro Devices, Inc."
    assert body["sha256"]
    assert body["extraction_run"]["status"] == "pending"
    assert body["passages"] == []
    source_id = body["id"]

    no_note = client.post(
        f"/api/v1/evidence-sources/{source_id}/review",
        json={"decision": "approved", "note": ""},
    )
    assert no_note.status_code == 400

    approved = client.post(
        f"/api/v1/evidence-sources/{source_id}/review",
        json={
            "decision": "approved",
            "note": "Official AMD publication ID, version, date, and artifact verified.",
        },
    )
    assert approved.status_code == 200
    reviewed = approved.json()
    assert reviewed["status"] == "approved"
    assert reviewed["extraction_run"]["status"] == "succeeded"
    assert reviewed["extraction_run"]["pages_processed"] == 2
    assert len(reviewed["passages"]) >= 2
    assert reviewed["passages"][0]["page_number"] == 1
    assert len(reviewed["passages"][0]["text_sha256"]) == 64
    assert [event["action"] for event in reviewed["events"]] == [
        "source_created",
        "source_approved",
    ]

    artifact = client.get(f"/api/v1/evidence-sources/{source_id}/artifact")
    assert artifact.status_code == 200
    assert artifact.content.startswith(b"%PDF")
    (summary,) = client.get("/api/v1/evidence-sources").json()
    assert summary["passage_count"] == len(reviewed["passages"])


def test_source_intake_enforces_official_domain_and_content_deduplication(
    client, pdf_bytes_factory
):
    pdf = _source_pdf(pdf_bytes_factory)
    bad = client.post(
        "/api/v1/evidence-sources",
        files={"file": ("source.pdf", pdf, "application/pdf")},
        data={**SOURCE_DATA, "source_url": "https://example.com/68886.pdf"},
    )
    assert bad.status_code == 400
    assert "official HTTPS domain" in bad.json()["detail"]
    assert list(config.EVIDENCE_SOURCES_DIR.glob("*")) == []

    first = client.post(
        "/api/v1/evidence-sources",
        files={"file": ("source.pdf", pdf, "application/pdf")},
        data=SOURCE_DATA,
    )
    assert first.status_code == 201
    duplicate = client.post(
        "/api/v1/evidence-sources",
        files={"file": ("renamed.pdf", pdf, "application/pdf")},
        data=SOURCE_DATA,
    )
    assert duplicate.status_code == 400
    assert "already stored" in duplicate.json()["detail"]


def test_rejection_is_audited_and_does_not_create_passages(client, pdf_bytes_factory):
    created = client.post(
        "/api/v1/evidence-sources",
        files={"file": ("wrong-version.pdf", _source_pdf(pdf_bytes_factory), "application/pdf")},
        data=SOURCE_DATA,
    ).json()
    source_id = created["id"]
    rejected = client.post(
        f"/api/v1/evidence-sources/{source_id}/review",
        json={"decision": "rejected", "note": "The PDF does not match the stated version."},
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["passages"] == []
    assert rejected.json()["events"][-1]["action"] == "source_rejected"

    second_review = client.post(
        f"/api/v1/evidence-sources/{source_id}/review",
        json={"decision": "approved", "note": "Changed mind."},
    )
    assert second_review.status_code == 400


def test_interrupted_extraction_is_recovered_and_can_be_retried(client, pdf_bytes_factory):
    created = client.post(
        "/api/v1/evidence-sources",
        files={"file": ("68886.pdf", _source_pdf(pdf_bytes_factory), "application/pdf")},
        data=SOURCE_DATA,
    ).json()
    source_id = created["id"]
    with SessionLocal() as session:
        run = session.query(EvidenceExtractionRun).filter_by(source_id=source_id).one()
        run.status = "running"
        run.started_at = "2026-07-18T00:00:00+00:00"
        session.commit()

    with TestClient(create_app()):
        pass
    # Recovery is idempotent and must not append a second interruption event.
    with TestClient(create_app()):
        pass

    with SessionLocal() as session:
        source = session.get(EvidenceSource, source_id)
        run = session.query(EvidenceExtractionRun).filter_by(source_id=source_id).one()
        events = session.query(EvidenceSourceEvent).filter_by(source_id=source_id).all()
        assert source.status == "pending"
        assert run.status == "failed"
        assert "interrupted" in run.error_message
        assert [event.action for event in events] == [
            "source_created",
            "extraction_interrupted",
        ]

    retried = client.post(
        f"/api/v1/evidence-sources/{source_id}/review",
        json={"decision": "approved", "note": "Provenance rechecked after interruption."},
    )
    assert retried.status_code == 200
    body = retried.json()
    assert body["status"] == "approved"
    assert body["extraction_run"]["status"] == "succeeded"
    assert [event["action"] for event in body["events"]] == [
        "source_created",
        "extraction_interrupted",
        "source_approved",
    ]


def test_html_evidence_source_workflow(client, pdf_bytes_factory):
    landing = client.get("/evidence-sources")
    assert landing.status_code == 200
    assert "Trusted Evidence Sources" in landing.text

    created = client.post(
        "/evidence-sources",
        files={"file": ("68886.pdf", _source_pdf(pdf_bytes_factory), "application/pdf")},
        data=SOURCE_DATA,
        follow_redirects=False,
    )
    assert created.status_code == 303
    detail_url = created.headers["location"]
    page = client.get(detail_url)
    assert "Immutable source and provenance" in page.text
    assert "Approve and extract evidence" in page.text

    approved = client.post(
        f"{detail_url}/review",
        data={
            "decision": "approved",
            "note": "Official document and provenance reviewed.",
        },
        follow_redirects=False,
    )
    assert approved.status_code == 303
    page = client.get(detail_url)
    assert "Immutable evidence passages" in page.text
    assert "source_approved" in page.text

    with SessionLocal() as session:
        assert session.query(EvidenceSource).one().status == "approved"
        assert session.query(EvidencePassage).count() >= 2
        assert session.query(EvidenceSourceEvent).count() == 2
