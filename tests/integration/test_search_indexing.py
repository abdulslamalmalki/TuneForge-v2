"""Search-index maintenance through the web flow (FR-24, FR-26).

Approving via the review UI indexes the setting in the same transaction;
reopening removes it immediately; deleting a manual removes its entries.
No search page exists yet — the index itself is inspected directly.
"""

from sqlalchemy import text

from app.db.models import ExtractionRun, Setting
from app.db.session import SessionLocal

PROSE_PAGE = "Plain lowercase reference prose for extraction threshold padding. " * 4


def _seed(client, pdf_bytes_factory):
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
            extracted_description="Selects the memory profile.",
            page_number=3,
            status="draft",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
        session.add(setting)
        session.commit()
        return manual_id, setting.id


def _indexed_ids():
    with SessionLocal() as session:
        return [row.rowid for row in session.execute(text("SELECT rowid FROM settings_fts"))]


def test_approving_via_the_web_indexes_immediately(client, pdf_bytes_factory):
    manual_id, setting_id = _seed(client, pdf_bytes_factory)
    assert _indexed_ids() == []

    client.post(f"/settings/{setting_id}/approve", data={"note": ""})
    assert _indexed_ids() == [setting_id]


def test_reopening_via_the_web_removes_immediately(client, pdf_bytes_factory):
    manual_id, setting_id = _seed(client, pdf_bytes_factory)
    client.post(f"/settings/{setting_id}/approve", data={"note": ""})
    client.post(f"/settings/{setting_id}/reopen")
    assert _indexed_ids() == []  # FR-26


def test_rejecting_a_draft_never_indexes(client, pdf_bytes_factory):
    _, setting_id = _seed(client, pdf_bytes_factory)
    client.post(f"/settings/{setting_id}/reject", data={"reason": "noise"})
    assert _indexed_ids() == []


def test_deleting_a_manual_removes_its_search_entries(client, pdf_bytes_factory):
    manual_id, setting_id = _seed(client, pdf_bytes_factory)
    client.post(f"/settings/{setting_id}/approve", data={"note": ""})
    assert _indexed_ids() == [setting_id]

    response = client.post(
        f"/manuals/{manual_id}/delete", data={"confirm": "yes"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert _indexed_ids() == []
