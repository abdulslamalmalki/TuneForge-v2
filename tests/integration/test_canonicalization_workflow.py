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
    assert body["engine_version"] == "canonicalization-1.0"
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
