from fastapi.testclient import TestClient

from app.main import create_app


def test_root_makes_the_bios_settings_library_the_public_homepage():
    client = TestClient(create_app())
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/library"

    library = client.get("/library")
    assert library.status_code == 200
    assert "BIOS Settings Library" in library.text
    assert ">BIOS Settings</a>" in library.text
    assert ">Compare Vendors</a>" in library.text
    assert ">Ask TuneForge</a>" in library.text
    assert ">Upload Manual</a>" in library.text
    assert "Guided answers" not in library.text
    assert "My system" not in library.text


def test_untrusted_host_is_rejected_before_application_routing(client):
    response = client.get("/", headers={"Host": "attacker.invalid"})

    assert response.status_code == 400
    assert response.text == "Invalid host header"


def test_public_readiness_reports_head_schema_and_storage(client):
    response = client.get("/api/v1/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "app_version": "1.33.0",
        "database_revision": "0027",
        "authentication_initialized": False,
        "checks": {
            "database": True,
            "schema": True,
            "uploads_storage": True,
            "evidence_storage": True,
        },
    }


def test_manuals_page_shows_empty_state():
    client = TestClient(create_app())
    response = client.get("/manuals")
    assert response.status_code == 200
    assert "No manuals yet" in response.text


def test_admin_dashboard_is_a_clear_read_only_operations_home(client):
    response = client.get("/admin")
    assert response.status_code == 200
    assert "Knowledge operations" in response.text
    assert "Work queue" in response.text
    assert "AM5 validation" in response.text
    assert "Recent activity" in response.text
    assert "Local development bypass" in response.text
    assert "Launch readiness" in response.text
    assert 'href="/admin/release-readiness"' in response.text
    assert 'href="/manuals/upload"' in response.text
    assert 'href="/evidence-sources/upload"' in response.text
    assert 'href="/">Open public site' in response.text


def test_release_readiness_page_and_api_explain_local_launch_holds(client):
    page = client.get("/admin/release-readiness")
    api = client.get("/api/v1/release-readiness")

    assert page.status_code == 200
    assert "Focused AM5 v1" in page.text
    assert "Product checks" in page.text
    assert "Deployment checks" in page.text
    assert "Provider-free public answers" in page.text
    assert "no paid LLM provider is required" in page.text
    assert 'aria-current="page"' in page.text
    assert api.status_code == 200
    assert api.json()["product_status"] == "hold"
    assert api.json()["deployment_status"] == "hold"
    assert {check["check_id"] for check in api.json()["checks"]} == {
        "am5-coverage",
        "am5-questions",
        "extraction-pipeline",
        "provider-free-core",
        "public-library",
        "infrastructure",
        "administrator",
        "production-configuration",
        "recovery-checkpoint",
    }


def test_admin_shell_links_to_every_curator_workspace(client):
    # The shared shell keeps every protected curator workspace discoverable.
    response = client.get("/manuals")
    assert 'aria-current="page"' in response.text
    assert 'href="/manuals"' in response.text
    assert 'href="/manuals/upload"' in response.text
    assert 'href="/review"' in response.text
    assert 'href="/search"' in response.text
    assert 'href="/am5-coverage"' in response.text
    assert 'href="/am5-question-acceptance"' in response.text
    assert 'href="/canonical-settings"' in response.text
    assert 'href="/evidence-sources"' in response.text
    assert 'href="/guidance-claims"' in response.text
    assert 'href="/recommendation-claims"' in response.text
    assert 'href="/refusal-gaps"' in response.text
    assert 'href="/admin/release-readiness"' in response.text
    assert 'href="/">Open public site' in response.text


def test_upload_page_renders_empty_discovery_form(client):
    response = client.get("/manuals/upload")

    assert response.status_code == 200
    assert 'name="provenance_workflow" value="discover"' in response.text
    assert 'name="eligibility_workflow" value="review"' in response.text
    assert "Upload and run preflight" in response.text


def test_my_system_page_resolves_only_reviewed_am5_identifiers(client):
    landing = client.get("/system-context")
    assert landing.status_code == 200
    assert "My AM5 system" in landing.text
    assert "X870E Taichi White" in landing.text
    assert "AMD Ryzen 7 9800X3D" in landing.text
    assert "Z790 AORUS" not in landing.text

    resolved = client.get(
        "/system-context",
        params={
            "motherboard_model_id": "asrock-x870e-taichi-white",
            "cpu_model_id": "amd-ryzen-7-9800x3d",
        },
    )
    assert resolved.status_code == 200
    assert "Identity resolved" in resolved.text
    assert "ASRock X870E Taichi White" in resolved.text
    assert "BIOS version was not provided" in resolved.text
    assert "Browse guided answers" in resolved.text
    assert (
        'href="/assistant?motherboard_model_id=asrock-x870e-taichi-white&amp;'
        'cpu_model_id=amd-ryzen-7-9800x3d"'
    ) in resolved.text
    assert (
        'href="/answer?motherboard_model_id=asrock-x870e-taichi-white&amp;'
        'cpu_model_id=amd-ryzen-7-9800x3d"'
    ) in resolved.text

    versioned = client.get(
        "/system-context",
        params={
            "motherboard_model_id": "asrock-x870e-taichi-white",
            "cpu_model_id": "amd-ryzen-7-9800x3d",
            "bios_version": "3.20",
            "use_case": "gaming",
        },
    )
    assert "3.20 · provided unverified" in versioned.text
    assert "bios_version=3.20" in versioned.text
    assert "Gaming · provided" in versioned.text
    assert "use_case=gaming" in versioned.text
    assert "does not yet expose recommendation retrieval" in versioned.text

    answer = client.get(
        "/answer",
        params={
            "motherboard_model_id": "asrock-x870e-taichi-white",
            "cpu_model_id": "amd-ryzen-7-9800x3d",
            "bios_version": "3.20",
            "use_case": "gaming",
        },
    )
    assert 'name="bios_version" value="3.20"' in answer.text
    assert 'name="use_case" value="gaming"' in answer.text
    assert "AMD Ryzen 7 9800X3D · Gaming · Identity context only" in answer.text


def test_public_answer_landing_is_clear_and_has_reviewed_examples(client):
    response = client.get("/answer")
    assert response.status_code == 200
    assert "Ask TuneForge" in response.text
    assert "Ask a BIOS settings question" in response.text
    assert "Precision Boost Overdrive" in response.text
    assert 'href="/compare"' in response.text
    assert 'href="/upload-manual"' in response.text

    partial = client.get(
        "/system-context",
        params={"motherboard_model_id": "asrock-x870e-taichi-white"},
    )
    assert partial.status_code == 400
    assert "Choose both a motherboard and CPU model" in partial.text


def test_am5_coverage_page_renders_factual_empty_state(client):
    response = client.get("/am5-coverage")
    assert response.status_code == 200
    assert "AM5 Coverage Validation" in response.text
    assert "Expansion Gates" in response.text
    assert "All four major vendors have AM5 manual coverage" in response.text
    assert "blocked" in response.text
    assert "unobserved canonical setting is not automatically an AM5 gap" in response.text


def test_am5_question_acceptance_page_distinguishes_gaps_from_expected_refusals(client):
    response = client.get("/am5-question-acceptance")

    assert response.status_code == 200
    assert "AM5 Product-Question Acceptance" in response.text
    assert "Passing questions" in response.text
    assert "3 / 12" in response.text
    assert "What does PBO Scalar do?" in response.text
    assert "No approved AM5 setting is mapped to this canonical identity." in response.text
    assert "What PBO Scalar value should I use?" in response.text
    assert "A refusal passes" in response.text
