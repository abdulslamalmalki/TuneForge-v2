"""Setting detail page (/settings/{id}): FR-18 display, read-only slice.

Manuals are uploaded through the web (prose only, so the parser adds no
drafts); Setting rows are seeded directly, and review-domain actions
generate the audit events under test.
"""

from app.db.models import ExtractionRun, Setting
from app.db.session import SessionLocal
from app.domain import review

PROSE_PAGE = "Plain lowercase reference prose for extraction threshold padding. " * 4


def _upload_manual(client, pdf_bytes, vendor="ASUS"):
    response = client.post(
        "/manuals",
        files={"file": ("manual.pdf", pdf_bytes, "application/pdf")},
        data={"vendor": vendor, "model_name": "X670", "eligibility_workflow": "legacy"},
        follow_redirects=False,
    )
    return int(response.headers["location"].rsplit("/", 1)[1])


def _seed_setting(manual_id, name="Ai Overclock Tuner", description="Original.", page_number=3):
    with SessionLocal() as session:
        run = session.query(ExtractionRun).filter_by(manual_id=manual_id).one()
        setting = Setting(
            manual_id=manual_id,
            extraction_run_id=run.id,
            extracted_name=name,
            extracted_description=description,
            page_number=page_number,
            status="draft",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
        session.add(setting)
        session.commit()
        return setting.id


def _apply(setting_id, action):
    with SessionLocal() as session:
        setting = session.get(Setting, setting_id)
        action(session, setting)


def test_unknown_setting_renders_404_page(client):
    response = client.get("/settings/12345")
    assert response.status_code == 404
    assert "Setting not found." in response.text


def test_draft_detail_shows_source_status_and_page_link(client, pdf_bytes_factory):
    manual_id = _upload_manual(client, pdf_bytes_factory(texts=[PROSE_PAGE]))
    setting_id = _seed_setting(manual_id, page_number=3)

    page = client.get(f"/settings/{setting_id}").text
    assert "Ai Overclock Tuner" in page
    assert "status-draft" in page
    assert f'href="/manuals/{manual_id}"' in page
    assert "ASUS X670" in page
    # FR-18: opens the served PDF at the setting's page via #page=N.
    assert f'href="/manuals/{manual_id}/file#page=3"' in page
    assert "Open manual at page 3" in page
    assert "No review actions yet." in page


def test_detail_shows_extracted_vs_current_after_edit(client, pdf_bytes_factory):
    manual_id = _upload_manual(client, pdf_bytes_factory(texts=[PROSE_PAGE]))
    setting_id = _seed_setting(manual_id)
    _apply(
        setting_id,
        lambda s, x: review.edit_draft_setting(s, x, name="Renamed", description="Cleaned."),
    )

    page = client.get(f"/settings/{setting_id}").text
    # Both columns visible: original stays displayed alongside the edit.
    assert "Ai Overclock Tuner" in page
    assert "Renamed" in page
    assert "Original." in page
    assert "Cleaned." in page
    # The edit is audited with before/after values.
    assert "edited" in page
    assert review.ACTOR in page
    assert "before" in page


def test_rejected_detail_shows_note_badge_and_audit_reason(client, pdf_bytes_factory):
    manual_id = _upload_manual(client, pdf_bytes_factory(texts=[PROSE_PAGE]))
    setting_id = _seed_setting(manual_id)
    _apply(setting_id, lambda s, x: review.reject_setting(s, x, "Garbled table text."))

    page = client.get(f"/settings/{setting_id}").text
    assert "status-rejected" in page
    assert "Review note" in page
    assert "Garbled table text." in page


def test_audit_history_lists_actions_in_order(client, pdf_bytes_factory):
    manual_id = _upload_manual(client, pdf_bytes_factory(texts=[PROSE_PAGE]))
    setting_id = _seed_setting(manual_id)
    _apply(setting_id, lambda s, x: review.approve_setting(s, x, note="looks right"))
    _apply(setting_id, lambda s, x: review.reopen_setting(s, x))

    page = client.get(f"/settings/{setting_id}").text
    assert "status-draft" in page  # reopened back to draft
    assert page.index("approved") < page.index("reopened")
    assert "looks right" in page  # approval note preserved in the audit trail


def test_empty_description_shows_placeholder(client, pdf_bytes_factory):
    manual_id = _upload_manual(client, pdf_bytes_factory(texts=[PROSE_PAGE]))
    setting_id = _seed_setting(manual_id, description="")

    page = client.get(f"/settings/{setting_id}").text
    assert "—" in page
