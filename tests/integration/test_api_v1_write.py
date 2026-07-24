"""API v1 write endpoints: upload and the review actions.

Same domain paths as the HTML forms — these tests cover the JSON round
trips, error mapping, and that the API drives the full pipeline (upload
-> extraction -> drafts) and search index exactly like the web does.
"""

import pytest

from app.db.models import ExtractionRun, Setting
from app.db.session import SessionLocal

PROSE_PAGE = "Plain lowercase reference prose for extraction threshold padding. " * 4
SETTING_PAGE = (
    "Ai Overclock Tuner\n"
    "Allows you to select the memory overclocking profile for this system.\n"
    "Configuration options: [Auto] [Manual]"
)


def _api_upload(client, pdf_bytes, filename="manual.pdf", vendor="ASUS", model_name="X670"):
    return client.post(
        "/api/v1/manuals",
        files={"file": (filename, pdf_bytes, "application/pdf")},
        data={
            "vendor": vendor,
            "model_name": model_name,
            "eligibility_workflow": "legacy",
        },
    )


def _seed_draft(client, pdf_bytes_factory):
    manual_id = _api_upload(client, pdf_bytes_factory(texts=[PROSE_PAGE])).json()["id"]
    with SessionLocal() as session:
        run = session.query(ExtractionRun).filter_by(manual_id=manual_id).one()
        setting = Setting(
            manual_id=manual_id,
            extraction_run_id=run.id,
            extracted_name="Ai Overclock Tuner",
            extracted_description="Original description.",
            page_number=3,
            status="draft",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
        session.add(setting)
        session.commit()
        return setting.id


def test_upload_creates_manual_and_runs_extraction(client, pdf_bytes_factory):
    response = _api_upload(client, pdf_bytes_factory(texts=[SETTING_PAGE, PROSE_PAGE]))
    assert response.status_code == 201
    body = response.json()
    assert body["original_filename"] == "manual.pdf"
    assert body["vendor"] == "ASUS"
    assert body["extraction_run"]["status"] == "pending"  # runs after the response

    # The background task has run by now (TestClient executes it post-response).
    detail = client.get(f"/api/v1/manuals/{body['id']}").json()
    assert detail["extraction_run"]["status"] == "succeeded"
    assert detail["extraction_run"]["settings_found"] == 1
    (draft,) = client.get("/api/v1/review-queue").json()
    assert draft["effective_name"] == "Ai Overclock Tuner"


def test_upload_rejects_non_pdf_bytes_as_json(client):
    response = _api_upload(client, b"not a pdf at all")
    assert response.status_code == 400
    assert "could not be read as a PDF" in response.json()["detail"]


def test_upload_duplicate_returns_409_with_existing_id(client, pdf_bytes_factory):
    pdf = pdf_bytes_factory(texts=[PROSE_PAGE])
    first_id = _api_upload(client, pdf).json()["id"]
    response = _api_upload(client, pdf, filename="again.pdf")
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["existing_manual_id"] == first_id
    assert "already been uploaded" in detail["message"]


def test_upload_rejects_overlong_vendor_label(client, pdf_bytes_factory):
    response = _api_upload(client, pdf_bytes_factory(texts=[PROSE_PAGE]), vendor="V" * 300)
    assert response.status_code == 400
    assert "Vendor" in response.json()["detail"]


def test_edit_updates_overrides_and_audits(client, pdf_bytes_factory):
    setting_id = _seed_draft(client, pdf_bytes_factory)
    response = client.patch(
        f"/api/v1/settings/{setting_id}", json={"name": "Renamed", "description": "Cleaned."}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["extracted_name"] == "Ai Overclock Tuner"  # FR-16: immutable
    assert body["edited_name"] == "Renamed"
    assert body["effective_description"] == "Cleaned."
    assert [event["action"] for event in body["events"]] == ["edited"]


def test_edit_empty_name_maps_domain_error_to_400(client, pdf_bytes_factory):
    setting_id = _seed_draft(client, pdf_bytes_factory)
    response = client.patch(f"/api/v1/settings/{setting_id}", json={"name": "   "})
    assert response.status_code == 400
    assert "name cannot be empty" in response.json()["detail"]


def test_approve_with_note_returns_updated_setting(client, pdf_bytes_factory):
    setting_id = _seed_draft(client, pdf_bytes_factory)
    response = client.post(
        f"/api/v1/settings/{setting_id}/approve", json={"note": "matches the manual"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "approved"
    assert body["review_note"] == "matches the manual"
    assert [event["action"] for event in body["events"]] == ["approved"]

    # Approval is immediately searchable through the API (FR-24).
    (result,) = client.get("/api/v1/search?q=Overclock").json()
    assert result["setting_id"] == setting_id


def test_approve_twice_is_a_400_state_error(client, pdf_bytes_factory):
    setting_id = _seed_draft(client, pdf_bytes_factory)
    client.post(f"/api/v1/settings/{setting_id}/approve", json={})
    response = client.post(f"/api/v1/settings/{setting_id}/approve", json={})
    assert response.status_code == 400
    assert "cannot be moved" in response.json()["detail"]


def test_reject_requires_a_reason(client, pdf_bytes_factory):
    setting_id = _seed_draft(client, pdf_bytes_factory)
    response = client.post(f"/api/v1/settings/{setting_id}/reject", json={"reason": "  "})
    assert response.status_code == 400
    assert "reason is required" in response.json()["detail"]
    assert client.get(f"/api/v1/settings/{setting_id}").json()["status"] == "draft"


def test_reject_with_reason_stores_note_and_audits(client, pdf_bytes_factory):
    setting_id = _seed_draft(client, pdf_bytes_factory)
    response = client.post(
        f"/api/v1/settings/{setting_id}/reject", json={"reason": "Garbled table text."}
    )
    body = response.json()
    assert body["status"] == "rejected"
    assert body["review_note"] == "Garbled table text."
    assert body["events"][-1]["detail"] == "Garbled table text."


def test_reopen_returns_to_draft_and_leaves_search(client, pdf_bytes_factory):
    setting_id = _seed_draft(client, pdf_bytes_factory)
    client.post(f"/api/v1/settings/{setting_id}/approve", json={})
    assert client.get("/api/v1/search?q=Overclock").json() != []

    response = client.post(f"/api/v1/settings/{setting_id}/reopen")
    body = response.json()
    assert body["status"] == "draft"
    assert body["review_note"] is None
    assert [event["action"] for event in body["events"]] == ["approved", "reopened"]
    assert client.get("/api/v1/search?q=Overclock").json() == []  # FR-26


def test_reopen_a_draft_is_a_400_state_error(client, pdf_bytes_factory):
    setting_id = _seed_draft(client, pdf_bytes_factory)
    response = client.post(f"/api/v1/settings/{setting_id}/reopen")
    assert response.status_code == 400
    assert "cannot be moved" in response.json()["detail"]


@pytest.mark.parametrize(
    ("method", "suffix"),
    [("patch", ""), ("post", "/approve"), ("post", "/reject"), ("post", "/reopen")],
)
def test_write_actions_on_unknown_setting_are_json_404(client, method, suffix):
    response = getattr(client, method)(f"/api/v1/settings/12345{suffix}", json={})
    assert response.status_code == 404
    assert response.json() == {"detail": "Setting not found."}


def test_html_review_actions_still_work(client, pdf_bytes_factory):
    setting_id = _seed_draft(client, pdf_bytes_factory)
    response = client.post(f"/settings/{setting_id}/approve", data={"note": ""})
    assert response.status_code == 200  # after the PRG redirect
    assert "Setting approved." in response.text
