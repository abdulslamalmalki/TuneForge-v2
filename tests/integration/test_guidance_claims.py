from app.db.models import CanonicalSetting
from app.db.session import SessionLocal

SOURCE_DATA = {
    "publisher_id": "amd",
    "title": "Ryzen Master User Guide",
    "source_url": "https://docs.amd.com/r/en-US/68886-ryzen-master-user-guide",
    "document_id": "68886",
    "document_version": "3.1.0",
    "published_on": "2026-05-20",
    "language": "en",
}


def _approved_evidence_and_setting(client, pdf_bytes_factory):
    prose = "Precision Boost Overdrive can operate up to board power and current limits. " * 7
    created = client.post(
        "/api/v1/evidence-sources",
        files={
            "file": (
                "amd-guidance.pdf",
                pdf_bytes_factory(texts=[f"6.3.2 CPU\n{prose}"]),
                "application/pdf",
            )
        },
        data=SOURCE_DATA,
    ).json()
    source_id = created["id"]
    approved = client.post(
        f"/api/v1/evidence-sources/{source_id}/review",
        json={"decision": "approved", "note": "Official test source verified."},
    ).json()
    passage_id = approved["passages"][0]["id"]
    with SessionLocal() as session:
        session.add(
            CanonicalSetting(
                canonical_id="precision-boost-overdrive",
                canonical_name="Precision Boost Overdrive",
                description="AMD automatic boost control.",
                created_at="2026-07-17T00:00:00+00:00",
                updated_at="2026-07-17T00:00:00+00:00",
            )
        )
        session.commit()
    return passage_id


