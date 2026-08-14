"""Safe recommendation claims stay separate, scoped, cited, and reviewer-controlled."""


SOURCE = {
    "publisher_id": "amd",
    "title": "Official Recommendation Evidence",
    "source_url": "https://docs.amd.com/r/en-US/official-recommendation-evidence",
    "document_id": "recommendation-evidence",
    "document_version": "1.0",
    "published_on": "2026-01-01",
    "language": "en",
}


def _canonical(client):
    return client.post(
        "/api/v1/canonical-settings",
        json={
            "name": "Reviewed Test Control",
            "description": "A controlled recommendation test identity.",
        },
    ).json()["canonical_id"]


def _passage(client, pdf_bytes_factory):
    source = client.post(
        "/api/v1/evidence-sources",
        files={
            "file": (
                "recommendation.pdf",
                pdf_bytes_factory(
                    texts=[
                        "Official exact-system recommendation evidence. " * 20
                    ]
                ),
                "application/pdf",
            )
        },
        data=SOURCE,
    ).json()
    approved = client.post(
        f"/api/v1/evidence-sources/{source['id']}/review",
        json={"decision": "approved", "note": "Official source reviewed."},
    ).json()
    return approved["passages"][0]["id"]


def _payload(canonical_id, passage_id, **overrides):
    payload = {
        "canonical_id": canonical_id,
        "recommendation_text": (
            "For the reviewed exact AM5 test system and gaming use case, enable this control."
        ),
        "recommended_action": "enable",
        "use_case": "gaming",
        "risk_level": "moderate",
        "prerequisites": ["Confirm the exact motherboard and CPU identities."],
        "confidence_level": "high",
        "conflict_state": "none",
        "passage_ids": [passage_id],
        "applicability_targets": [
            {
                "target_type": "motherboard_model",
                "target_id": "asus-tuf-gaming-x870-plus-wifi",
                "evidence_basis": "The official evidence names this motherboard model.",
            },
            {
                "target_type": "cpu_model",
                "target_id": "amd-ryzen-7-9800x3d",
                "evidence_basis": "The official evidence names this CPU model.",
            },
        ],
        "bios_scopes": [
            {
                "context_type": "motherboard_model",
                "context_id": "asus-tuf-gaming-x870-plus-wifi",
                "bios_version_exact": "1801",
                "comparison_mode": "opaque",
                "version_notes": "Recommendation reviewed only for BIOS 1801.",
                "evidence_basis": "The official evidence identifies BIOS 1801.",
            }
        ],
    }
    payload.update(overrides)
    return payload


def test_recommendation_claim_requires_review_and_preserves_scope(client, pdf_bytes_factory):
    canonical_id = _canonical(client)
    passage_id = _passage(client, pdf_bytes_factory)

    created = client.post(
        "/api/v1/recommendation-claims",
        json=_payload(canonical_id, passage_id),
    )

    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "draft"
    assert body["canonical_id"] == canonical_id
    assert body["recommended_action"] == "enable"
    assert body["value_mode"] == "none"
    assert body["use_case"] == "gaming"
    assert body["risk_level"] == "moderate"
    assert body["confidence_level"] == "high"
    assert body["conflict_state"] == "none"
    assert len(body["applicability_targets"]) == 2
    assert body["bios_scopes"][0]["bios_version_exact"] == "1801"
    assert body["events"][0]["action"] == "candidate_created"

    queue = client.get("/recommendation-claims")
    detail = client.get(f"/recommendation-claims/{body['id']}")
    dashboard = client.get("/admin")
    assert queue.status_code == 200
    assert "Recommendation claims" in queue.text
    assert body["recommendation_text"] in queue.text
    assert detail.status_code == 200
    assert "Approval fails closed" in detail.text
    assert "BIOS 1801" in detail.text
    assert "Recommendation review" in dashboard.text

    html_approved = client.post(
        f"/recommendation-claims/{body['id']}/review",
        data={
            "decision": "approved",
            "note": "Exact action, hardware, BIOS, risk, and evidence reviewed.",
        },
        follow_redirects=False,
    )
    assert html_approved.status_code == 303
    approved = client.get(f"/api/v1/recommendation-claims/{body['id']}")
    assert approved.status_code == 200
    reviewed = approved.json()
    assert reviewed["status"] == "approved"
    assert reviewed["reviewer"]
    assert [event["action"] for event in reviewed["events"]] == [
        "candidate_created",
        "claim_approved",
    ]


