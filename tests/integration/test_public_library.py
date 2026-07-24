"""Public BIOS Settings Library trust, discovery, citation, and UI contracts."""

from app import config
from app.db.models import CanonicalSetting, ExtractionRun, Setting
from app.db.session import SessionLocal
from app.domain import canonical_settings, review


def _source_setting(
    client,
    pdf_bytes_factory,
    *,
    vendor: str,
    filename: str,
    name: str,
    description: str,
    status: str = "approved",
    canonical_id: str | None = None,
) -> int:
    source_urls = {
        "ASUS": "https://www.asus.com/supportonly/rog-x870e/helpdesk_manual/",
        "MSI": "https://www.msi.com/Motherboard/MEG-X870E-GODLIKE/support#manual",
    }
    uploaded = client.post(
        "/manuals",
        files={
            "file": (
                filename,
                pdf_bytes_factory(texts=[f"{vendor} {name} official BIOS text. " * 20]),
                "application/pdf",
            )
        },
        data={
            "vendor": vendor,
            "model_name": "AMD 800 Series",
            "document_title": f"{vendor} AMD 800 Series BIOS Manual",
            "source_url": source_urls[vendor],
            "chipset": "AMD 800 Series",
            "eligibility_workflow": "legacy",
        },
        follow_redirects=False,
    )
    manual_id = int(uploaded.headers["location"].rsplit("/", 1)[1])
    with SessionLocal() as session:
        run = session.query(ExtractionRun).filter_by(manual_id=manual_id).one()
        setting = Setting(
            manual_id=manual_id,
            extraction_run_id=run.id,
            extracted_name=name,
            extracted_description=description,
            page_number=12,
            status="draft",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
        session.add(setting)
        session.commit()
        setting_id = setting.id
        if status == "approved":
            review.approve_setting(session, setting, note="Public Library fixture approval.")
            if canonical_id:
                canonical_settings.map_setting(
                    session,
                    setting,
                    canonical_id=canonical_id,
                    note="Reviewed vendor equivalence.",
                )
        elif status == "rejected":
            review.reject_setting(session, setting, "Not a real setting.")
        return setting_id


def _knowledge_fixture(client, pdf_bytes_factory):
    with SessionLocal() as session:
        identity = canonical_settings.create_identity(
            session,
            name="Precision Boost Overdrive",
            description="Controls AMD automatic boost behavior within reviewed limits.",
            aliases=(),
        )
        canonical_settings.classify_identity(
            session,
            identity,
            category_id="cpu-performance",
            risk_level="moderate",
            risk_rationale="Changing boost controls can affect stability, power, and temperature.",
        )
        canonical_id = identity.canonical_id

    asus_id = _source_setting(
        client,
        pdf_bytes_factory,
        vendor="ASUS",
        filename="asus-library.pdf",
        name="Precision Boost Overdrive",
        description="Selects the reviewed Precision Boost Overdrive mode.",
        canonical_id=canonical_id,
    )
    msi_id = _source_setting(
        client,
        pdf_bytes_factory,
        vendor="MSI",
        filename="msi-library.pdf",
        name="CPU Boost Scalar",
        description="Controls a scalar used by documented boost behavior.",
        canonical_id=canonical_id,
    )
    unmapped_id = _source_setting(
        client,
        pdf_bytes_factory,
        vendor="ASUS",
        filename="asus-unmapped-library.pdf",
        name="Memory Context Restore",
        description="Controls restoration of trained memory context.",
    )
    draft_id = _source_setting(
        client,
        pdf_bytes_factory,
        vendor="ASUS",
        filename="asus-draft-library.pdf",
        name="Internal Draft Setting",
        description="Must never become public.",
        status="draft",
    )
    rejected_id = _source_setting(
        client,
        pdf_bytes_factory,
        vendor="MSI",
        filename="msi-rejected-library.pdf",
        name="Rejected Parser Heading",
        description="Must never become public.",
        status="rejected",
    )
    return canonical_id, asus_id, msi_id, unmapped_id, draft_id, rejected_id


def _accepted_guidance(client, pdf_bytes_factory, canonical_id):
    source = client.post(
        "/api/v1/evidence-sources",
        files={
            "file": (
                "amd-library-evidence.pdf",
                pdf_bytes_factory(texts=["Precision Boost Overdrive behavior and limits. " * 20]),
                "application/pdf",
            )
        },
        data={
            "publisher_id": "amd",
            "title": "Ryzen Master User Guide",
            "source_url": "https://docs.amd.com/r/en-US/68886-ryzen-master-user-guide",
            "document_id": "68886",
            "document_version": "3.1.0",
            "published_on": "2026-05-20",
            "language": "en",
        },
    ).json()
    approved = client.post(
        f"/api/v1/evidence-sources/{source['id']}/review",
        json={"decision": "approved", "note": "Official evidence reviewed."},
    ).json()
    claim = client.post(
        "/api/v1/guidance-claims",
        json={
            "claim_text": "Precision Boost Overdrive can use reviewed platform limits.",
            "claim_type": "behavior",
            "passage_ids": [approved["passages"][0]["id"]],
            "canonical_ids": [canonical_id],
        },
    ).json()
    response = client.post(
        f"/api/v1/guidance-claims/{claim['id']}/review",
        json={"decision": "approved", "note": "Claim matches the cited passage."},
    )
    assert response.status_code == 200


def _all_keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _all_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _all_keys(item)


def test_library_browses_only_approved_settings_with_filters_and_pagination(
    client, pdf_bytes_factory
):
    canonical_id, asus_id, msi_id, unmapped_id, draft_id, rejected_id = _knowledge_fixture(
        client, pdf_bytes_factory
    )

    default_library = client.get("/library")
    assert default_library.status_code == 200
    assert "All BIOS Settings" in default_library.text
    assert "Browse all BIOS settings" in default_library.text
    assert f"/library/source/{asus_id}" in default_library.text
    assert f"/library/source/{msi_id}" in default_library.text
    assert f"/library/source/{unmapped_id}" in default_library.text
    assert "3</dt><dd>All approved settings" in default_library.text
    assert "2</dt><dd>Cross-vendor mapped" in default_library.text
    assert "1</dt><dd>Awaiting canonical mapping" in default_library.text
    assert "Internal Draft Setting" not in default_library.text
    assert "Rejected Parser Heading" not in default_library.text

    canonical = client.get("/library", params={"view": "canonical", "q": "PBO"})
    assert canonical.status_code == 200
    assert "BIOS Settings Library" in canonical.text
    assert f"style.css?v={config.APP_VERSION}" in canonical.text
    assert 'class="public-main library-main"' in canonical.text
    assert 'href="/library" aria-current="page"' in canonical.text
    assert 'class="library-filter-heading"' in canonical.text
    assert 'class="library-card-title-row"' in canonical.text
    assert "Search Library" in canonical.text
    assert "Precision Boost Overdrive" in canonical.text
    assert "CPU Boost Scalar" in canonical.text
    assert canonical.text.index("Precision Boost Overdrive") < canonical.text.index(
        "CPU Boost Scalar"
    )

    source = client.get(
        "/library",
        params={
            "view": "source",
            "vendor_id": "msi",
            "platform_id": "amd-am5",
            "chipset_id": "amd-x870",
        },
    )
    assert source.status_code == 200
    assert f"/library/source/{msi_id}" in source.text
    assert f"/library/source/{asus_id}" not in source.text
    assert "Internal Draft Setting" not in source.text
    assert "Rejected Parser Heading" not in source.text

    source_search = client.get(
        "/api/v1/library/settings",
        params={"view": "source", "q": "scalar"},
    ).json()
    assert source_search["total"] == 1
    assert source_search["items"][0]["setting_id"] == msi_id

    classified = client.get(
        "/api/v1/library/settings",
        params={"category_id": "cpu-performance", "risk_level": "moderate"},
    ).json()
    assert classified["total"] == 1
    assert classified["items"][0]["canonical_id"] == canonical_id

    page = client.get(
        "/api/v1/library/settings",
        params={"view": "source", "page": 2, "page_size": 1},
    ).json()
    assert page["total"] == 3
    assert page["page"] == 2
    assert page["page_size"] == 1
    assert page["total_pages"] == 3
    assert len(page["items"]) == 1
    assert {asus_id, msi_id, unmapped_id}.isdisjoint({draft_id, rejected_id})

    assert client.get(f"/api/v1/library/source/{draft_id}").status_code == 404
    assert client.get(f"/api/v1/library/source/{rejected_id}").status_code == 404
    assert client.get(f"/library/source/{draft_id}").status_code == 404
    assert client.get(f"/library/source/{rejected_id}").status_code == 404
    assert canonical_id == "precision-boost-overdrive"


def test_library_details_combine_manual_and_evidence_citations_without_internal_data(
    client, pdf_bytes_factory
):
    canonical_id, asus_id, msi_id, unmapped_id, _, _ = _knowledge_fixture(client, pdf_bytes_factory)
    _accepted_guidance(client, pdf_bytes_factory, canonical_id)

    canonical_page = client.get(f"/library/canonical/{canonical_id}")
    assert canonical_page.status_code == 200
    assert "Vendor names and citations" in canonical_page.text
    assert "ASUS AMD 800 Series BIOS Manual" in canonical_page.text
    assert "MSI AMD 800 Series BIOS Manual" in canonical_page.text
    assert "Ryzen Master User Guide" in canonical_page.text
    assert "Precision Boost Overdrive can use reviewed platform limits" in canonical_page.text
    assert f"/answer?setting={canonical_id}" in canonical_page.text
    assert 'href="/compare?' in canonical_page.text
    assert f"/library/source/{asus_id}" in canonical_page.text
    assert f"/library/source/{msi_id}" in canonical_page.text

    source_page = client.get(f"/library/source/{asus_id}")
    assert source_page.status_code == 200
    assert "Official citation" in source_page.text
    assert "Page 12" in source_page.text
    assert f"/library/canonical/{canonical_id}" in source_page.text
    assert "Evidence-backed guidance" in source_page.text

    unmapped = client.get(f"/library/source/{unmapped_id}")
    assert unmapped.status_code == 200
    assert "no canonical identity yet" in unmapped.text
    assert "does not infer equivalence" in unmapped.text

    body = client.get(f"/api/v1/library/canonical/{canonical_id}").json()
    assert body["setting"]["guidance_available"] is True
    assert len(body["facts"]) == 1
    assert body["facts"][0]["citations"][0]["source_title"] == "Ryzen Master User Guide"
    forbidden = {
        "review_note",
        "reviewer",
        "events",
        "actor",
        "extracted_name",
        "extracted_description",
        "passage_text",
    }
    assert forbidden.isdisjoint(set(_all_keys(body)))


def test_library_api_is_documented_as_an_explicit_public_contract(client):
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]

    assert "/api/v1/library/settings" in paths
    assert "/api/v1/library/canonical/{canonical_id}" in paths
    assert "/api/v1/library/source/{setting_id}" in paths
    response = paths["/api/v1/library/settings"]["get"]["responses"]["200"]
    assert response["content"]["application/json"]["schema"]["$ref"].endswith("/LibraryPageOut")


def test_deprecated_canonical_identity_is_hidden_but_source_evidence_remains_public(
    client,
    pdf_bytes_factory,
):
    canonical_id, asus_id, _, _, _, _ = _knowledge_fixture(client, pdf_bytes_factory)
    with SessionLocal() as session:
        identity = session.get(CanonicalSetting, canonical_id)
        canonical_settings.set_identity_status(
            session,
            identity,
            status=canonical_settings.IDENTITY_DEPRECATED,
            reason="Reviewer is correcting this cross-vendor identity.",
        )

    assert client.get(f"/api/v1/library/canonical/{canonical_id}").status_code == 404
    catalog = client.get(
        "/api/v1/library/settings",
        params={"q": "Precision Boost Overdrive"},
    ).json()
    assert catalog["total"] == 0
    source = client.get(f"/api/v1/library/source/{asus_id}").json()
    assert source["setting"]["canonical_id"] is None

    with SessionLocal() as session:
        identity = session.get(CanonicalSetting, canonical_id)
        canonical_settings.set_identity_status(
            session,
            identity,
            status=canonical_settings.IDENTITY_ACTIVE,
            reason="Corrected identity was re-verified.",
        )
    assert client.get(f"/api/v1/library/canonical/{canonical_id}").status_code == 200
