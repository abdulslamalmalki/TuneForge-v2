"""Manual source provenance through the JSON API and reviewer UI."""

PROSE_PAGE = "Plain lowercase reference prose for extraction threshold padding. " * 4
ASUS_SUPPORT_URL = "https://www.asus.com/supportonly/b650-test/helpdesk_manual/"
MSI_SUPPORT_URL = "https://www.msi.com/Motherboard/MPG-X670E-CARBON-WIFI/support"
GIGABYTE_SUPPORT_URL = (
    "https://www.gigabyte.com/Motherboard/B650-AORUS-ELITE-AX/support#support-manual"
)


def _api_upload(client, pdf_bytes_factory, **overrides):
    data = {
        "vendor": "ASUS",
        "model_name": "B650 Test",
        "document_title": "Official BIOS Setup Guide",
        "source_url": ASUS_SUPPORT_URL,
        "document_version": "1.0",
        "published_on": "2026-05-01",
        "language": "en-US",
        "board_revision": "1.1",
        "chipset": "B650",
        "eligibility_workflow": "legacy",
    }
    data.update(overrides)
    return client.post(
        "/api/v1/manuals",
        files={
            "file": (
                "manual.pdf",
                pdf_bytes_factory(texts=[PROSE_PAGE]),
                "application/pdf",
            )
        },
        data=data,
    )


def test_api_upload_returns_complete_provenance_and_creation_event(client, pdf_bytes_factory):
    response = _api_upload(client, pdf_bytes_factory)
    assert response.status_code == 201
    body = response.json()
    assert body["document_title"] == "Official BIOS Setup Guide"
    assert body["vendor"] == "ASUS"
    assert body["vendor_id"] == "asus"
    assert body["source_url"] == ASUS_SUPPORT_URL
    assert body["document_version"] == "1.0"
    assert body["published_on"] == "2026-05-01"
    assert body["language"] == "en-US"
    assert body["board_revision"] == "1.1"
    assert body["chipset"] == "B650"
    assert body["provenance_complete"] is True
    assert body["missing_provenance_fields"] == []
    assert len(body["sha256"]) == 64
    assert [event["action"] for event in body["events"]] == [
        "provenance_created",
        "hardware_applicability_normalized",
    ]


def test_api_list_marks_incomplete_and_complete_sources(client, pdf_bytes_factory):
    incomplete = client.post(
        "/api/v1/manuals",
        files={"file": ("incomplete.pdf", pdf_bytes_factory(), "application/pdf")},
        data={"eligibility_workflow": "legacy"},
    )
    assert incomplete.status_code == 201
    complete = _api_upload(client, pdf_bytes_factory)
    assert complete.status_code == 201

    rows = client.get("/api/v1/manuals").json()
    complete_by_id = {row["id"]: row["provenance_complete"] for row in rows}
    assert complete_by_id[incomplete.json()["id"]] is False
    assert complete_by_id[complete.json()["id"]] is True


