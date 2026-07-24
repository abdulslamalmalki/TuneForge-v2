"""Search page (/search): FR-24/FR-25 results, empty states, hostile input."""

from app.db.models import ExtractionRun, Manual, Setting
from app.db.session import SessionLocal
from app.domain import canonical_settings, hardware

PROSE_PAGE = "Plain lowercase reference prose for extraction threshold padding. " * 4


def _seed(
    client,
    pdf_bytes_factory,
    description="Selects the memory overclocking profile.",
    vendor="ASUS",
    filename="manual.pdf",
    model_name="X670",
    chipset="",
    name="Ai Overclock Tuner",
    document_title="",
):
    response = client.post(
        "/manuals",
        files={
            "file": (
                filename,
                pdf_bytes_factory(texts=[f"{PROSE_PAGE} {filename}"]),
                "application/pdf",
            )
        },
        data={
            "vendor": vendor,
            "model_name": model_name,
            "document_title": document_title,
            "chipset": chipset,
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
            extracted_name=name,
            extracted_description=description,
            page_number=3,
            status="draft",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
        session.add(setting)
        session.commit()
        return setting.id


def test_no_query_shows_search_prompt(client):
    page = client.get("/search").text
    assert "Search approved settings." in page


def test_no_results_shows_approval_hint(client):
    page = client.get("/search?q=nonexistent").text
    assert "No approved settings match" in page
    assert "must be approved" in page


def test_results_show_all_fr25_fields(client, pdf_bytes_factory):
    setting_id = _seed(client, pdf_bytes_factory)
    client.post(f"/settings/{setting_id}/approve", data={"note": ""})

    page = client.get("/search?q=memory").text
    assert "Ai Overclock Tuner" in page
    assert f'href="/settings/{setting_id}"' in page
    assert "ASUS X670" in page
    assert ">3<" in page  # page number cell
    assert "<mark>memory</mark>" in page
    assert "No reviewed canonical mapping" in page
    assert "guidance is unavailable until a reviewer maps this source setting" in page


def test_drafts_never_appear_in_results(client, pdf_bytes_factory):
    _seed(client, pdf_bytes_factory)  # left as draft
    page = client.get("/search?q=memory").text
    assert "No approved settings match" in page


def test_reopening_removes_from_results_immediately(client, pdf_bytes_factory):
    setting_id = _seed(client, pdf_bytes_factory)
    client.post(f"/settings/{setting_id}/approve", data={"note": ""})
    assert "Ai Overclock Tuner" in client.get("/search?q=memory").text

    client.post(f"/settings/{setting_id}/reopen")
    assert "No approved settings match" in client.get("/search?q=memory").text


def test_hostile_query_strings_return_safely(client, pdf_bytes_factory):
    setting_id = _seed(client, pdf_bytes_factory)
    client.post(f"/settings/{setting_id}/approve", data={"note": ""})

    for hostile in ('"', "name:x OR (", "NEAR(", '" OR 1=1 --'):
        response = client.get("/search", params={"q": hostile})
        assert response.status_code == 200

    # A hostile-looking but matching term still finds the setting literally.
    response = client.get("/search", params={"q": 'memory"'})
    assert "Ai Overclock Tuner" in response.text


def test_query_box_retains_the_search_terms(client):
    page = client.get("/search?q=voltage").text
    assert 'value="voltage"' in page


def test_search_can_filter_by_stable_vendor_id(client, pdf_bytes_factory):
    asus_setting = _seed(client, pdf_bytes_factory)
    msi_setting = _seed(
        client,
        pdf_bytes_factory,
        vendor="Micro-Star International",
        filename="msi-manual.pdf",
    )
    client.post(f"/settings/{asus_setting}/approve", data={"note": ""})
    client.post(f"/settings/{msi_setting}/approve", data={"note": ""})

    page = client.get("/search", params={"q": "memory", "vendor_id": "msi"}).text
    assert f'href="/settings/{msi_setting}"' in page
    assert f'href="/settings/{asus_setting}"' not in page
    assert 'value="msi" selected' in page


def test_search_can_filter_by_normalized_platform(client, pdf_bytes_factory):
    amd_setting = _seed(
        client,
        pdf_bytes_factory,
        filename="amd800.pdf",
        model_name="AMD 800 Series",
        chipset="AMD 800 Series",
    )
    intel_setting = _seed(
        client,
        pdf_bytes_factory,
        filename="intel800.pdf",
        model_name="Intel 800 Series",
        chipset="Intel 800 Series",
    )
    client.post(f"/settings/{amd_setting}/approve", data={"note": ""})
    client.post(f"/settings/{intel_setting}/approve", data={"note": ""})

    page = client.get(
        "/search",
        params={"q": "memory", "platform_id": "amd-am5"},
    ).text
    assert f'href="/settings/{amd_setting}"' in page
    assert f'href="/settings/{intel_setting}"' not in page
    assert 'value="amd-am5" selected' in page
    assert "chipset family" in page
    assert "<code>amd-800-series</code>" in page


def test_model_filter_includes_broad_documents_compatible_with_the_model(client, pdf_bytes_factory):
    broad_setting = _seed(
        client,
        pdf_bytes_factory,
        filename="amd800-family.pdf",
        model_name="AMD 800 Series",
        chipset="AMD 800 Series",
    )
    other_setting = _seed(
        client,
        pdf_bytes_factory,
        filename="intel700-family.pdf",
        model_name="Intel 700 Series",
        chipset="Intel 700 Series",
    )
    client.post(f"/settings/{broad_setting}/approve", data={"note": ""})
    client.post(f"/settings/{other_setting}/approve", data={"note": ""})

    page = client.get(
        "/search",
        params={
            "q": "memory",
            "model_id": "asrock-x870e-taichi-white",
        },
    ).text
    assert f'href="/settings/{broad_setting}"' in page
    assert f'href="/settings/{other_setting}"' not in page


def test_search_page_and_api_expose_cited_vendor_equivalence(client, pdf_bytes_factory):
    asus_setting = _seed(
        client,
        pdf_bytes_factory,
        vendor="ASUS",
        filename="asus-scalar.pdf",
        model_name="ROG X870E",
        name="PBO Scalar",
    )
    msi_setting = _seed(
        client,
        pdf_bytes_factory,
        vendor="MSI",
        filename="msi-scalar.pdf",
        model_name="MEG X870E",
        name="CPU Boost Scalar",
    )
    for setting_id in (asus_setting, msi_setting):
        client.post(f"/settings/{setting_id}/approve", data={"note": ""})
    with SessionLocal() as session:
        identity = canonical_settings.create_identity(session, name="PBO Scalar")
        for setting_id in (asus_setting, msi_setting):
            canonical_settings.map_setting(
                session,
                session.get(Setting, setting_id),
                canonical_id=identity.canonical_id,
                note="Vendor-equivalent source setting reviewed.",
            )

    page = client.get("/search", params={"q": "CPU Boost Scalar"})
    assert page.status_code == 200
    assert "Equivalent Vendor Names" in page.text
    assert "PBO Scalar" in page.text
    assert "CPU Boost Scalar" in page.text
    assert f'href="/settings/{asus_setting}"' in page.text
    assert f'href="/settings/{msi_setting}"' in page.text
    assert 'href="/guidance?setting=pbo-scalar"' in page.text

    body = client.get("/api/v1/search", params={"q": "CPU Boost Scalar"}).json()
    assert len(body) == 1
    assert body[0]["canonical_id"] == "pbo-scalar"
    assert [item["vendor_name"] for item in body[0]["equivalent_vendor_names"]] == [
        "ASUS",
        "MSI",
    ]
    assert [item["citations"][0]["setting_id"] for item in body[0]["equivalent_vendor_names"]] == [
        asus_setting,
        msi_setting,
    ]


def test_search_explains_exact_system_applicability(client, pdf_bytes_factory):
    setting_id = _seed(
        client,
        pdf_bytes_factory,
        vendor="ASRock",
        filename="x870e-taichi-context.pdf",
        model_name="X870E Taichi White",
        chipset="X870E",
        name="PBO Scalar",
        document_title="X870E Taichi White Motherboard User Guide",
    )
    client.post(f"/settings/{setting_id}/approve", data={"note": ""})
    other_vendor_setting_id = _seed(
        client,
        pdf_bytes_factory,
        vendor="MSI",
        filename="msi-am5-platform-context.pdf",
        model_name="AMD AM5 series",
        chipset="AMD AM5 series",
        name="PBO Scalar",
    )
    client.post(f"/settings/{other_vendor_setting_id}/approve", data={"note": ""})
    with SessionLocal() as session:
        identity = canonical_settings.create_identity(session, name="PBO Scalar")
        canonical_settings.map_setting(
            session,
            session.get(Setting, setting_id),
            canonical_id=identity.canonical_id,
            note="Exact-system Search to Guidance journey fixture.",
        )
    params = {
        "q": "PBO Scalar",
        "model_id": "asrock-x870e-taichi-white",
        "cpu_model_id": "amd-ryzen-7-9800x3d",
    }

    page = client.get("/search", params=params)
    assert page.status_code == 200
    assert "System context:" in page.text
    assert "ASRock X870E Taichi White" in page.text
    assert "Exact motherboard" in page.text
    assert "normalized to the selected motherboard model" in page.text
    assert f'href="/settings/{other_vendor_setting_id}"' not in page.text
    assert (
        'href="/guidance?setting=pbo-scalar&amp;'
        "motherboard_model_id=asrock-x870e-taichi-white&amp;"
        'cpu_model_id=amd-ryzen-7-9800x3d"'
    ) in page.text

    refusal = client.get(
        "/guidance",
        params={
            "setting": "pbo-scalar",
            "motherboard_model_id": "asrock-x870e-taichi-white",
            "cpu_model_id": "amd-ryzen-7-9800x3d",
        },
    )
    assert refusal.status_code == 200
    assert "ASRock X870E Taichi White" in refusal.text
    assert "no accepted guidance claims" in refusal.text
    assert "will not fill the gap from model memory" in refusal.text

    body = client.get("/api/v1/search", params=params).json()
    assert len(body) == 1
    assert body[0]["context_match"] == {
        "status": "exact_model",
        "label": "Exact motherboard",
        "reason": "The cited manual is normalized to the selected motherboard model.",
    }


def test_full_manual_classifier_value_keeps_asus_exact_model_search_scope(
    client,
    pdf_bytes_factory,
):
    setting_id = _seed(
        client,
        pdf_bytes_factory,
        vendor="ASUS",
        filename="tuf-x870-exact.pdf",
        model_name="TUF GAMING X870-PLUS WIFI",
        chipset="AMD X870",
        name="Setup Mode",
        description="Selects between EZ Mode and Advanced Mode.",
        document_title="TUF GAMING X870-PLUS WIFI User Manual",
    )
    with SessionLocal() as session:
        setting = session.get(Setting, setting_id)
        manual = session.get(Manual, setting.manual_id)
        manual.document_type = "full_manual"
        hardware.sync_manual_applicability(
            session,
            manual,
            actor="local-reviewer",
            created_at="2026-07-19T00:00:00+00:00",
        )
        session.commit()
    client.post(f"/settings/{setting_id}/approve", data={"note": "Exact model reviewed."})

    params = {
        "q": "Setup Mode",
        "model_id": "asus-tuf-gaming-x870-plus-wifi",
        "cpu_model_id": "amd-ryzen-7-9800x3d",
    }
    body = client.get("/api/v1/search", params=params).json()

    assert len(body) == 1
    assert body[0]["setting_id"] == setting_id
    assert body[0]["applicability_scope"] == "motherboard_model"
    assert body[0]["applicability_target_ids"] == ["asus-tuf-gaming-x870-plus-wifi"]
    assert body[0]["context_match"]["status"] == "exact_model"


def test_search_refuses_partial_or_cross_platform_context(client):
    missing_board = client.get(
        "/api/v1/search",
        params={"q": "PBO", "cpu_model_id": "amd-ryzen-7-9800x3d"},
    )
    assert missing_board.status_code == 400
    assert "both a motherboard and CPU" in missing_board.json()["detail"]

    cross_platform = client.get(
        "/search",
        params={
            "q": "PBO",
            "model_id": "msi-mpg-z690-carbon-wifi",
            "cpu_model_id": "amd-ryzen-7-9800x3d",
        },
    )
    assert cross_platform.status_code == 400
    assert "different platforms" in cross_platform.text

    conflicting_vendor = client.get(
        "/api/v1/search",
        params={
            "q": "PBO",
            "vendor_id": "msi",
            "model_id": "asrock-x870e-taichi-white",
            "cpu_model_id": "amd-ryzen-7-9800x3d",
        },
    )
    assert conflicting_vendor.status_code == 400
    assert "vendor filter does not match" in conflicting_vendor.json()["detail"]


def test_search_empty_state_refuses_unreviewed_or_model_memory(client):
    page = client.get("/search", params={"q": "unsupported setting"})

    assert page.status_code == 200
    assert "No approved settings match" in page.text
    assert "will not fill the gap from unreviewed sources or model memory" in page.text