def test_api_guidance_claim_acceptance_workflow(client, pdf_bytes_factory):
    passage_id = _approved_evidence_and_setting(client, pdf_bytes_factory)
    created = client.post(
        "/api/v1/guidance-claims",
        json={
            "claim_text": "PBO can operate up to motherboard power and current limits.",
            "claim_type": "behavior",
            "passage_ids": [passage_id],
            "canonical_ids": ["precision-boost-overdrive"],
        },
    )
    assert created.status_code == 201
    claim = created.json()
    claim_id = claim["id"]
    assert claim["status"] == "draft"
    assert claim["applicability_scope"] == "source_scope"
    assert claim["cpu_model_id"] is None
    assert claim["evidence"][0]["passage_id"] == passage_id
    assert claim["settings"][0]["canonical_id"] == "precision-boost-overdrive"
    assert claim["events"][0]["action"] == "candidate_created"

    edited = client.put(
        f"/api/v1/guidance-claims/{claim_id}",
        json={
            "claim_text": "PBO may operate up to motherboard power and current limits.",
            "claim_type": "behavior",
            "passage_ids": [passage_id],
            "canonical_ids": ["precision-boost-overdrive"],
        },
    )
    assert edited.status_code == 200
    assert edited.json()["events"][-1]["action"] == "candidate_edited"

    missing_note = client.post(
        f"/api/v1/guidance-claims/{claim_id}/review",
        json={"decision": "approved", "note": ""},
    )
    assert missing_note.status_code == 400
    approved = client.post(
        f"/api/v1/guidance-claims/{claim_id}/review",
        json={
            "decision": "approved",
            "note": "Claim meaning and applicability match the cited source passage.",
        },
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert approved.json()["events"][-1]["action"] == "claim_approved"

    immutable = client.put(
        f"/api/v1/guidance-claims/{claim_id}",
        json={
            "claim_text": "Changed after approval.",
            "claim_type": "behavior",
            "passage_ids": [passage_id],
            "canonical_ids": ["precision-boost-overdrive"],
        },
    )
    assert immutable.status_code == 400
    (summary,) = client.get("/api/v1/guidance-claims?status=approved").json()
    assert summary["id"] == claim_id
    assert summary["evidence_count"] == 1
    assert summary["setting_count"] == 1


def test_hardware_scope_requires_explicit_evidence_basis(client, pdf_bytes_factory):
    passage_id = _approved_evidence_and_setting(client, pdf_bytes_factory)
    scoped = client.post(
        "/api/v1/guidance-claims",
        json={
            "claim_text": "Unsupported model-specific assertion.",
            "claim_type": "behavior",
            "passage_ids": [passage_id],
            "canonical_ids": ["precision-boost-overdrive"],
            "applicability_scope": "cpu_model",
            "cpu_model_id": "amd-ryzen-7-9800x3d",
        },
    )
    assert scoped.status_code == 400
    assert "without an evidence basis" in scoped.json()["detail"]

    reviewed = client.post(
        "/api/v1/guidance-claims",
        json={
            "claim_text": "Official evidence explicitly scopes this fact to Ryzen 7 9800X3D.",
            "claim_type": "constraint",
            "passage_ids": [passage_id],
            "canonical_ids": ["precision-boost-overdrive"],
            "applicability_scope": "reviewed_hardware",
            "applicability_targets": [
                {
                    "target_type": "cpu_model",
                    "target_id": "amd-ryzen-7-9800x3d",
                    "evidence_basis": (
                        "Reviewer confirmed the cited official passage names this exact CPU."
                    ),
                }
            ],
        },
    )
    assert reviewed.status_code == 201
    body = reviewed.json()
    assert body["status"] == "draft"
    assert body["applicability_scope"] == "reviewed_hardware"
    assert body["applicability_targets"] == [
        {
            "target_type": "cpu_model",
            "target_id": "amd-ryzen-7-9800x3d",
            "canonical_name": "AMD Ryzen 7 9800X3D",
            "evidence_level": "exact_cpu",
            "evidence_basis": (
                "Reviewer confirmed the cited official passage names this exact CPU."
            ),
        }
    ]
    approved = client.post(
        f"/api/v1/guidance-claims/{body['id']}/review",
        json={
            "decision": "approved",
            "note": "Reviewer verified the exact CPU scope against the cited passage.",
        },
    )
    assert approved.status_code == 200
    public = client.get(
        "/api/v1/guidance/explain",
        params={
            "setting": "precision-boost-overdrive",
            "motherboard_model_id": "asrock-x870e-taichi-white",
            "cpu_model_id": "amd-ryzen-7-9800x3d",
        },
    )
    assert public.status_code == 200
    assert public.json()["facts"][0]["applicability_targets"][0]["evidence_level"] == ("exact_cpu")
    assert "1 reviewed hardware-scoped fact(s) match" in public.json()["context_notice"]

    family_scoped = client.post(
        "/api/v1/guidance-claims",
        json={
            "claim_text": "Compatible family and vendor scope test.",
            "claim_type": "constraint",
            "passage_ids": [passage_id],
            "canonical_ids": ["precision-boost-overdrive"],
            "applicability_scope": "reviewed_hardware",
            "applicability_targets": [
                {
                    "target_type": "cpu_vendor",
                    "target_id": "amd",
                    "evidence_basis": "The official evidence identifies AMD processors.",
                },
                {
                    "target_type": "motherboard_family",
                    "target_id": "asrock-taichi",
                    "evidence_basis": "The official evidence identifies the Taichi family.",
                },
                {
                    "target_type": "vendor",
                    "target_id": "asrock",
                    "evidence_basis": "The official evidence is published by ASRock.",
                },
                {
                    "target_type": "platform",
                    "target_id": "amd-am5",
                    "evidence_basis": "The official evidence identifies the AM5 platform.",
                },
            ],
        },
    )
    assert family_scoped.status_code == 201
    assert {target["target_type"] for target in family_scoped.json()["applicability_targets"]} == {
        "cpu_vendor",
        "motherboard_family",
        "vendor",
        "platform",
    }

    incompatible = client.post(
        "/api/v1/guidance-claims",
        json={
            "claim_text": "Conflicting platform target test.",
            "claim_type": "constraint",
            "passage_ids": [passage_id],
            "canonical_ids": ["precision-boost-overdrive"],
            "applicability_scope": "reviewed_hardware",
            "applicability_targets": [
                {
                    "target_type": "cpu_model",
                    "target_id": "amd-ryzen-7-9800x3d",
                    "evidence_basis": "Exact AMD CPU named by the evidence.",
                },
                {
                    "target_type": "platform",
                    "target_id": "intel-lga1700",
                    "evidence_basis": "Deliberately incompatible platform for validation.",
                },
            ],
        },
    )
    assert incompatible.status_code == 400
    assert "incompatible hardware platforms" in incompatible.json()["detail"]

    recommendation = client.post(
        "/api/v1/guidance-claims",
        json={
            "claim_text": "Use a particular PBO value.",
            "claim_type": "recommendation",
            "passage_ids": [passage_id],
            "canonical_ids": ["precision-boost-overdrive"],
        },
    )
    assert recommendation.status_code == 400
    assert "definition, behavior, constraint, or safety" in recommendation.json()["detail"]


def test_rejected_claim_is_audited_and_not_in_approved_catalog(client, pdf_bytes_factory):
    passage_id = _approved_evidence_and_setting(client, pdf_bytes_factory)
    created = client.post(
        "/api/v1/guidance-claims",
        json={
            "claim_text": "Draft interpretation requiring rejection.",
            "claim_type": "definition",
            "passage_ids": [passage_id],
            "canonical_ids": ["precision-boost-overdrive"],
        },
    ).json()
    rejected = client.post(
        f"/api/v1/guidance-claims/{created['id']}/review",
        json={"decision": "rejected", "note": "Claim overstates the cited passage."},
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["events"][-1]["action"] == "claim_rejected"
    assert client.get("/api/v1/guidance-claims?status=approved").json() == []


def test_html_guidance_review_queue_and_detail(client, pdf_bytes_factory):
    passage_id = _approved_evidence_and_setting(client, pdf_bytes_factory)
    claim = client.post(
        "/api/v1/guidance-claims",
        json={
            "claim_text": "PBO behavior is bounded by motherboard limits.",
            "claim_type": "constraint",
            "passage_ids": [passage_id],
            "canonical_ids": ["precision-boost-overdrive"],
        },
    ).json()
    queue = client.get("/guidance-claims")
    assert queue.status_code == 200
    assert "PBO behavior is bounded" in queue.text
    assert "does not generate recommendations" in queue.text

    detail_url = f"/guidance-claims/{claim['id']}"
    detail = client.get(detail_url)
    assert "Supporting evidence" in detail.text
    assert "source_scope" in detail.text
    approved = client.post(
        f"{detail_url}/review",
        data={"decision": "approved", "note": "Source-faithful and correctly linked."},
        follow_redirects=False,
    )
    assert approved.status_code == 303
    page = client.get(detail_url)
    assert "claim_approved" in page.text


def test_reviewed_bios_scopes_are_audited_and_matched_without_version_inference(
    client,
    pdf_bytes_factory,
):
    passage_id = _approved_evidence_and_setting(client, pdf_bytes_factory)
    scope = {
        "context_type": "motherboard_model",
        "context_id": "asrock-x870e-taichi-white",
        "bios_version_exact": "3.20",
        "comparison_mode": "opaque",
        "version_notes": "The cited release documents this behavior for BIOS 3.20.",
        "evidence_basis": "Reviewer verified the exact board and version in official evidence.",
    }
    created = client.post(
        "/api/v1/guidance-claims",
        json={
            "claim_text": "This behavior is documented for ASRock BIOS 3.20.",
            "claim_type": "behavior",
            "passage_ids": [passage_id],
            "canonical_ids": ["precision-boost-overdrive"],
            "bios_scopes": [scope],
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["bios_scopes"][0]["context_name"] == "X870E Taichi White"
    assert body["bios_scopes"][0]["bios_version_exact"] == "3.20"
    assert "bios_scopes" in body["events"][0]["detail"]

    approved = client.post(
        f"/api/v1/guidance-claims/{body['id']}/review",
        json={"decision": "approved", "note": "Exact BIOS scope verified."},
    )
    assert approved.status_code == 200

    matched = client.get(
        "/api/v1/guidance/explain",
        params={
            "setting": "precision-boost-overdrive",
            "motherboard_model_id": "asrock-x870e-taichi-white",
            "cpu_model_id": "amd-ryzen-7-9800x3d",
            "bios_version": "3.20",
        },
    )
    assert matched.status_code == 200
    assert matched.json()["facts"][0]["bios_match_status"] == "matched"
    assert matched.json()["facts"][0]["bios_scopes"][0]["evidence_basis"].startswith(
        "Reviewer verified"
    )

    different = client.get(
        "/api/v1/guidance/explain",
        params={
            "setting": "precision-boost-overdrive",
            "motherboard_model_id": "asrock-x870e-taichi-white",
            "cpu_model_id": "amd-ryzen-7-9800x3d",
            "bios_version": "3.21",
        },
    )
    assert different.json()["facts"] == []
    assert different.json()["excluded_fact_count"] == 1
    assert different.json()["answer_state"] == "No Verified Answer"
    assert "do not match" in different.json()["scope_notice"]

    no_version = client.get(
        "/api/v1/guidance/explain",
        params={
            "setting": "precision-boost-overdrive",
            "motherboard_model_id": "asrock-x870e-taichi-white",
            "cpu_model_id": "amd-ryzen-7-9800x3d",
        },
    )
    assert no_version.json()["facts"][0]["bios_match_status"] == "version_not_provided"
    assert no_version.json()["answer_state"] == "Partially Verified Answer"

    page = client.get(f"/guidance-claims/{body['id']}")
    assert "Reviewed BIOS-version applicability" in page.text
    assert "Exact version 3.20" in page.text


def test_bios_ranges_require_explicit_numeric_semantics(client, pdf_bytes_factory):
    passage_id = _approved_evidence_and_setting(client, pdf_bytes_factory)
    base = {
        "claim_text": "This behavior is documented for a reviewed BIOS range.",
        "claim_type": "constraint",
        "passage_ids": [passage_id],
        "canonical_ids": ["precision-boost-overdrive"],
    }
    invalid = client.post(
        "/api/v1/guidance-claims",
        json={
            **base,
            "bios_scopes": [
                {
                    "context_type": "vendor",
                    "context_id": "asrock",
                    "valid_from": "3.10",
                    "valid_until": "3.30",
                    "comparison_mode": "opaque",
                    "version_notes": "Range from official documentation.",
                    "evidence_basis": "Official version history reviewed.",
                }
            ],
        },
    )
    assert invalid.status_code == 400
    assert "Opaque BIOS versions cannot use ranges" in invalid.json()["detail"]

    valid = client.post(
        "/api/v1/guidance-claims",
        json={
            **base,
            "bios_scopes": [
                {
                    "context_type": "vendor",
                    "context_id": "asrock",
                    "valid_from": "3.10",
                    "valid_until": "3.30",
                    "comparison_mode": "dotted_numeric",
                    "version_notes": "Range from official documentation.",
                    "evidence_basis": "Official version history reviewed.",
                }
            ],
        },
    )
    assert valid.status_code == 201

    conflicting = client.put(
        f"/api/v1/guidance-claims/{valid.json()['id']}",
        json={
            **base,
            "bios_scopes": [
                {
                    "context_type": "vendor",
                    "context_id": "asrock",
                    "bios_version_exact": "3.20",
                    "valid_from": "3.10",
                    "comparison_mode": "dotted_numeric",
                    "version_notes": "Conflicting exact and range markers.",
                    "evidence_basis": "Validation regression test.",
                }
            ],
        },
    )
    assert conflicting.status_code == 400
    assert "either an exact BIOS version or a valid range" in conflicting.json()["detail"]


def test_retrieval_excludes_claims_for_a_different_reviewed_motherboard(
    client,
    pdf_bytes_factory,
):
    passage_id = _approved_evidence_and_setting(client, pdf_bytes_factory)
    created = client.post(
        "/api/v1/guidance-claims",
        json={
            "claim_text": "This fact is documented for the X870E Taichi White only.",
            "claim_type": "constraint",
            "passage_ids": [passage_id],
            "canonical_ids": ["precision-boost-overdrive"],
            "applicability_scope": "reviewed_hardware",
            "applicability_targets": [
                {
                    "target_type": "motherboard_model",
                    "target_id": "asrock-x870e-taichi-white",
                    "evidence_basis": "Official evidence explicitly names this exact board.",
                }
            ],
        },
    ).json()
    client.post(
        f"/api/v1/guidance-claims/{created['id']}/review",
        json={"decision": "approved", "note": "Exact board scope verified."},
    )

    matched = client.get(
        "/api/v1/guidance/explain",
        params={
            "setting": "precision-boost-overdrive",
            "motherboard_model_id": "asrock-x870e-taichi-white",
            "cpu_model_id": "amd-ryzen-7-9800x3d",
        },
    ).json()
    assert matched["facts"][0]["hardware_match_status"] == "matched"

    excluded = client.get(
        "/api/v1/guidance/explain",
        params={
            "setting": "precision-boost-overdrive",
            "motherboard_model_id": "asus-tuf-gaming-x870-plus-wifi",
            "cpu_model_id": "amd-ryzen-7-9800x3d",
        },
    ).json()
    assert excluded["facts"] == []
    assert excluded["excluded_fact_count"] == 1
    assert excluded["answer_state"] == "No Verified Answer"
    assert "excluded" in excluded["context_notice"]
    gap = client.get("/api/v1/refusal-gaps/events").json()[0]
    assert gap["reason"] == "no_accepted_guidance_claims"
    assert gap["context"]["applicability"] == "scope_mismatch"
