from app.db.models import ExtractionRun, Setting
from app.db.session import SessionLocal
from app.domain import canonical_settings, review

SOURCE_DATA = {
    "publisher_id": "amd",
    "title": "Ryzen Master User Guide",
    "source_url": "https://docs.amd.com/r/en-US/68886-ryzen-master-user-guide",
    "document_id": "68886",
    "document_version": "3.1.0",
    "published_on": "2026-05-20",
    "language": "en",
}


def _seed_guidance(client, pdf_bytes_factory):
    identity = client.post(
        "/api/v1/canonical-settings",
        json={
            "name": "Precision Boost Overdrive",
            "description": "AMD automatic boost control.",
            "aliases": ["AMD Overclocking Precision Boost Overdrive"],
        },
    )
    assert identity.status_code == 200
    prose = "PBO can operate up to motherboard power and current limits. " * 8
    source = client.post(
        "/api/v1/evidence-sources",
        files={
            "file": (
                "amd-guidance-read.pdf",
                pdf_bytes_factory(texts=[f"6.3.2 CPU\n{prose}"]),
                "application/pdf",
            )
        },
        data=SOURCE_DATA,
    ).json()
    approved_source = client.post(
        f"/api/v1/evidence-sources/{source['id']}/review",
        json={"decision": "approved", "note": "Official source verified."},
    ).json()
    passage_id = approved_source["passages"][0]["id"]
    claim = client.post(
        "/api/v1/guidance-claims",
        json={
            "claim_text": "PBO may operate up to motherboard power and current limits.",
            "claim_type": "behavior",
            "passage_ids": [passage_id],
            "canonical_ids": ["precision-boost-overdrive"],
        },
    ).json()
    approved_claim = client.post(
        f"/api/v1/guidance-claims/{claim['id']}/review",
        json={"decision": "approved", "note": "Source-faithful factual claim."},
    )
    assert approved_claim.status_code == 200
    return source["id"], passage_id, claim["id"]


