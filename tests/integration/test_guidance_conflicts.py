from app.db.models import CanonicalSetting
from app.db.session import SessionLocal

SOURCE_DATA = {
    "publisher_id": "amd",
    "title": "Official Conflict Test Guide",
    "source_url": "https://docs.amd.com/r/en-US/conflict-test-guide",
    "document_id": "conflict-test",
    "document_version": "1.0",
    "published_on": "2026-05-20",
    "language": "en",
}


def _evidence_and_identity(client, pdf_bytes_factory):
    prose = "The setting behavior is explicitly documented for conflict testing. " * 8
    source = client.post(
        "/api/v1/evidence-sources",
        files={
            "file": (
                "conflict-guide.pdf",
                pdf_bytes_factory(texts=[prose]),
                "application/pdf",
            )
        },
        data=SOURCE_DATA,
    ).json()
    approved = client.post(
        f"/api/v1/evidence-sources/{source['id']}/review",
        json={"decision": "approved", "note": "Official source verified."},
    ).json()
    with SessionLocal() as session:
        session.add(
            CanonicalSetting(
                canonical_id="conflict-test-setting",
                canonical_name="Conflict Test Setting",
                description="Conflict workflow test identity.",
                created_at="2026-08-14T00:00:00+00:00",
                updated_at="2026-08-14T00:00:00+00:00",
            )
        )
        session.commit()
    return approved["passages"][0]["id"]


def _approved_claim(client, passage_id, text, *, bios_scopes=None):
    created = client.post(
        "/api/v1/guidance-claims",
        json={
            "claim_text": text,
            "claim_type": "behavior",
            "passage_ids": [passage_id],
            "canonical_ids": ["conflict-test-setting"],
            "bios_scopes": bios_scopes or [],
        },
    ).json()
    reviewed = client.post(
        f"/api/v1/guidance-claims/{created['id']}/review",
        json={"decision": "approved", "note": "Claim and source verified."},
    )
    assert reviewed.status_code == 200
    return created["id"]


def test_conflict_scan_requires_human_confirmation_and_surfaces_reviewed_disagreement(
    client,
    pdf_bytes_factory,
):
    passage_id = _evidence_and_identity(client, pdf_bytes_factory)
    left_id = _approved_claim(
        client,
        passage_id,
        "The controller can apply the documented boost limit in enabled mode.",
    )
    right_id = _approved_claim(
        client,
        passage_id,
        "The controller cannot apply the documented boost limit in enabled mode.",
    )

    scan = client.post("/api/v1/guidance-conflicts/scan")
    assert scan.status_code == 200
    assert scan.json()["detected_pairs"] == 1

    pending = client.get("/api/v1/guidance-conflicts?status=pending").json()
    assert len(pending) == 1
    conflict = pending[0]
    assert {conflict["left_claim_id"], conflict["right_claim_id"]} == {
        left_id,
        right_id,
    }
    assert conflict["hardware_relationship"] == "unknown"
    assert conflict["bios_relationship"] == "unknown"
    assert conflict["status"] == "pending"
    assert conflict["left_evidence_strength"] == "primary_official"

    premature = client.post(
        f"/api/v1/guidance-conflicts/{conflict['id']}/review",
        json={
            "decision": "resolved_scope",
            "note": "No reviewed disjoint scope exists.",
        },
    )
    assert premature.status_code == 400
    assert "disjoint hardware targets" in premature.json()["detail"]

    reviewed = client.post(
        f"/api/v1/guidance-conflicts/{conflict['id']}/review",
        json={
            "decision": "confirmed",
            "note": "Reviewer confirmed a true disagreement in overlapping official evidence.",
        },
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["status"] == "confirmed"
    assert [event["action"] for event in reviewed.json()["events"]] == [
        "conflict_detected",
        "conflict_confirmed",
    ]

    public = client.get(
        "/api/v1/guidance/explain",
        params={"setting": "conflict-test-setting"},
    )
    assert public.status_code == 200
    assert public.json()["knowledge_conflicts"][0]["status"] == "confirmed"
    assert "true disagreement" in public.json()["knowledge_conflicts"][0]["reviewer_note"]

    page = client.get("/guidance", params={"setting": "conflict-test-setting"})
    assert "Reviewed knowledge differences" in page.text
    assert "instead of silently choosing one claim" in page.text


def test_version_difference_can_be_resolved_without_discarding_either_claim(
    client,
    pdf_bytes_factory,
):
    passage_id = _evidence_and_identity(client, pdf_bytes_factory)

    def scope(version):
        return [
            {
                "context_type": "motherboard_model",
                "context_id": "asrock-x870e-taichi-white",
                "bios_version_exact": version,
                "comparison_mode": "opaque",
                "version_notes": f"Behavior documented for BIOS {version}.",
                "evidence_basis": "Reviewer verified the official release context.",
            }
        ]

    left_id = _approved_claim(
        client,
        passage_id,
        "The firmware can expose the documented control in this release.",
        bios_scopes=scope("3.20"),
    )
    right_id = _approved_claim(
        client,
        passage_id,
        "The firmware cannot expose the documented control in this release.",
        bios_scopes=scope("3.30"),
    )
    created = client.post(
        "/api/v1/guidance-conflicts",
        json={
            "left_claim_id": left_id,
            "right_claim_id": right_id,
            "detection_reason": "Official behavior differs between two exact releases.",
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["bios_relationship"] == "version_difference"

    resolved = client.post(
        f"/api/v1/guidance-conflicts/{body['id']}/review",
        json={
            "decision": "resolved_version",
            "note": "Both claims remain valid in their distinct exact BIOS releases.",
        },
    )
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "resolved_version"

    public = client.get(
        "/api/v1/guidance/explain",
        params={"setting": "conflict-test-setting"},
    ).json()
    assert len(public["facts"]) == 2
    assert public["knowledge_conflicts"][0]["status"] == "resolved_version"