def test_recommendation_draft_edits_are_audited_and_reviewed_claims_are_immutable(
    client,
    pdf_bytes_factory,
):
    canonical_id = _canonical(client)
    passage_id = _passage(client, pdf_bytes_factory)
    created = client.post(
        "/api/v1/recommendation-claims",
        json=_payload(canonical_id, passage_id),
    ).json()
    edited_payload = _payload(
        canonical_id,
        passage_id,
        recommendation_text=(
            "For the reviewed exact AM5 test system, leave this control on Auto."
        ),
        recommended_action="leave_auto",
    )

    edited = client.put(
        f"/api/v1/recommendation-claims/{created['id']}",
        json=edited_payload,
    )
    assert edited.status_code == 200
    assert [event["action"] for event in edited.json()["events"]] == [
        "candidate_created",
        "candidate_edited",
    ]

    client.post(
        f"/api/v1/recommendation-claims/{created['id']}/review",
        json={"decision": "approved", "note": "Reviewed exact recommendation."},
    )
    immutable = client.put(
        f"/api/v1/recommendation-claims/{created['id']}",
        json=edited_payload,
    )
    assert immutable.status_code == 400
    assert "Only draft" in immutable.json()["detail"]


def test_recommendation_candidate_rejects_missing_hardware_scope(client, pdf_bytes_factory):
    response = client.post(
        "/api/v1/recommendation-claims",
        json=_payload(
            _canonical(client),
            _passage(client, pdf_bytes_factory),
            applicability_targets=[],
        ),
    )

    assert response.status_code == 400
    assert "hardware target" in response.json()["detail"]


def test_recommendation_approval_rejects_unresolved_conflict_and_low_confidence(
    client,
    pdf_bytes_factory,
):
    canonical_id = _canonical(client)
    passage_id = _passage(client, pdf_bytes_factory)
    created = client.post(
        "/api/v1/recommendation-claims",
        json=_payload(
            canonical_id,
            passage_id,
            confidence_level="moderate",
            conflict_state="potential",
        ),
    ).json()

    response = client.post(
        f"/api/v1/recommendation-claims/{created['id']}/review",
        json={"decision": "approved", "note": "Attempt approval."},
    )

    assert response.status_code == 400
    assert "high or very-high confidence" in response.json()["detail"]


def test_recommendation_values_are_structured_and_cannot_attach_to_enable_action(
    client,
    pdf_bytes_factory,
):
    response = client.post(
        "/api/v1/recommendation-claims",
        json=_payload(
            _canonical(client),
            _passage(client, pdf_bytes_factory),
            exact_value="1.25",
            value_unit="V",
        ),
    )

    assert response.status_code == 400
    assert "Only set_value or set_range" in response.json()["detail"]


def test_approved_recommendation_is_not_exposed_as_factual_guidance(
    client,
    pdf_bytes_factory,
):
    canonical_id = _canonical(client)
    passage_id = _passage(client, pdf_bytes_factory)
    created = client.post(
        "/api/v1/recommendation-claims",
        json=_payload(canonical_id, passage_id),
    ).json()
    client.post(
        f"/api/v1/recommendation-claims/{created['id']}/review",
        json={"decision": "approved", "note": "Recommendation-only review."},
    )

    factual = client.get(
        "/api/v1/interface/presentation",
        params={"setting": canonical_id},
    ).json()

    assert factual["outcome"] == "refused"
    assert factual["recommendation_status"] == "not_available"
    assert factual["statements"] == []