def _seed_mapped_setting(
    client,
    pdf_bytes_factory,
    *,
    vendor,
    model,
    name,
    filename,
    source_url,
    page_number,
    canonical_id,
):
    uploaded = client.post(
        "/manuals",
        files={
            "file": (
                filename,
                pdf_bytes_factory(texts=[f"{vendor} {name} source text. " * 20]),
                "application/pdf",
            )
        },
        data={
            "vendor": vendor,
            "model_name": model,
            "document_title": f"{model} BIOS Manual",
            "source_url": source_url,
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
            extracted_description="Controls the scalar used by boost behavior.",
            page_number=page_number,
            status="draft",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
        session.add(setting)
        session.commit()
        review.approve_setting(session, setting)
        canonical_settings.map_setting(
            session,
            setting,
            canonical_id=canonical_id,
            note="Vendor-equivalent name reviewed against the BIOS manual.",
        )
        return setting.id


def _seed_vendor_equivalences(client, pdf_bytes_factory):
    records = (
        (
            "ASUS",
            "ROG X870E",
            "PBO Scalar",
            "asus-pbo.pdf",
            "https://www.asus.com/supportonly/rog-x870e/helpdesk_manual/",
            12,
        ),
        (
            "MSI",
            "MEG X870E",
            "CPU Boost Scalar",
            "msi-pbo.pdf",
            "https://www.msi.com/Motherboard/MEG-X870E-GODLIKE/support#manual",
            27,
        ),
    )
    return [
        _seed_mapped_setting(
            client,
            pdf_bytes_factory,
            vendor=vendor,
            model=model,
            name=name,
            filename=filename,
            source_url=source_url,
            page_number=page_number,
            canonical_id="precision-boost-overdrive",
        )
        for vendor, model, name, filename, source_url, page_number in records
    ]


def test_citation_first_api_returns_only_accepted_facts(client, pdf_bytes_factory):
    source_id, passage_id, claim_id = _seed_guidance(client, pdf_bytes_factory)
    response = client.get("/api/v1/canonical-settings/precision-boost-overdrive/guidance")
    assert response.status_code == 200
    body = response.json()
    assert body["canonical_id"] == "precision-boost-overdrive"
    assert body["resolved_by"] == "canonical_id"
    assert body["answer_mode"] == "accepted_claims_only"
    assert body["recommendation_status"] == "not_available"
    assert "will not invent values" in body["recommendation_notice"]
    assert "Advanced Micro Devices, Inc. — Ryzen Master User Guide" in body["scope_notice"]
    assert "not validated for a specific CPU or motherboard model" in body["scope_notice"]
    assert "9800X3D" not in body["scope_notice"]
    assert len(body["facts"]) == 1
    fact = body["facts"][0]
    assert fact["claim_id"] == claim_id
    assert fact["applicability_scope"] == "source_scope"
    citation = fact["citations"][0]
    assert citation["source_id"] == source_id
    assert citation["passage_id"] == passage_id
    assert citation["page_number"] == 1
    assert citation["source_url"] == SOURCE_DATA["source_url"]
    assert citation["document_version"] == "3.1.0"
    assert citation["passage_text"]
    assert len(citation["text_sha256"]) == 64


def test_exact_name_and_reviewed_alias_resolution_fail_closed(client, pdf_bytes_factory):
    _seed_guidance(client, pdf_bytes_factory)
    by_name = client.get(
        "/api/v1/guidance/explain",
        params={"setting": "Precision Boost Overdrive"},
    )
    assert by_name.status_code == 200
    assert by_name.json()["resolved_by"] == "canonical_name"

    by_alias = client.get(
        "/api/v1/guidance/explain",
        params={"setting": "AMD-Overclocking Precision Boost Overdrive"},
    )
    assert by_alias.status_code == 200
    assert by_alias.json()["resolved_by"] == "reviewed_alias"
    assert by_alias.json()["matched_alias"] == "AMD Overclocking Precision Boost Overdrive"

    acronym = client.get("/api/v1/guidance/explain", params={"setting": "PBO"})
    assert acronym.status_code == 404
    assert "No exact canonical setting" in acronym.json()["detail"]
    invented_question = client.get(
        "/api/v1/guidance/explain",
        params={"setting": "best PBO settings"},
    )
    assert invented_question.status_code == 404


def test_drafts_and_rejections_never_enter_read_model(client, pdf_bytes_factory):
    _, passage_id, _ = _seed_guidance(client, pdf_bytes_factory)
    draft = client.post(
        "/api/v1/guidance-claims",
        json={
            "claim_text": "Unreviewed draft claim.",
            "claim_type": "constraint",
            "passage_ids": [passage_id],
            "canonical_ids": ["precision-boost-overdrive"],
        },
    ).json()
    rejected = client.post(
        "/api/v1/guidance-claims",
        json={
            "claim_text": "Rejected interpretation.",
            "claim_type": "safety",
            "passage_ids": [passage_id],
            "canonical_ids": ["precision-boost-overdrive"],
        },
    ).json()
    client.post(
        f"/api/v1/guidance-claims/{rejected['id']}/review",
        json={"decision": "rejected", "note": "Not supported."},
    )
    body = client.get("/api/v1/canonical-settings/precision-boost-overdrive/guidance").json()
    assert len(body["facts"]) == 1
    assert {fact["claim_text"] for fact in body["facts"]}.isdisjoint(
        {draft["claim_text"], rejected["claim_text"]}
    )


def test_no_coverage_returns_empty_facts_without_filler(client):
    created = client.post(
        "/api/v1/canonical-settings",
        json={"name": "Uncovered Test Setting"},
    )
    assert created.status_code == 200
    explanation = client.get("/api/v1/canonical-settings/uncovered-test-setting/guidance")
    assert explanation.status_code == 200
    body = explanation.json()
    assert body["facts"] == []
    assert body["recommendation_status"] == "not_available"
    assert body["scope_notice"] == (
        "No accepted guidance claims are available for this setting, so TuneForge "
        "makes no hardware or CPU applicability claim."
    )

    all_coverage = client.get("/api/v1/guidance/coverage").json()
    assert all_coverage == [
        {
            "canonical_id": "uncovered-test-setting",
            "canonical_name": "Uncovered Test Setting",
            "approved_claim_count": 0,
        }
    ]
    assert client.get("/api/v1/guidance/coverage?approved_only=true").json() == []


def test_scope_notice_is_derived_from_the_retrieved_source(client, pdf_bytes_factory):
    created = client.post(
        "/api/v1/canonical-settings",
        json={"name": "Turbo Power Limit"},
    )
    assert created.status_code == 200
    prose = "Turbo power limits describe processor power behavior. " * 10
    source = client.post(
        "/api/v1/evidence-sources",
        files={
            "file": (
                "intel-tuning-guide.pdf",
                pdf_bytes_factory(texts=[f"2.1 Power Controls\n{prose}"]),
                "application/pdf",
            )
        },
        data={
            "publisher_id": "intel",
            "title": "Intel Processor Tuning Guide",
            "source_url": "https://www.intel.com/content/www/us/en/gaming/resources/tuning.html",
            "document_id": "intel-tuning",
            "document_version": "1.0",
            "published_on": "2026-01-15",
            "language": "en",
        },
    ).json()
    approved = client.post(
        f"/api/v1/evidence-sources/{source['id']}/review",
        json={"decision": "approved", "note": "Official Intel source verified."},
    ).json()
    claim = client.post(
        "/api/v1/guidance-claims",
        json={
            "claim_text": "Turbo power limits describe processor power behavior.",
            "claim_type": "definition",
            "passage_ids": [approved["passages"][0]["id"]],
            "canonical_ids": ["turbo-power-limit"],
        },
    ).json()
    reviewed = client.post(
        f"/api/v1/guidance-claims/{claim['id']}/review",
        json={"decision": "approved", "note": "Source-faithful Intel claim."},
    )
    assert reviewed.status_code == 200

    body = client.get("/api/v1/canonical-settings/turbo-power-limit/guidance").json()
    assert "Intel Corporation — Intel Processor Tuning Guide" in body["scope_notice"]
    assert "AMD" not in body["scope_notice"]
    assert "Ryzen" not in body["scope_notice"]


def test_html_citation_first_guidance(client, pdf_bytes_factory):
    _seed_guidance(client, pdf_bytes_factory)
    landing = client.get("/guidance")
    assert landing.status_code == 200
    assert "Citation-First Guidance" in landing.text
    assert "Precision Boost Overdrive" in landing.text

    page = client.get("/guidance", params={"setting": "Precision Boost Overdrive"})
    assert page.status_code == 200
    assert "Behavior" in page.text
    assert "Official source" in page.text
    assert "will not invent values" in page.text
    assert "source_scope" in page.text

    unknown = client.get("/guidance", params={"setting": "best PBO settings"})
    assert unknown.status_code == 404
    assert "No exact canonical setting" in unknown.text


def test_am5_search_handoff_preserves_context_and_returns_cited_guidance(
    client,
    pdf_bytes_factory,
):
    source_id, _, _ = _seed_guidance(client, pdf_bytes_factory)
    setting_id = _seed_mapped_setting(
        client,
        pdf_bytes_factory,
        vendor="ASRock",
        model="X870E Taichi White",
        name="Precision Boost Overdrive",
        filename="asrock-am5-product-journey.pdf",
        source_url="https://www.asrock.com/mb/AMD/X870E%20Taichi%20White/index.asp#Manual",
        page_number=22,
        canonical_id="precision-boost-overdrive",
    )
    context = {
        "model_id": "asrock-x870e-taichi-white",
        "cpu_model_id": "amd-ryzen-7-9800x3d",
    }

    search = client.get(
        "/search",
        params={"q": "Precision Boost Overdrive", **context},
    )

    assert search.status_code == 200
    assert f'href="/settings/{setting_id}"' in search.text
    assert (
        'href="/guidance?setting=precision-boost-overdrive&amp;'
        "motherboard_model_id=asrock-x870e-taichi-white&amp;"
        'cpu_model_id=amd-ryzen-7-9800x3d"'
    ) in search.text

    guidance = client.get(
        "/guidance",
        params={
            "setting": "precision-boost-overdrive",
            "motherboard_model_id": context["model_id"],
            "cpu_model_id": context["cpu_model_id"],
        },
    )

    assert guidance.status_code == 200
    assert "ASRock X870E Taichi White" in guidance.text
    assert "PBO may operate up to motherboard power and current limits." in guidance.text
    assert "Ryzen Master User Guide, page 1" in guidance.text
    assert f'href="/evidence-sources/{source_id}"' in guidance.text
    assert "Current accepted guidance remains source-scoped" in guidance.text


def test_guidance_resolves_mapped_vendor_name_and_exposes_cited_equivalents(
    client,
    pdf_bytes_factory,
):
    _seed_guidance(client, pdf_bytes_factory)
    asus_setting_id, msi_setting_id = _seed_vendor_equivalences(client, pdf_bytes_factory)

    response = client.get(
        "/api/v1/guidance/explain",
        params={"setting": "CPU-Boost Scalar"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["canonical_id"] == "precision-boost-overdrive"
    assert body["resolved_by"] == "mapped_vendor_name"
    assert body["matched_alias"] == "CPU Boost Scalar"
    assert [item["vendor_name"] for item in body["equivalent_vendor_names"]] == [
        "ASUS",
        "MSI",
    ]
    assert [item["setting_name"] for item in body["equivalent_vendor_names"]] == [
        "PBO Scalar",
        "CPU Boost Scalar",
    ]
    assert [item["citations"][0]["setting_id"] for item in body["equivalent_vendor_names"]] == [
        asus_setting_id,
        msi_setting_id,
    ]
    assert all(item["citations"][0]["source_url"] for item in body["equivalent_vendor_names"])

    page = client.get("/guidance", params={"setting": "CPU Boost Scalar"})
    assert page.status_code == 200
    assert "Equivalent Vendor Names" in page.text
    assert "ASUS" in page.text
    assert "MSI" in page.text
    assert f'href="/settings/{asus_setting_id}"' in page.text
    assert f'href="/settings/{msi_setting_id}"' in page.text
    assert "Open detailed canonical mapping" in page.text


def test_guidance_refuses_ambiguous_mapped_vendor_name(client, pdf_bytes_factory):
    _seed_guidance(client, pdf_bytes_factory)
    _seed_vendor_equivalences(client, pdf_bytes_factory)
    created = client.post(
        "/api/v1/canonical-settings",
        json={"name": "Alternate Boost Scalar"},
    )
    assert created.status_code == 200
    _seed_mapped_setting(
        client,
        pdf_bytes_factory,
        vendor="GIGABYTE",
        model="AORUS X870E",
        name="CPU Boost Scalar",
        filename="gigabyte-ambiguous-scalar.pdf",
        source_url="https://www.gigabyte.com/Motherboard/X870E-AORUS/support#support-manual",
        page_number=31,
        canonical_id="alternate-boost-scalar",
    )

    response = client.get(
        "/api/v1/guidance/explain",
        params={"setting": "CPU Boost Scalar"},
    )
    assert response.status_code == 400
    assert "Ambiguous vendor setting name" in response.json()["detail"]


def test_guidance_carries_system_identity_without_inventing_exact_applicability(
    client,
    pdf_bytes_factory,
):
    _seed_guidance(client, pdf_bytes_factory)
    params = {
        "setting": "Precision Boost Overdrive",
        "motherboard_model_id": "asrock-x870e-taichi-white",
        "cpu_model_id": "amd-ryzen-7-9800x3d",
    }

    response = client.get("/api/v1/guidance/explain", params=params)
    assert response.status_code == 200
    body = response.json()
    assert body["system_context"]["motherboard_model_name"] == "X870E Taichi White"
    assert body["system_context"]["bios_version_status"] == "not_provided"
    assert "Current accepted guidance remains source-scoped" in body["context_notice"]
    assert "not validated for this exact motherboard" in body["context_notice"]

    page = client.get("/guidance", params=params)
    assert page.status_code == 200
    assert "System context:" in page.text
    assert "ASRock X870E Taichi White" in page.text
    assert "Current accepted guidance remains source-scoped" in page.text

    refused = client.get(
        "/api/v1/guidance/explain",
        params={
            "setting": "Precision Boost Overdrive",
            "motherboard_model_id": "msi-mpg-z690-carbon-wifi",
            "cpu_model_id": "amd-ryzen-7-9800x3d",
        },
    )
    assert refused.status_code == 400
    assert "different platforms" in refused.json()["detail"]
