"""API v1 (read-only foundation): JSON parity with the HTML pages.

Every endpoint calls the same domain functions the web layer uses; these
tests exercise the JSON shapes, 404 behavior, and the OpenAPI schema.
"""

from app.db.models import ExtractionRun, Setting
from app.db.session import SessionLocal
from app.domain import review

PROSE_PAGE = "Plain lowercase reference prose for extraction threshold padding. " * 4


def _upload_manual(client, pdf_bytes, vendor="ASUS", model_name="X670"):
    response = client.post(
        "/manuals",
        files={"file": ("manual.pdf", pdf_bytes, "application/pdf")},
        data={
            "vendor": vendor,
            "model_name": model_name,
            "eligibility_workflow": "legacy",
        },
        follow_redirects=False,
    )
    return int(response.headers["location"].rsplit("/", 1)[1])


def _seed_setting(manual_id, name="Ai Overclock Tuner", page_number=3):
    with SessionLocal() as session:
        run = session.query(ExtractionRun).filter_by(manual_id=manual_id).one()
        setting = Setting(
            manual_id=manual_id,
            extraction_run_id=run.id,
            extracted_name=name,
            extracted_description="Selects the memory profile.",
            page_number=page_number,
            status="draft",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
        session.add(setting)
        session.commit()
        return setting.id


def test_list_manuals_empty(client):
    response = client.get("/api/v1/manuals")
    assert response.status_code == 200
    assert response.json() == []


def test_list_manuals_carries_run_status_and_counts(client, pdf_bytes_factory):
    manual_id = _upload_manual(client, pdf_bytes_factory(texts=[PROSE_PAGE]))
    _seed_setting(manual_id)

    (row,) = client.get("/api/v1/manuals").json()
    assert row["id"] == manual_id
    assert row["vendor"] == "ASUS"
    assert row["vendor_id"] == "asus"
    assert row["applicability"]["scope"] == "chipset"
    assert row["applicability"]["target_ids"] == ["amd-x670"]
    assert row["run_status"] == "succeeded"
    assert (row["draft_count"], row["approved_count"], row["rejected_count"]) == (1, 0, 0)


def test_manual_detail_includes_the_extraction_run(client, pdf_bytes_factory):
    manual_id = _upload_manual(client, pdf_bytes_factory(texts=[PROSE_PAGE]))
    body = client.get(f"/api/v1/manuals/{manual_id}").json()
    assert body["original_filename"] == "manual.pdf"
    assert body["extraction_run"]["status"] == "succeeded"
    assert body["extraction_run"]["parser_version"]
    assert body["applicability"]["scope"] == "chipset"
    assert body["applicability"]["targets"][0]["canonical_name"] == "AMD X670"


def test_manual_404_is_json(client):
    response = client.get("/api/v1/manuals/12345")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"detail": "Manual not found."}


def test_review_queue_lists_and_filters_drafts(client, pdf_bytes_factory):
    first = _upload_manual(client, pdf_bytes_factory(texts=[PROSE_PAGE]))
    second = _upload_manual(client, pdf_bytes_factory(texts=[PROSE_PAGE] * 2), vendor="MSI")
    _seed_setting(first, "First Setting", 9)
    _seed_setting(first, "Earlier Setting", 4)
    _seed_setting(second, "Second Manual Setting", 1)

    names = [row["effective_name"] for row in client.get("/api/v1/review-queue").json()]
    assert names == ["Earlier Setting", "First Setting", "Second Manual Setting"]

    filtered = client.get(f"/api/v1/review-queue?manual_id={second}").json()
    assert [row["effective_name"] for row in filtered] == ["Second Manual Setting"]
    assert filtered[0]["manual_label"] == "MSI X670"


def test_setting_detail_exposes_originals_edits_and_audit(client, pdf_bytes_factory):
    manual_id = _upload_manual(client, pdf_bytes_factory(texts=[PROSE_PAGE]))
    setting_id = _seed_setting(manual_id)
    with SessionLocal() as session:
        setting = session.get(Setting, setting_id)
        review.edit_draft_setting(session, setting, name="Renamed", description="Cleaned.")

    body = client.get(f"/api/v1/settings/{setting_id}").json()
    assert body["extracted_name"] == "Ai Overclock Tuner"
    assert body["edited_name"] == "Renamed"
    assert body["effective_name"] == "Renamed"
    assert body["status"] == "draft"
    (event,) = body["events"]
    assert event["action"] == "edited"
    assert event["actor"] == review.ACTOR


def test_setting_404_is_json(client):
    response = client.get("/api/v1/settings/12345")
    assert response.status_code == 404
    assert response.json() == {"detail": "Setting not found."}


def test_search_returns_approved_results_only(client, pdf_bytes_factory):
    manual_id = _upload_manual(client, pdf_bytes_factory(texts=[PROSE_PAGE]))
    setting_id = _seed_setting(manual_id)
    draft_id = _seed_setting(manual_id, "Draft Only Voltage Setting", 5)
    client.post(f"/settings/{setting_id}/approve", data={"note": ""})

    (result,) = client.get("/api/v1/search?q=memory").json()
    assert result["setting_id"] == setting_id
    assert result["effective_name"] == "Ai Overclock Tuner"
    assert result["manual_label"] == "ASUS X670"
    assert result["vendor_id"] == "asus"
    assert result["manual_id"] == manual_id
    assert result["applicability_scope"] == "chipset"
    assert result["applicability_target_ids"] == ["amd-x670"]
    assert result["page_number"] == 3
    assert "<mark>memory</mark>" in result["snippet"]

    assert client.get("/api/v1/search?q=Voltage").json() == []  # draft excluded
    assert draft_id  # documents why the draft exists


