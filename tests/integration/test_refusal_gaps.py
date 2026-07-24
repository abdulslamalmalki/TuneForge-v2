"""Refusal demand logging and official referral-pointer boundaries."""

import pytest
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.db.models import ReferralPointer, ReferralPointerEvent, UnansweredLookup
from app.domain import refusal_gaps


def _create_identity(client, name="Precision Boost Overdrive"):
    response = client.post(
        "/api/v1/canonical-settings",
        json={
            "name": name,
            "description": "Reviewed canonical identity for refusal-gap testing.",
            "aliases": ["AMD Overclocking PBO"],
        },
    )
    assert response.status_code == 200
    return response.json()["canonical_id"]


def _register_pointer(client, canonical_id, **overrides):
    payload = {
        "publisher_id": "amd",
        "title": "Ryzen Master User Guide",
        "source_url": "https://docs.amd.com/r/en-US/68886-ryzen-master-user-guide",
        "document_id": "68886",
        "document_version": "3.1.0",
        "canonical_ids": [canonical_id],
        "review_note": "Official AMD domain and document identity reviewed.",
    }
    payload.update(overrides)
    return client.post("/api/v1/referral-pointers", json=payload)


def test_failed_search_appends_privacy_limited_demand_and_preserves_api_shape(client):
    canonical_id = _create_identity(client)
    query = "Precision Boost Overdriv"

    assert client.get("/api/v1/search", params={"q": query, "vendor_id": "asus"}).json() == []
    assert client.get("/api/v1/search", params={"q": query, "vendor_id": "asus"}).json() == []

    worklist = client.get("/api/v1/refusal-gaps").json()
    assert len(worklist) == 1
    gap = worklist[0]
    assert gap["query_text"] == query
    assert gap["occurrence_count"] == 2
    assert gap["context"] == {"vendor_id": "asus"}
    assert gap["nearest_matches"][0]["canonical_id"] == canonical_id
    assert gap["nearest_matches"][0]["match_kind"] == "canonical_name"

    event = client.get("/api/v1/refusal-gaps/events").json()[0]
    assert set(event["context"]) == {"vendor_id"}
    assert "ip" not in event["context"]
    assert "user_agent" not in event["context"]


def test_gap_query_is_whitespace_normalized_and_capped_at_300_characters(client, db_session):
    query = "  missing   " + ("setting " * 80)
    response = client.get("/api/v1/search", params={"q": query})
    assert response.status_code == 200

    row = db_session.query(UnansweredLookup).one()
    assert len(row.query_text) == 300
    assert "  " not in row.query_text
    assert len(row.normalized_query) <= 300
    assert row.context_json == "{}"


def test_blank_and_invalid_context_lookups_are_not_recorded(client):
    assert client.get("/api/v1/search").json() == []
    invalid = client.get(
        "/api/v1/search",
        params={"q": "missing", "cpu_model_id": "unknown", "model_id": "unknown"},
    )
    assert invalid.status_code == 400
    assert client.get("/api/v1/refusal-gaps/events").json() == []


def test_audit_storage_failure_never_replaces_the_safe_product_response(client, monkeypatch):
    def fail_to_append(*_args, **_kwargs):
        raise SQLAlchemyError("forced audit-storage failure")

    monkeypatch.setattr(refusal_gaps, "record_lookup_gap", fail_to_append)

    search = client.get("/api/v1/search", params={"q": "missing setting"})
    assert search.status_code == 200
    assert search.json() == []

    guidance = client.get(
        "/api/v1/guidance/explain",
        params={"setting": "missing setting"},
    )
    assert guidance.status_code == 404
    assert "No exact canonical setting" in guidance.json()["detail"]


def test_unknown_guidance_returns_only_reviewed_official_pointer_metadata(client):
    canonical_id = _create_identity(client)
    pointer = _register_pointer(client, canonical_id)
    assert pointer.status_code == 200

    response = client.get(
        "/api/v1/guidance/explain",
        params={"setting": "Precision Boost Overdriv"},
    )
    assert response.status_code == 404
    body = response.json()
    assert "No exact canonical setting" in body["detail"]
    assert len(body["referral_pointers"]) == 1
    referral = body["referral_pointers"][0]
    assert referral["source_url"].startswith("https://docs.amd.com/")
    assert "passage_text" not in referral
    assert "claim_text" not in referral

    event = client.get("/api/v1/refusal-gaps/events").json()[0]
    assert event["reason"] == "no_exact_guidance_match"


