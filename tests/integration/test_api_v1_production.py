"""Production API behaviors: pagination, error consistency, health, OpenAPI.

Backward compatibility is the anchor: without pagination parameters every
list endpoint returns the same bare array it always did.
"""

import tomllib

from fastapi.testclient import TestClient

from app import config
from app.db.models import ExtractionRun, Setting
from app.db.session import SessionLocal
from app.domain import manuals as manuals_domain
from app.extraction.settings_parser import PARSER_VERSION
from app.main import create_app
from app.observability import metrics

PROSE_PAGE = "Plain lowercase reference prose for extraction threshold padding. " * 4


def _upload(client, pdf_bytes, vendor="ASUS"):
    response = client.post(
        "/manuals",
        files={"file": ("manual.pdf", pdf_bytes, "application/pdf")},
        data={"vendor": vendor, "model_name": "", "eligibility_workflow": "legacy"},
        follow_redirects=False,
    )
    return int(response.headers["location"].rsplit("/", 1)[1])


def _seed_setting(manual_id, name, page_number, status="draft"):
    with SessionLocal() as session:
        run = session.query(ExtractionRun).filter_by(manual_id=manual_id).one()
        setting = Setting(
            manual_id=manual_id,
            extraction_run_id=run.id,
            extracted_name=name,
            extracted_description="Shared keyword: overclocking.",
            page_number=page_number,
            status=status,
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
        session.add(setting)
        session.commit()
        return setting.id


def _three_manuals(client, pdf_bytes_factory):
    return [_upload(client, pdf_bytes_factory(texts=[PROSE_PAGE] * pages)) for pages in (1, 2, 3)]


def test_health_reports_versions(client):
    response = client.get("/api/v1/health")
    body = response.json()
    assert body == {
        "status": "ok",
        "app_version": config.APP_VERSION,
        "parser_version": PARSER_VERSION,
    }
    assert len(response.headers["x-request-id"]) == 32


def test_request_id_is_echoed_only_when_well_formed(client):
    accepted = client.get("/api/v1/health", headers={"X-Request-ID": "request-1234"})
    replaced = client.get("/api/v1/health", headers={"X-Request-ID": "short"})

    assert accepted.headers["x-request-id"] == "request-1234"
    assert replaced.headers["x-request-id"] != "short"
    assert len(replaced.headers["x-request-id"]) == 32


def test_operational_metrics_use_route_templates_and_require_no_user_content(client):
    metrics.reset()
    client.get("/library/canonical/not-a-real-identity")
    response = client.get("/api/v1/operations/metrics")

    assert response.status_code == 200
    body = response.json()
    labels = [row["labels"] for row in body["counters"] if row["name"] == "http_requests_total"]
    expected = {
        "method": "GET",
        "route": "/library/canonical/{canonical_id}",
        "status_class": "4xx",
    }
    assert expected in labels
    assert "not-a-real-identity" not in response.text


def test_runtime_and_package_versions_cannot_drift():
    package = tomllib.loads((config.COMPONENT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert config.APP_VERSION == package["project"]["version"]


def test_manuals_pagination_and_total_header(client, pdf_bytes_factory):
    ids = _three_manuals(client, pdf_bytes_factory)

    # Backward compatible: no parameters -> the full bare array.
    full = client.get("/api/v1/manuals")
    assert [row["id"] for row in full.json()] == sorted(ids, reverse=True)
    assert full.headers["X-Total-Count"] == "3"

    page = client.get("/api/v1/manuals?limit=2")
    assert len(page.json()) == 2
    assert page.headers["X-Total-Count"] == "3"

    rest = client.get("/api/v1/manuals?limit=2&offset=2")
    assert [row["id"] for row in rest.json()] == [sorted(ids, reverse=True)[2]]

    beyond = client.get("/api/v1/manuals?offset=99")
    assert beyond.json() == []
    assert beyond.headers["X-Total-Count"] == "3"


def test_review_queue_pagination(client, pdf_bytes_factory):
    manual_id = _upload(client, pdf_bytes_factory(texts=[PROSE_PAGE]))
    for page_number in (1, 2, 3):
        _seed_setting(manual_id, f"Setting {page_number}", page_number)

    page = client.get("/api/v1/review-queue?limit=1&offset=1")
    assert [row["effective_name"] for row in page.json()] == ["Setting 2"]
    assert page.headers["X-Total-Count"] == "3"


def test_search_pagination(client, pdf_bytes_factory):
    manual_id = _upload(client, pdf_bytes_factory(texts=[PROSE_PAGE]))
    for page_number in (1, 2, 3):
        setting_id = _seed_setting(manual_id, f"Search Target {page_number}", page_number)
        client.post(f"/api/v1/settings/{setting_id}/approve", json={})

    response = client.get("/api/v1/search?q=overclocking&limit=2")
    assert len(response.json()) == 2
    assert response.headers["X-Total-Count"] == "3"


def test_pagination_bounds_are_validated_as_json(client):
    for params in ("limit=0", "limit=501", "offset=-1", "limit=abc"):
        response = client.get(f"/api/v1/manuals?{params}")
        assert response.status_code == 422
        assert response.headers["content-type"].startswith("application/json")
        assert isinstance(response.json()["detail"], list)


def test_unexpected_errors_return_safe_json_on_api_paths(client, monkeypatch):
    def boom(_session):
        raise RuntimeError("secret internals")

    monkeypatch.setattr(manuals_domain, "list_manuals_overview", boom)
    raw_client = TestClient(create_app(), raise_server_exceptions=False)

    response = raw_client.get("/api/v1/manuals")
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error."}
    assert "secret internals" not in response.text
    assert len(response.headers["x-request-id"]) == 32


def test_unexpected_errors_render_the_html_error_page_on_web_paths(monkeypatch):
    def boom(_session):
        raise RuntimeError("secret internals")

    monkeypatch.setattr(manuals_domain, "list_manuals_overview", boom)
    raw_client = TestClient(create_app(), raise_server_exceptions=False)

    response = raw_client.get("/manuals")
    assert response.status_code == 500
    assert "Something went wrong." in response.text
    assert "secret internals" not in response.text


def test_openapi_documents_pagination_errors_and_health(client):
    schema = client.get("/openapi.json").json()

    assert schema["info"]["version"] == config.APP_VERSION
    assert {tag["name"] for tag in schema["tags"]} == {
        "manuals",
        "batches",
        "settings",
        "search",
        "hardware",
        "canonical settings",
        "evidence sources",
            "guidance claims",
            "recommendation claims",
            "citation-first guidance",
        "refusal gaps",
        "interface contract",
        "public library",
        "meta",
    }
    assert "/api/v1/health" in schema["paths"]

    manuals_get = schema["paths"]["/api/v1/manuals"]["get"]
    param_names = {param["name"] for param in manuals_get["parameters"]}
    assert {"limit", "offset"} <= param_names
    assert "X-Total-Count" in manuals_get["responses"]["200"]["headers"]

    detail_get = schema["paths"]["/api/v1/manuals/{manual_id}"]["get"]
    assert "404" in detail_get["responses"]
    assert "ErrorOut" in str(detail_get["responses"]["404"])

    upload_post = schema["paths"]["/api/v1/manuals"]["post"]
    assert "DuplicateUploadErrorOut" in str(upload_post["responses"]["409"])