def test_api_search_filters_by_stable_vendor_id(client, pdf_bytes_factory):
    asus_manual = _upload_manual(client, pdf_bytes_factory(texts=[PROSE_PAGE]))
    msi_manual = _upload_manual(
        client,
        pdf_bytes_factory(texts=[f"{PROSE_PAGE} MSI"]),
        vendor="Micro-Star International",
    )
    asus_setting = _seed_setting(asus_manual)
    msi_setting = _seed_setting(msi_manual, "MSI Memory Setting")
    client.post(f"/settings/{asus_setting}/approve", data={"note": ""})
    client.post(f"/settings/{msi_setting}/approve", data={"note": ""})

    results = client.get(
        "/api/v1/search",
        params={"q": "memory", "vendor_id": "msi"},
    ).json()
    assert [result["setting_id"] for result in results] == [msi_setting]
    assert results[0]["vendor_id"] == "msi"


def test_search_handles_blank_and_hostile_queries(client):
    assert client.get("/api/v1/search").json() == []
    for hostile in ('"', "name:x OR (", "NEAR("):
        response = client.get("/api/v1/search", params={"q": hostile})
        assert response.status_code == 200
        assert response.json() == []


def test_api_validation_errors_are_json(client):
    response = client.get("/api/v1/manuals/not-a-number")
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/json")
    assert isinstance(response.json()["detail"], list)


def test_openapi_documents_the_api_and_hides_the_html_pages(client):
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    assert "/api/v1/manuals" in paths
    assert "/api/v1/manuals/{manual_id}" in paths
    assert "/api/v1/review-queue" in paths
    assert "/api/v1/settings/{setting_id}" in paths
    assert "/api/v1/search" in paths
    assert "/api/v1/hardware" in paths
    assert "/api/v1/hardware/am5-coverage" in paths
    assert "/api/v1/hardware/am5-question-acceptance" in paths
    assert "/api/v1/hardware/system-context" in paths
    assert "/api/v1/tuning-contract" in paths
    # HTML pages are not part of the documented API surface.
    assert "/manuals" not in paths
    assert "/review" not in paths
    assert client.get("/docs").status_code == 200


def test_web_interface_still_serves_html(client):
    response = client.get("/manuals")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


def test_hardware_contract_exposes_stable_filter_ids(client):
    body = client.get("/api/v1/hardware").json()
    assert body["cpus"]["models"][0]["cpu_model_id"] == "amd-ryzen-7-9800x3d"
    assert body["version"] == "1.0"
    assert any(item["platform_id"] == "amd-am5" for item in body["platforms"])
    assert any(item["chipset_id"] == "amd-x870e" for item in body["chipsets"])
    assert any(
        item["model_id"] == "asrock-x870e-taichi-white" for item in body["motherboard_models"]
    )


def test_api_resolves_am5_system_context_without_claiming_bios_coverage(client):
    response = client.get(
        "/api/v1/hardware/system-context",
        params={
            "motherboard_model_id": "asrock-x870e-taichi-white",
            "cpu_model_id": "amd-ryzen-7-9800x3d",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["platform_id"] == "amd-am5"
    assert body["motherboard_model_name"] == "X870E Taichi White"
    assert body["cpu_model_name"] == "AMD Ryzen 7 9800X3D"
    assert body["bios_version_status"] == "not_provided"
    assert body["coverage_status"] == "identity_resolved"
    assert "will not infer it" in body["warnings"][0]

    refused = client.get(
        "/api/v1/hardware/system-context",
        params={
            "motherboard_model_id": "msi-mpg-z690-carbon-wifi",
            "cpu_model_id": "amd-ryzen-7-9800x3d",
        },
    )
    assert refused.status_code == 400
    assert "different platforms" in refused.json()["detail"]


def test_api_exposes_read_only_am5_coverage_report(client):
    response = client.get("/api/v1/hardware/am5-coverage")
    assert response.status_code == 200
    body = response.json()
    assert body["report_version"] == "1.0"
    assert body["platform_id"] == "amd-am5"
    assert body["summary"]["major_vendor_count"] == 4
    assert body["summary"]["selectable_model_count"] == 4
    assert body["summary"]["expansion_status"] == "hold"
    assert len(body["vendors"]) == 4
    assert {row["vendor_id"] for row in body["vendors"]} == {
        "asus",
        "msi",
        "gigabyte",
        "asrock",
    }
    assert "not automatically an AM5 gap" in body["scope_notice"]


def test_api_exposes_fixed_am5_question_acceptance_contract(client):
    response = client.get("/api/v1/hardware/am5-question-acceptance")

    assert response.status_code == 200
    body = response.json()
    assert body["report_version"] == "1.0"
    assert body["platform_id"] == "amd-am5"
    assert body["summary"] == {
        "total_question_count": 12,
        "expected_supported_count": 9,
        "expected_refusal_count": 3,
        "actual_supported_count": 0,
        "actual_refusal_count": 12,
        "passing_question_count": 3,
        "unexpected_gap_count": 9,
        "acceptance_status": "hold",
    }
    scalar = next(row for row in body["questions"] if row["question_id"] == "pbo-scalar-behavior")
    assert scalar["expected_outcome"] == "supported"
    assert scalar["actual_outcome"] == "refused"
    recommendation = next(
        row for row in body["questions"] if row["question_id"] == "pbo-scalar-recommendation"
    )
    assert recommendation["passed"] is True
    assert recommendation["actual_outcome"] == "refused"


def test_tuning_contract_exposes_pilot_value_vocabulary(client):
    body = client.get("/api/v1/tuning-contract").json()
    assert body["version"] == "1.0"
    assert "signed_offset" in body["value_kinds"]
    assert "celsius" in body["units"]
