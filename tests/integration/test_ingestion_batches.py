from app.api.v1 import batches as api_batches
from app.web.routes import batches as web_batches

BIOS_TEXT = "Intel 700 series BIOS User Guide\nOC Menu\nDRAM Frequency\nSecure Boot\n" + (
    "BIOS configuration options. " * 10
)
FULL_TEXT = "Motherboard User Guide\nPackage Contents\nSafety Information\nMotherboard Layout\n" + (
    "Hardware installation instructions. " * 10
)


def _files(pdf_bytes_factory):
    bios = pdf_bytes_factory(texts=[BIOS_TEXT])
    return [
        ("files", ("Intel700BIOS.pdf", bios, "application/pdf")),
        (
            "files",
            ("MotherboardUserGuide.pdf", pdf_bytes_factory(texts=[FULL_TEXT]), "application/pdf"),
        ),
        ("files", ("broken.pdf", b"not a pdf", "application/pdf")),
        ("files", ("duplicate.pdf", bios, "application/pdf")),
    ]


def test_web_batch_isolates_failures_and_runs_preflight(client, pdf_bytes_factory, monkeypatch):
    monkeypatch.setattr(
        web_batches.provenance_discovery, "execute_discovery", lambda _manual_id: None
    )
    response = client.post("/batches", files=_files(pdf_bytes_factory), follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/batches/")

    page = client.get(response.headers["location"])
    assert page.status_code == 200
    assert "2 accepted; 2 errors" in page.text
    assert "provenance review" in page.text
    assert "full manual" in page.text
    assert "could not be read as a PDF" in page.text
    assert "Open existing manual" in page.text

    listing = client.get("/batches")
    assert "Batch 1" in listing.text
    assert "ready with errors" in listing.text


def test_api_batch_reports_each_item_and_persists_progress(client, pdf_bytes_factory, monkeypatch):
    monkeypatch.setattr(
        api_batches.provenance_discovery, "execute_discovery", lambda _manual_id: None
    )
    response = client.post("/api/v1/batches", files=_files(pdf_bytes_factory))
    assert response.status_code == 201
    body = response.json()
    assert body["batch"]["total_count"] == 4
    assert body["batch"]["accepted_count"] == 2
    assert body["batch"]["failed_count"] == 2
    assert [item["intake_status"] for item in body["items"]] == [
        "accepted",
        "accepted",
        "failed",
        "duplicate",
    ]

    batch_id = body["batch"]["id"]
    detail = client.get(f"/api/v1/batches/{batch_id}").json()
    assert [item["workflow_status"] for item in detail["items"]] == [
        "provenance_review",
        "provenance_review",
        "failed",
        "duplicate",
    ]
    listed = client.get("/api/v1/batches").json()
    assert [batch["id"] for batch in listed] == [batch_id]


def test_unknown_batch_returns_404(client):
    assert client.get("/batches/9999").status_code == 404
    response = client.get("/api/v1/batches/9999")
    assert response.status_code == 404
    assert response.json()["detail"] == "Batch not found."