def test_exact_identity_without_claims_fails_closed_and_includes_linked_pointer(client):
    canonical_id = _create_identity(client)
    _register_pointer(client, canonical_id)

    response = client.get("/api/v1/guidance/explain", params={"setting": canonical_id})
    assert response.status_code == 200
    body = response.json()
    assert body["facts"] == []
    assert body["recommendation_status"] == "not_available"
    assert len(body["referral_pointers"]) == 1

    worklist = client.get("/api/v1/refusal-gaps").json()
    assert worklist[0]["reason"] == "no_accepted_guidance_claims"


def test_referral_registry_rejects_nonofficial_domains_and_duplicates(client):
    canonical_id = _create_identity(client)
    external = _register_pointer(
        client,
        canonical_id,
        source_url="https://example.com/ryzen-master",
    )
    assert external.status_code == 400
    assert "official HTTPS domain" in external.json()["detail"]

    first = _register_pointer(client, canonical_id)
    assert first.status_code == 200
    duplicate = _register_pointer(client, canonical_id)
    assert duplicate.status_code == 400
    assert "already registered" in duplicate.json()["detail"]


def test_referral_registry_rejects_deprecated_canonical_identity(client):
    canonical_id = _create_identity(client)
    deprecated = client.post(
        f"/api/v1/canonicalization/identities/{canonical_id}/deprecate",
        json={"reason": "Reviewer withdrew this identity from public use."},
    )
    assert deprecated.status_code == 200

    pointer = _register_pointer(client, canonical_id)
    assert pointer.status_code == 400
    assert "Unknown or deprecated canonical setting" in pointer.json()["detail"]


def test_deprecated_identity_does_not_surface_its_linked_referral(client):
    canonical_id = _create_identity(client)
    assert _register_pointer(client, canonical_id).status_code == 200

    deprecated = client.post(
        f"/api/v1/canonicalization/identities/{canonical_id}/deprecate",
        json={"reason": "Reviewer withdrew this identity from public use."},
    )
    assert deprecated.status_code == 200

    response = client.get(
        "/api/v1/guidance/explain",
        params={"setting": "Precision Boost Overdriv"},
    )
    assert response.status_code == 404
    assert response.json()["referral_pointers"] == []


def test_referral_retirement_is_audited_and_removes_pointer_from_refusals(client, db_session):
    canonical_id = _create_identity(client)
    pointer = _register_pointer(client, canonical_id).json()
    retirement = client.post(
        f"/api/v1/referral-pointers/{pointer['id']}/retire",
        json={"review_note": "Publisher replaced this document reference."},
    )
    assert retirement.status_code == 200
    assert retirement.json()["status"] == "retired"
    assert [event["action"] for event in retirement.json()["events"]] == [
        "registered",
        "retired",
    ]

    events = db_session.query(ReferralPointerEvent).order_by(ReferralPointerEvent.id).all()
    assert [event.action for event in events] == ["registered", "retired"]
    assert "Publisher replaced" in events[-1].detail

    refused = client.get(
        "/api/v1/guidance/explain",
        params={"setting": "Precision Boost Overdriv"},
    )
    assert refused.status_code == 404
    assert refused.json()["referral_pointers"] == []


def test_pointer_audit_event_prevents_destructive_parent_deletion(client, db_session):
    canonical_id = _create_identity(client)
    pointer_id = _register_pointer(client, canonical_id).json()["id"]

    pointer = db_session.get(ReferralPointer, pointer_id)
    db_session.delete(pointer)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    assert db_session.get(ReferralPointer, pointer_id) is not None
    assert db_session.query(ReferralPointerEvent).filter_by(pointer_id=pointer_id).count() == 1


def test_reviewer_page_exposes_worklist_registry_and_privacy_notice(client):
    client.get("/search", params={"q": "unanswered voltage option"})
    page = client.get("/refusal-gaps")
    assert page.status_code == 200
    assert "Demand worklist" in page.text
    assert "unanswered voltage option" in page.text
    assert "Official referral registry" in page.text
    assert "exclude network identifiers" in page.text
