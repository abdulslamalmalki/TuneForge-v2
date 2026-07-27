def test_canonicalization_backlog_api_is_structured_and_empty_when_no_work_exists(
    client,
):
    response = client.get("/api/v1/canonicalization/backlog")

    assert response.status_code == 200
    assert response.json() == {
        "items": [],
        "total": 0,
        "page": 1,
        "page_size": 50,
        "pages": 1,
        "proposed_groups": [],
    }


def test_canonicalization_reviewer_api_returns_a_complete_report(client):
    response = client.post("/api/v1/canonicalization/review")

    assert response.status_code == 200
    body = response.json()
    assert body["engine_version"] == "canonicalization-1.1"
    assert body["confidence_threshold_basis_points"] == 9950
    assert body["evaluated_count"] == 0
    assert body["auto_approved_mappings"] == []
    assert body["new_canonical_settings"] == []
    assert body["remaining_manual_review_count"] == 0


def test_admin_canonicalization_page_explains_fail_closed_policy(client):
    response = client.get("/canonicalization")

    assert response.status_code == 200
    assert "Canonicalization Backlog" in response.text
    assert "99.50%" in response.text
    assert "evidence fails closed" in response.text


def test_identity_review_api_exposes_empty_queue_and_audited_lifecycle(client):
    assert client.get("/api/v1/canonicalization/identity-proposals").json() == []
    missing_context = client.put(
        "/api/v1/canonicalization/settings/999/menu-context",
        json={
            "menu_path": "Advanced > AMD CBS",
            "source_page_number": 1,
            "evidence_excerpt": "Official menu evidence.",
        },
    )
    assert missing_context.status_code == 400

    created = client.post(
        "/api/v1/canonical-settings",
        json={"name": "Lifecycle Test Control"},
    ).json()
    canonical_id = created["canonical_id"]
    deprecated = client.post(
        f"/api/v1/canonicalization/identities/{canonical_id}/deprecate",
        json={"reason": "Reviewer is correcting this identity."},
    )
    assert deprecated.json()["status"] == "deprecated"
    reactivated = client.post(
        f"/api/v1/canonicalization/identities/{canonical_id}/reactivate",
        json={"reason": "Reviewer re-verified the identity."},
    )
    assert reactivated.json()["status"] == "active"
