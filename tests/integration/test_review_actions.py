"""Review actions on the setting detail page (FR-19 to FR-22).

The routes only wire forms to the domain state machine; these tests cover
the full web round trip: form POST, PRG redirect with success message,
and inline error re-rendering for domain rejections.
"""

import pytest

from app.db.models import ExtractionRun, Setting
from app.db.session import SessionLocal

PROSE_PAGE = "Plain lowercase reference prose for extraction threshold padding. " * 4


def _seeded_setting_url(client, pdf_bytes_factory):
    response = client.post(
        "/manuals",
        files={"file": ("manual.pdf", pdf_bytes_factory(texts=[PROSE_PAGE]), "application/pdf")},
        data={"vendor": "", "model_name": "", "eligibility_workflow": "legacy"},
        follow_redirects=False,
    )
    manual_id = int(response.headers["location"].rsplit("/", 1)[1])
    with SessionLocal() as session:
        run = session.query(ExtractionRun).filter_by(manual_id=manual_id).one()
        setting = Setting(
            manual_id=manual_id,
            extraction_run_id=run.id,
            extracted_name="Ai Overclock Tuner",
            extracted_description="Original description.",
            page_number=3,
            status="draft",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
        session.add(setting)
        session.commit()
        return f"/settings/{setting.id}"


def test_draft_page_offers_edit_approve_and_reject_but_not_reopen(client, pdf_bytes_factory):
    url = _seeded_setting_url(client, pdf_bytes_factory)
    page = client.get(url).text
    assert f'action="{url}/edit"' in page
    assert f'action="{url}/approve"' in page
    assert f'action="{url}/reject"' in page
    assert f'action="{url}/reopen"' not in page


def test_edit_saves_and_shows_current_next_to_extracted(client, pdf_bytes_factory):
    url = _seeded_setting_url(client, pdf_bytes_factory)
    response = client.post(
        url + "/edit", data={"name": "Renamed", "description": "Cleaned."}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == url + "?done=edited"

    page = client.get(response.headers["location"]).text
    assert "Changes saved." in page
    assert "Renamed" in page
    assert "Cleaned." in page
    # FR-16 / FR-19: originals stay displayed read-only, and the edit is audited.
    assert "Ai Overclock Tuner" in page
    assert "Original description." in page
    assert "edited" in page


def test_edit_with_empty_name_shows_inline_error(client, pdf_bytes_factory):
    url = _seeded_setting_url(client, pdf_bytes_factory)
    response = client.post(url + "/edit", data={"name": "   ", "description": "x"})
    assert response.status_code == 400
    assert "name cannot be empty" in response.text
    assert "status-draft" in response.text  # nothing changed


def test_edit_non_draft_shows_reopen_first_error(client, pdf_bytes_factory):
    url = _seeded_setting_url(client, pdf_bytes_factory)
    client.post(url + "/approve", data={"note": ""})
    response = client.post(url + "/edit", data={"name": "Renamed", "description": ""})
    assert response.status_code == 400
    assert "reopen it first" in response.text


def test_approve_with_note_switches_page_to_reopen_only(client, pdf_bytes_factory):
    url = _seeded_setting_url(client, pdf_bytes_factory)
    response = client.post(url + "/approve", data={"note": "matches the manual"})
    assert response.status_code == 200  # after following the PRG redirect
    page = response.text
    assert "Setting approved." in page
    assert "status-approved" in page
    assert "matches the manual" in page  # review note displayed
    assert f'action="{url}/reopen"' in page
    assert f'action="{url}/edit"' not in page
    assert f'action="{url}/approve"' not in page
    assert f'action="{url}/reject"' not in page


def test_approve_twice_shows_transition_error(client, pdf_bytes_factory):
    url = _seeded_setting_url(client, pdf_bytes_factory)
    client.post(url + "/approve", data={"note": ""})
    response = client.post(url + "/approve", data={"note": ""})
    assert response.status_code == 400
    assert "cannot be moved" in response.text


def test_reject_without_reason_shows_inline_validation_error(client, pdf_bytes_factory):
    url = _seeded_setting_url(client, pdf_bytes_factory)
    response = client.post(url + "/reject", data={"reason": "   "})
    assert response.status_code == 400
    assert "A rejection reason is required." in response.text
    assert "status-draft" in response.text  # FR-21: still a draft


def test_reject_with_reason_stores_note_and_audits(client, pdf_bytes_factory):
    url = _seeded_setting_url(client, pdf_bytes_factory)
    response = client.post(url + "/reject", data={"reason": "Garbled table text."})
    page = response.text
    assert "Setting rejected." in page
    assert "status-rejected" in page
    assert "Garbled table text." in page
    assert "rejected" in page


def test_reopen_returns_to_draft_and_restores_forms(client, pdf_bytes_factory):
    url = _seeded_setting_url(client, pdf_bytes_factory)
    client.post(url + "/reject", data={"reason": "Not a real setting."})
    response = client.post(url + "/reopen")
    page = response.text
    assert "Setting reopened" in page
    assert "status-draft" in page
    assert f'action="{url}/edit"' in page
    assert "Not a real setting." in page  # prior reason stays in the audit history


def test_reopen_a_draft_shows_transition_error(client, pdf_bytes_factory):
    url = _seeded_setting_url(client, pdf_bytes_factory)
    response = client.post(url + "/reopen")
    assert response.status_code == 400
    assert "cannot be moved" in response.text


@pytest.mark.parametrize("action", ["edit", "approve", "reject", "reopen"])
def test_actions_on_unknown_setting_render_404(client, action):
    response = client.post(f"/settings/12345/{action}", data={})
    assert response.status_code == 404
    assert "Setting not found." in response.text
