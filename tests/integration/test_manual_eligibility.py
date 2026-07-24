from sqlalchemy import select

from app.db.models import ExtractionRun, ManualEvent, Setting
from app.db.session import SessionLocal

SETTING_PAGE = (
    "Intel 700 series BIOS User Guide\n"
    "OC Menu\n"
    "Ai Overclock Tuner\n"
    "Allows selection of the memory overclocking profile for this system.\n"
    "Configuration options: [Auto] [Manual]\n" + ("BIOS configuration reference. " * 8)
)


def _api_upload(client, pdf_bytes_factory, *, full_manual=False):
    if full_manual:
        filename = "MPGX670ECARBONWIFI.pdf"
        title = "MPG X670E CARBON WIFI Motherboard User Guide"
        text = (
            "Package Contents\nSafety Information\nMotherboard Layout\n"
            "Installing the CPU\nRear I/O Panel\n" + ("Hardware instructions. " * 12)
        )
    else:
        filename = "Intel700BIOS.pdf"
        title = "Intel 700 series BIOS User Guide"
        text = SETTING_PAGE
    return client.post(
        "/api/v1/manuals",
        files={"file": (filename, pdf_bytes_factory(texts=[text]), "application/pdf")},
        data={
            "vendor": "MSI",
            "model_name": "Test Platform",
            "document_title": title,
            "source_url": "https://www.msi.com/support/download-manual/test-platform",
            "language": "en",
        },
    )


def test_bios_guide_waits_for_review_then_extracts(client, pdf_bytes_factory):
    uploaded = _api_upload(client, pdf_bytes_factory)
    assert uploaded.status_code == 201
    manual_id = uploaded.json()["id"]

    detail = client.get(f"/api/v1/manuals/{manual_id}").json()
    assert detail["document_type"] == "bios_guide"
    assert detail["eligibility_status"] == "pending"
    assert detail["eligibility_confidence"] >= 90
    assert detail["extraction_run"]["status"] == "pending"
    with SessionLocal() as session:
        assert list(session.scalars(select(Setting))) == []

    reviewed = client.post(
        f"/api/v1/manuals/{manual_id}/eligibility",
        json={"document_type": "bios_guide", "decision": "eligible"},
    )
    assert reviewed.status_code == 200
    finished = client.get(f"/api/v1/manuals/{manual_id}").json()
    assert finished["eligibility_status"] == "eligible"
    assert finished["extraction_run"]["status"] == "succeeded"
    assert finished["extraction_run"]["settings_found"] >= 1
    actions = [event["action"] for event in finished["events"]]
    assert actions == [
        "provenance_created",
        "hardware_applicability_normalized",
        "eligibility_classified",
        "eligibility_confirmed",
    ]


def test_full_manual_can_be_rejected_without_creating_drafts(client, pdf_bytes_factory):
    uploaded = _api_upload(client, pdf_bytes_factory, full_manual=True)
    manual_id = uploaded.json()["id"]
    detail = client.get(f"/api/v1/manuals/{manual_id}").json()
    assert detail["document_type"] == "full_manual"
    assert detail["eligibility_status"] == "pending"

    rejected = client.post(
        f"/api/v1/manuals/{manual_id}/eligibility",
        json={"document_type": "full_manual", "decision": "ineligible"},
    )
    assert rejected.status_code == 200
    after = client.get(f"/api/v1/manuals/{manual_id}").json()
    assert after["eligibility_status"] == "ineligible"
    assert after["extraction_run"]["status"] == "pending"
    with SessionLocal() as session:
        assert list(session.scalars(select(Setting))) == []


def test_non_bios_override_requires_explanation(client, pdf_bytes_factory):
    uploaded = _api_upload(client, pdf_bytes_factory, full_manual=True)
    manual_id = uploaded.json()["id"]
    blocked = client.post(
        f"/api/v1/manuals/{manual_id}/eligibility",
        json={"document_type": "full_manual", "decision": "eligible"},
    )
    assert blocked.status_code == 400
    assert "Explain why" in blocked.json()["detail"]


def test_web_upload_routes_complete_provenance_to_eligibility(client, pdf_bytes_factory):
    response = client.post(
        "/manuals",
        files={
            "file": ("Intel700BIOS.pdf", pdf_bytes_factory(texts=[SETTING_PAGE]), "application/pdf")
        },
        data={
            "vendor": "MSI",
            "document_title": "Intel 700 series BIOS User Guide",
            "source_url": "https://www.msi.com/support/download-manual/test-platform",
            "language": "en",
            "eligibility_workflow": "review",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].endswith("/eligibility")
    page = client.get(response.headers["location"])
    assert "Review extraction eligibility" in page.text
    assert "bios guide" in page.text


def test_eligibility_decision_is_immutable_audit_history(client, pdf_bytes_factory):
    manual_id = _api_upload(client, pdf_bytes_factory).json()["id"]
    client.post(
        f"/api/v1/manuals/{manual_id}/eligibility",
        json={"document_type": "bios_guide", "decision": "ineligible", "note": "Wrong revision."},
    )
    with SessionLocal() as session:
        run = session.scalar(select(ExtractionRun).where(ExtractionRun.manual_id == manual_id))
        assert run.status == "pending"
        actions = list(
            session.scalars(
                select(ManualEvent.action)
                .where(ManualEvent.manual_id == manual_id)
                .order_by(ManualEvent.id)
            )
        )
    assert actions[-1] == "eligibility_confirmed"


def test_eligibility_cannot_change_after_extraction(client, pdf_bytes_factory):
    manual_id = _api_upload(client, pdf_bytes_factory).json()["id"]
    client.post(
        f"/api/v1/manuals/{manual_id}/eligibility",
        json={"document_type": "bios_guide", "decision": "eligible"},
    )
    blocked = client.post(
        f"/api/v1/manuals/{manual_id}/eligibility",
        json={
            "document_type": "full_manual",
            "decision": "ineligible",
            "note": "Changed later.",
        },
    )
    assert blocked.status_code == 400
    assert "after setting extraction has started" in blocked.json()["detail"]
