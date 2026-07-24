from app.db.models import ExtractionRun, Setting
from app.db.session import SessionLocal

PROSE_PAGE = "Plain lowercase reference prose for extraction threshold padding. " * 4


def _approved_setting(client, pdf_bytes_factory):
    response = client.post(
        "/manuals",
        files={
            "file": (
                "canonical.pdf",
                pdf_bytes_factory(texts=[PROSE_PAGE]),
                "application/pdf",
            )
        },
        data={
            "vendor": "ASUS",
            "model_name": "AMD 800 Series",
            "eligibility_workflow": "legacy",
        },
        follow_redirects=False,
    )
    manual_id = int(response.headers["location"].rsplit("/", 1)[1])
    with SessionLocal() as session:
        run = session.query(ExtractionRun).filter_by(manual_id=manual_id).one()
        setting = Setting(
            manual_id=manual_id,
            extraction_run_id=run.id,
            extracted_name="Re-Size BAR Support",
            extracted_description="Controls PCIe resizable BAR support.",
            page_number=1,
            status="approved",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
        session.add(setting)
        session.commit()
        return setting.id


def test_api_catalog_suggestion_mapping_and_unmapping(client, pdf_bytes_factory):
    setting_id = _approved_setting(client, pdf_bytes_factory)
    created = client.post(
        "/api/v1/canonical-settings",
        json={
            "name": "Resizable BAR Support",
            "description": "Allows compatible devices to expose a larger PCIe BAR.",
            "aliases": ["Re-Size BAR Support"],
        },
    )
    assert created.status_code == 200
    assert created.json()["canonical_id"] == "resizable-bar-support"

    detail = client.get(f"/api/v1/settings/{setting_id}").json()
    assert detail["canonical_mapping"] is None
    assert detail["canonical_suggestions"][0]["canonical_id"] == "resizable-bar-support"

    mapped = client.put(
        f"/api/v1/settings/{setting_id}/canonical",
        json={
            "canonical_id": "resizable-bar-support",
            "note": "Exact alias and behavior reviewed.",
        },
    )
    assert mapped.status_code == 200
    assert mapped.json()["canonical_mapping"]["canonical_name"] == "Resizable BAR Support"
    assert mapped.json()["canonical_events"][0]["action"] == "mapped"

    (catalog_row,) = client.get("/api/v1/canonical-settings").json()
    assert catalog_row["mapping_count"] == 1
    assert catalog_row["vendor_count"] == 1
    canonical_detail = client.get("/api/v1/canonical-settings/resizable-bar-support").json()
    assert canonical_detail["mappings"][0]["setting_id"] == setting_id

    rejected = client.post(
        f"/api/v1/settings/{setting_id}/canonical/unmap",
        json={"reason": ""},
    )
    assert rejected.status_code == 400
    unmapped = client.post(
        f"/api/v1/settings/{setting_id}/canonical/unmap",
        json={"reason": "Reviewer found a semantic mismatch."},
    )
    assert unmapped.status_code == 200
    assert unmapped.json()["canonical_mapping"] is None
    assert unmapped.json()["canonical_events"][-1]["action"] == "unmapped"


def test_html_catalog_and_setting_workflow(client, pdf_bytes_factory):
    setting_id = _approved_setting(client, pdf_bytes_factory)
    response = client.post(
        "/canonical-settings",
        data={
            "name": "Resizable BAR Support",
            "description": "Vendor-neutral feature identity.",
            "aliases": "Re-Size BAR Support",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/canonical-settings/resizable-bar-support"

    page = client.get(f"/settings/{setting_id}").text
    assert "Exact alias suggestion" in page
    assert "reviewer confirmation is still required" in page

    mapped = client.post(
        f"/settings/{setting_id}/canonical",
        data={
            "canonical_id": "resizable-bar-support",
            "note": "Reviewed.",
        },
        follow_redirects=False,
    )
    assert mapped.status_code == 303
    page = client.get(mapped.headers["location"]).text
    assert "Canonical mapping saved." in page
    assert 'href="/canonical-settings/resizable-bar-support"' in page

    catalog = client.get("/canonical-settings").text
    assert "Resizable BAR Support" in catalog
    assert "AMD Ryzen 7 9800X3D" in catalog
    assert "does not yet publish tuning recommendations" in catalog
    assert ">1</td>" in catalog


def test_phase2_api_and_html_suggestion_review(client, pdf_bytes_factory):
    setting_id = _approved_setting(client, pdf_bytes_factory)
    client.post(
        "/api/v1/canonical-settings",
        json={
            "name": "Resizable BAR Support",
            "aliases": ["Re-Size BAR Support"],
        },
    )
    classified = client.put(
        "/api/v1/canonical-settings/resizable-bar-support/classification",
        json={
            "category_id": "pcie-resource",
            "risk_level": "moderate",
            "risk_rationale": "Changes PCIe resource allocation.",
        },
    )
    assert classified.status_code == 200
    assert classified.json()["classification"]["category_name"] == "PCIe Resources"
    assert classified.json()["classification_events"][-1]["action"] == "reclassified"

    refreshed = client.post("/api/v1/canonical-suggestions/refresh")
    assert refreshed.json()["created"] == 1
    (suggestion,) = client.get("/api/v1/canonical-suggestions").json()
    assert suggestion["setting_id"] == setting_id
    assert suggestion["risk_level"] == "moderate"

    html = client.get("/canonical-suggestions").text
    assert "A suggestion never" in html
    assert "Re-Size BAR Support" in html
    accepted = client.post(
        f"/api/v1/canonical-suggestions/{suggestion['suggestion_id']}/accept",
        json={"note": "Exact source behavior reviewed."},
    )
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "accepted"
    assert client.get("/api/v1/canonical-suggestions").json() == []
