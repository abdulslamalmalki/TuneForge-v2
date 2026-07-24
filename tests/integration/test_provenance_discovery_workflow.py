from sqlalchemy import select

from app.db.models import ExtractionRun, Manual, ManualEvent
from app.db.session import SessionLocal
from app.web.routes import manuals as manual_routes


def _discovery_upload(client, pdf_bytes_factory, monkeypatch):
    monkeypatch.setattr(
        manual_routes.provenance_discovery, "execute_discovery", lambda _manual_id: None
    )
    return client.post(
        "/manuals",
        files={
            "file": (
                "ROG_X870E_Series_BIOS_Manual.pdf",
                pdf_bytes_factory(texts=["ASUS ROG X870E Series BIOS Manual"]),
                "application/pdf",
            )
        },
        data={"provenance_workflow": "discover"},
        follow_redirects=False,
    )


def test_ui_upload_queues_discovery_and_holds_extraction(client, pdf_bytes_factory, monkeypatch):
    response = _discovery_upload(client, pdf_bytes_factory, monkeypatch)
    assert response.status_code == 303
    assert response.headers["location"].endswith("/provenance/discovery")
    manual_id = int(response.headers["location"].split("/")[2])

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
        assert actions == [
            "provenance_created",
            "hardware_applicability_normalized",
            "provenance_discovery_queued",
            "eligibility_classified",
        ]

    page = client.get(response.headers["location"])
    assert page.status_code == 200
    assert "in progress" in page.text


def test_confirmation_rejects_lookalike_domain(client, pdf_bytes_factory, monkeypatch):
    response = _discovery_upload(client, pdf_bytes_factory, monkeypatch)
    manual_id = int(response.headers["location"].split("/")[2])
    rejected = client.post(
        f"/manuals/{manual_id}/provenance/confirm",
        data={
            "vendor": "ASUS",
            "document_title": "ROG X870E Series BIOS Manual",
            "source_url": "https://asus.com.evil.example/supportonly/rog/helpdesk_manual/",
            "language": "en",
        },
    )
    assert rejected.status_code == 400
    assert "official asus.com domain" in rejected.text
    with SessionLocal() as session:
        assert session.get(Manual, manual_id).source_url is None


def test_confirmation_saves_provenance_and_routes_to_eligibility(
    client, pdf_bytes_factory, monkeypatch
):
    response = _discovery_upload(client, pdf_bytes_factory, monkeypatch)
    manual_id = int(response.headers["location"].split("/")[2])
    started = []
    monkeypatch.setattr(
        manual_routes.extraction_pipeline,
        "execute_pending_run",
        lambda value: started.append(value),
    )
    official = "https://www.asus.com/us/supportonly/rog-x870e-hero/helpdesk_manual/"
    confirmed = client.post(
        f"/manuals/{manual_id}/provenance/confirm",
        data={
            "vendor": "ASUS",
            "model_name": "ROG X870E HERO",
            "document_title": "ROG X870E Series BIOS Manual",
            "source_url": official,
            "language": "en",
            "chipset": "X870E",
        },
        follow_redirects=False,
    )
    assert confirmed.status_code == 303
    assert confirmed.headers["location"].endswith("/eligibility")
    assert started == []
    with SessionLocal() as session:
        manual = session.get(Manual, manual_id)
        assert manual.source_url == official
        assert manual.vendor == "ASUS"
        actions = list(
            session.scalars(
                select(ManualEvent.action)
                .where(ManualEvent.manual_id == manual_id)
                .order_by(ManualEvent.id)
            )
        )
        assert actions[-3:] == [
            "provenance_updated",
            "hardware_applicability_normalized",
            "provenance_discovery_confirmed",
        ]
