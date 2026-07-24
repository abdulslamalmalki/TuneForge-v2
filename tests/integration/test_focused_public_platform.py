"""Focused BIOS platform navigation, comparison, and public intake contracts."""

from app.db.models import ExtractionRun, Manual, Setting
from app.db.session import SessionLocal
from app.domain import canonical_settings, review


def _approved_mapped_setting(client, pdf_bytes_factory) -> str:
    with SessionLocal() as session:
        identity = canonical_settings.create_identity(
            session,
            name="Precision Boost Overdrive",
            description="Controls reviewed AMD automatic boost behavior.",
            aliases=(),
        )
        canonical_id = identity.canonical_id
    uploaded = client.post(
        "/manuals",
        files={
            "file": (
                "asus-compare.pdf",
                pdf_bytes_factory(texts=["ASUS PBO Scalar BIOS setting. " * 30]),
                "application/pdf",
            )
        },
        data={
            "vendor": "ASUS",
            "model_name": "ROG Crosshair X870E Hero",
            "document_title": "ASUS X870E BIOS Manual",
            "source_url": "https://www.asus.com/supportonly/rog-x870e/helpdesk_manual/",
            "chipset": "AMD X870E",
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
            extracted_name="PBO Scalar",
            extracted_description="Controls the documented PBO scalar option.",
            page_number=42,
            status="draft",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
        session.add(setting)
        session.commit()
        review.approve_setting(session, setting, note="Reviewed official source setting.")
        canonical_settings.map_setting(
            session,
            setting,
            canonical_id=canonical_id,
            note="Reviewed vendor equivalence.",
        )
    return canonical_id


def test_compare_resolves_vendor_name_to_canonical_setting_with_official_citation(
    client, pdf_bytes_factory
):
    canonical_id = _approved_mapped_setting(client, pdf_bytes_factory)

    response = client.get("/compare", params={"q": "PBO Scalar"})

    assert response.status_code == 200
    assert "Precision Boost Overdrive" in response.text
    assert "PBO Scalar" in response.text
    assert "ASUS X870E BIOS Manual, page 42" in response.text
    assert "Not yet captured in the reviewed record" in response.text
    assert f"/library/canonical/{canonical_id}" in response.text
    assert f"/answer?setting={canonical_id}" in response.text
    assert "Similar wording never creates an automatic equivalence" in response.text


def test_public_manual_submission_is_official_only_and_stays_private(client, pdf_bytes_factory):
    landing = client.get("/upload-manual")
    assert landing.status_code == 200
    assert "Nothing from an upload is published automatically" in landing.text

    rejected = client.post(
        "/upload-manual",
        files={
            "file": (
                "unofficial.pdf",
                pdf_bytes_factory(texts=["Unofficial BIOS text. " * 30]),
                "application/pdf",
            )
        },
        data={
            "vendor": "ASUS",
            "model_name": "ROG Crosshair X870E Hero",
            "source_url": "https://example.com/manual.pdf",
            "language": "en",
        },
    )
    assert rejected.status_code == 400
    assert "official asus.com domain" in rejected.text

    accepted = client.post(
        "/upload-manual",
        files={
            "file": (
                "official.pdf",
                pdf_bytes_factory(texts=["Official ASUS BIOS setting documentation. " * 30]),
                "application/pdf",
            )
        },
        data={
            "vendor": "ASUS",
            "model_name": "ROG Crosshair X870E Hero",
            "document_title": "ASUS X870E BIOS Manual",
            "source_url": "https://www.asus.com/supportonly/rog-x870e/helpdesk_manual/",
            "language": "en",
            "chipset": "AMD X870E",
        },
    )
    assert accepted.status_code == 202
    assert "awaiting verification" in accepted.text
    assert "TF-MANUAL-" in accepted.text
    assert "/manuals/" not in accepted.text

    with SessionLocal() as session:
        manual = session.query(Manual).one()
        run = session.query(ExtractionRun).filter_by(manual_id=manual.id).one()
        assert manual.source_url.startswith("https://www.asus.com/")
        assert manual.eligibility_status != "legacy"
        assert run.status == "pending"
        assert session.query(Setting).count() == 0

    duplicate = client.post(
        "/upload-manual",
        files={
            "file": (
                "same-official.pdf",
                pdf_bytes_factory(texts=["Official ASUS BIOS setting documentation. " * 30]),
                "application/pdf",
            )
        },
        data={
            "vendor": "ASUS",
            "model_name": "ROG Crosshair X870E Hero",
            "source_url": "https://www.asus.com/supportonly/rog-x870e/helpdesk_manual/",
            "language": "en",
        },
    )
    assert duplicate.status_code == 409
    assert "previously submitted" in duplicate.text