def test_api_replaces_provenance_and_appends_event(client, pdf_bytes_factory):
    uploaded = _api_upload(client, pdf_bytes_factory)
    manual_id = uploaded.json()["id"]
    retrieved_at = uploaded.json()["retrieved_at"]
    sha256 = uploaded.json()["sha256"]

    response = client.put(
        f"/api/v1/manuals/{manual_id}/provenance",
        json={
            "vendor": "MSI",
            "model_name": "X670 Update",
            "document_title": "Updated Official Guide",
            "source_url": MSI_SUPPORT_URL,
            "document_version": "2.0",
            "published_on": "2026-06-02",
            "language": "en",
            "board_revision": "2.0",
            "chipset": "X670",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["vendor"] == "MSI"
    assert body["vendor_id"] == "msi"
    assert body["document_title"] == "Updated Official Guide"
    assert body["retrieved_at"] == retrieved_at
    assert body["sha256"] == sha256
    assert [event["action"] for event in body["events"]] == [
        "provenance_created",
        "hardware_applicability_normalized",
        "provenance_updated",
        "hardware_applicability_normalized",
    ]

    no_op = client.put(
        f"/api/v1/manuals/{manual_id}/provenance",
        json={
            "vendor": "MSI",
            "model_name": "X670 Update",
            "document_title": "Updated Official Guide",
            "source_url": MSI_SUPPORT_URL,
            "document_version": "2.0",
            "published_on": "2026-06-02",
            "language": "en",
            "board_revision": "2.0",
            "chipset": "X670",
        },
    )
    assert len(no_op.json()["events"]) == 4


def test_api_invalid_update_returns_400_without_partial_change(client, pdf_bytes_factory):
    uploaded = _api_upload(client, pdf_bytes_factory)
    manual_id = uploaded.json()["id"]

    response = client.put(
        f"/api/v1/manuals/{manual_id}/provenance",
        json={
            "vendor": "Changed",
            "document_title": "Changed",
            "source_url": "file:///local.pdf",
        },
    )
    assert response.status_code == 400
    assert "HTTP or HTTPS" in response.json()["detail"]

    body = client.get(f"/api/v1/manuals/{manual_id}").json()
    assert body["vendor"] == "ASUS"
    assert body["document_title"] == "Official BIOS Setup Guide"
    assert len(body["events"]) == 2


def test_api_provenance_update_unknown_manual_is_json_404(client):
    response = client.put("/api/v1/manuals/9999/provenance", json={})
    assert response.status_code == 404
    assert response.json() == {"detail": "Manual not found."}


def test_html_curator_can_backfill_provenance(client, pdf_bytes_factory):
    upload = client.post(
        "/manuals",
        files={"file": ("manual.pdf", pdf_bytes_factory(), "application/pdf")},
        data={"eligibility_workflow": "legacy"},
        follow_redirects=False,
    )
    detail_url = upload.headers["location"]
    edit = client.get(f"{detail_url}/provenance/edit")
    assert edit.status_code == 200
    assert "Edit manual provenance" in edit.text

    saved = client.post(
        f"{detail_url}/provenance",
        data={
            "vendor": "Gigabyte",
            "model_name": "X670 Test",
            "document_title": "Official Setup Guide",
            "source_url": GIGABYTE_SUPPORT_URL,
            "document_version": "1.0",
            "published_on": "2026-04-03",
            "language": "en",
            "board_revision": "1.0",
            "chipset": "X670",
        },
        follow_redirects=False,
    )
    assert saved.status_code == 303
    detail = client.get(detail_url)
    assert "Official Setup Guide" in detail.text
    assert "GIGABYTE" in detail.text
    assert "<code>gigabyte</code>" in detail.text
    assert GIGABYTE_SUPPORT_URL in detail.text
    assert "provenance_updated" in detail.text
    assert "Missing:" not in detail.text


def test_html_invalid_backfill_preserves_submitted_values(client, pdf_bytes_factory):
    upload = client.post(
        "/manuals",
        files={"file": ("manual.pdf", pdf_bytes_factory(), "application/pdf")},
        data={"eligibility_workflow": "legacy"},
        follow_redirects=False,
    )
    detail_url = upload.headers["location"]
    response = client.post(
        f"{detail_url}/provenance",
        data={
            "vendor": "ASUS",
            "document_title": "Attempted Title",
            "source_url": "not-a-url",
            "language": "en",
        },
    )
    assert response.status_code == 400
    assert "HTTP or HTTPS" in response.text
    assert 'value="Attempted Title"' in response.text


def test_openapi_documents_provenance_update(client):
    operation = client.get("/openapi.json").json()["paths"][
        "/api/v1/manuals/{manual_id}/provenance"
    ]["put"]
    assert operation["summary"] == "Replace manual provenance"
    assert "400" in operation["responses"]
    assert "404" in operation["responses"]
