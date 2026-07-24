"""Acceptance report API and curator workflow."""

from app.db.models import ExtractionRun, Setting
from app.db.session import SessionLocal

PROSE_PAGE = "Plain lowercase reference prose for extraction threshold padding. " * 4
ASUS_SUPPORT_URL = "https://www.asus.com/supportonly/b650-test/helpdesk_manual/"


def _upload_complete(client, pdf_bytes_factory):
    response = client.post(
        "/api/v1/manuals",
        files={"file": ("manual.pdf", pdf_bytes_factory(texts=[PROSE_PAGE]), "application/pdf")},
        data={
            "vendor": "ASUS",
            "model_name": "B650 Test",
            "document_title": "Official BIOS Guide",
            "source_url": ASUS_SUPPORT_URL,
            "language": "en",
            "eligibility_workflow": "legacy",
        },
    )
    assert response.status_code == 201
    return response.json()["id"]


def _seed_drafts(manual_id, count=3):
    ids = []
    with SessionLocal() as session:
        run = session.query(ExtractionRun).filter_by(manual_id=manual_id).one()
        for index in range(count):
            setting = Setting(
                manual_id=manual_id,
                extraction_run_id=run.id,
                extracted_name=f"Setting {index + 1}",
                extracted_description="Description",
                page_number=1,
                status="draft",
                created_at="2026-01-01T00:00:00+00:00",
                updated_at="2026-01-01T00:00:00+00:00",
            )
            session.add(setting)
            session.flush()
            ids.append(setting.id)
        run.settings_found = count
        session.commit()
    return ids


def test_api_acceptance_report_tracks_reviews_and_missed_settings(client, pdf_bytes_factory):
    manual_id = _upload_complete(client, pdf_bytes_factory)
    approved, rejected, pending = _seed_drafts(manual_id)
    client.patch(
        f"/api/v1/settings/{approved}",
        json={"name": "Corrected Setting", "description": "Description"},
    )
    client.post(f"/api/v1/settings/{approved}/approve", json={})
    client.post(f"/api/v1/settings/{rejected}/reject", json={"reason": "Not a BIOS setting."})

    response = client.post(
        f"/api/v1/manuals/{manual_id}/missed-settings",
        json={
            "name": "Missed Voltage Control",
            "page_number": 1,
            "description": "Not emitted by the parser.",
            "note": "Found during manual comparison.",
        },
    )
    assert response.status_code == 201
    body = response.json()
    report = body["report"]
    assert report["total_candidates"] == 3
    assert report["reviewed_count"] == 2
    assert report["draft_count"] == 1
    assert report["corrected_count"] == 1
    assert report["missed_count"] == 1
    assert report["precision"] == 0.5
    assert report["estimated_recall"] == 0.5
    assert body["missed_settings"][0]["name"] == "Missed Voltage Control"

    blocked = client.post(
        f"/api/v1/manuals/{manual_id}/acceptance/certify", json={"notes": "Checked."}
    )
    assert blocked.status_code == 400
    assert "drafts remain" in blocked.json()["detail"]

    client.post(f"/api/v1/settings/{pending}/reject", json={"reason": "Heading, not a setting."})
    page_blocked = client.post(
        f"/api/v1/manuals/{manual_id}/acceptance/certify", json={"notes": "Checked."}
    )
    assert page_blocked.status_code == 400
    assert "page reference" in page_blocked.json()["detail"]

    page_review = client.post(
        f"/api/v1/manuals/{manual_id}/settings/{approved}/page-reference",
        json={"page_number": 1},
    )
    assert page_review.status_code == 200
    assert page_review.json()["report"]["page_accuracy"] == 1.0
    assert page_review.json()["page_references"][0]["review_status"] == "correct"
    certified = client.post(
        f"/api/v1/manuals/{manual_id}/acceptance/certify", json={"notes": "Checked."}
    )
    assert certified.status_code == 200
    assert certified.json()["report"]["acceptance_complete"] is True


def test_api_dismisses_missed_setting_and_rejects_cross_manual_id(client, pdf_bytes_factory):
    first = _upload_complete(client, pdf_bytes_factory)
    # Different bytes avoid SHA-256 duplicate rejection.
    second_response = client.post(
        "/api/v1/manuals",
        files={
            "file": (
                "second.pdf",
                pdf_bytes_factory(texts=[PROSE_PAGE, PROSE_PAGE]),
                "application/pdf",
            )
        },
        data={
            "vendor": "MSI",
            "document_title": "Second",
            "source_url": "https://x.test/2",
            "eligibility_workflow": "legacy",
        },
    )
    second = second_response.json()["id"]
    added = client.post(
        f"/api/v1/manuals/{first}/missed-settings",
        json={"name": "Missed", "page_number": 1},
    ).json()
    missed_id = added["missed_settings"][0]["id"]

    wrong_manual = client.post(
        f"/api/v1/manuals/{second}/missed-settings/{missed_id}/dismiss",
        json={"reason": "Wrong"},
    )
    assert wrong_manual.status_code == 400
    assert "not found for this manual" in wrong_manual.json()["detail"]

    dismissed = client.post(
        f"/api/v1/manuals/{first}/missed-settings/{missed_id}/dismiss",
        json={"reason": "Duplicate entry"},
    )
    assert dismissed.status_code == 200
    assert dismissed.json()["missed_settings"] == []
    assert dismissed.json()["report"]["missed_count"] == 0


def test_html_acceptance_page_records_missed_setting_and_shows_metrics(client, pdf_bytes_factory):
    manual_id = _upload_complete(client, pdf_bytes_factory)
    page = client.get(f"/manuals/{manual_id}/acceptance")
    assert page.status_code == 200
    assert "Extraction acceptance" in page.text
    assert "provisional" in page.text

    saved = client.post(
        f"/manuals/{manual_id}/missed-settings",
        data={"name": "Missed Setting", "page_number": 1, "description": "Description"},
        follow_redirects=False,
    )
    assert saved.status_code == 303
    page = client.get(f"/manuals/{manual_id}/acceptance")
    assert "Missed Setting" in page.text
    assert "Recorded missed settings" in page.text


def test_acceptance_api_unknown_manual_is_json_404(client):
    assert client.get("/api/v1/manuals/9999/acceptance").status_code == 404
    response = client.post(
        "/api/v1/manuals/9999/missed-settings", json={"name": "X", "page_number": 1}
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Manual not found."}


def test_openapi_documents_acceptance_endpoints(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v1/manuals/{manual_id}/acceptance" in paths
    assert "/api/v1/manuals/{manual_id}/missed-settings" in paths
    assert "/api/v1/manuals/{manual_id}/settings/{setting_id}/page-reference" in paths
    assert "/api/v1/manuals/{manual_id}/acceptance/certify" in paths
