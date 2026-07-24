"""Review queue page (/review): FR-17 listing, filter, ordering, empty state.

Manuals are uploaded through the web (prose only, so the parser adds no
drafts of its own); the queue's Setting rows are seeded directly so names,
pages, and statuses are exact.
"""

from app.db.models import ExtractionRun, Setting
from app.db.session import SessionLocal
from app.domain import review

PROSE_PAGE = "Plain lowercase reference prose for extraction threshold padding. " * 4


def _upload_manual(client, pdf_bytes, vendor=""):
    response = client.post(
        "/manuals",
        files={"file": ("manual.pdf", pdf_bytes, "application/pdf")},
        data={"vendor": vendor, "model_name": "", "eligibility_workflow": "legacy"},
        follow_redirects=False,
    )
    return int(response.headers["location"].rsplit("/", 1)[1])


def _seed_setting(manual_id, name, page_number, status="draft", description=""):
    with SessionLocal() as session:
        run = session.query(ExtractionRun).filter_by(manual_id=manual_id).one()
        setting = Setting(
            manual_id=manual_id,
            extraction_run_id=run.id,
            extracted_name=name,
            extracted_description=description,
            page_number=page_number,
            status=status,
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
        session.add(setting)
        session.commit()
        return setting.id


def test_empty_queue_shows_empty_state_with_upload_link(client):
    page = client.get("/review").text
    assert "No drafts to review" in page
    assert "/manuals/upload" in page


def test_nav_links_to_review_queue(client):
    page = client.get("/manuals").text
    assert 'href="/review"' in page
    assert "Setting drafts" in page


def test_manual_detail_links_to_its_filtered_queue(client, pdf_bytes_factory):
    # Plan section 12, item 3: the manual detail page links to this
    # manual's review queue.
    manual_id = _upload_manual(client, pdf_bytes_factory(texts=[PROSE_PAGE]))
    _seed_setting(manual_id, "Queued Setting", 1)

    detail_page = client.get(f"/manuals/{manual_id}").text
    assert f'href="/review?manual_id={manual_id}"' in detail_page

    queue_page = client.get(f"/review?manual_id={manual_id}").text
    assert "Queued Setting" in queue_page


def test_queue_lists_drafts_ordered_by_manual_then_page(client, pdf_bytes_factory):
    first = _upload_manual(client, pdf_bytes_factory(texts=[PROSE_PAGE]), vendor="ASUS")
    second = _upload_manual(client, pdf_bytes_factory(texts=[PROSE_PAGE] * 2), vendor="MSI")
    _seed_setting(first, "Late Setting", 9)
    _seed_setting(first, "Early Setting", 4)
    second_setting_id = _seed_setting(second, "Other Manual Setting", 1)

    page = client.get("/review").text
    assert page.index("Early Setting") < page.index("Late Setting")
    assert page.index("Late Setting") < page.index("Other Manual Setting")
    # Rows link to the setting detail page and the source manual.
    assert f'href="/settings/{second_setting_id}"' in page
    assert f'href="/manuals/{second}"' in page


def test_queue_filters_by_manual_and_keeps_selection(client, pdf_bytes_factory):
    first = _upload_manual(client, pdf_bytes_factory(texts=[PROSE_PAGE]), vendor="ASUS")
    second = _upload_manual(client, pdf_bytes_factory(texts=[PROSE_PAGE] * 2), vendor="MSI")
    _seed_setting(first, "First Manual Setting", 1)
    _seed_setting(second, "Second Manual Setting", 1)

    page = client.get(f"/review?manual_id={second}").text
    assert "Second Manual Setting" in page
    assert "First Manual Setting" not in page
    assert f'value="{second}" selected' in page


def test_queue_excludes_reviewed_settings(client, pdf_bytes_factory):
    manual_id = _upload_manual(client, pdf_bytes_factory(texts=[PROSE_PAGE]))
    _seed_setting(manual_id, "Still Draft", 1)
    _seed_setting(manual_id, "Already Approved", 2, status="approved")
    _seed_setting(manual_id, "Already Rejected", 3, status="rejected")

    page = client.get("/review").text
    assert "Still Draft" in page
    assert "Already Approved" not in page
    assert "Already Rejected" not in page


def test_queue_shows_edited_name_when_present(client, pdf_bytes_factory):
    manual_id = _upload_manual(client, pdf_bytes_factory(texts=[PROSE_PAGE]))
    setting_id = _seed_setting(manual_id, "Extracted Name", 1, description="Original text.")
    with SessionLocal() as session:
        setting = session.get(Setting, setting_id)
        review.edit_draft_setting(session, setting, name="Edited Name", description="New text.")

    page = client.get("/review").text
    assert "Edited Name" in page
    assert "New text." in page


def test_invalid_manual_filter_falls_back_to_unfiltered(client, pdf_bytes_factory):
    manual_id = _upload_manual(client, pdf_bytes_factory(texts=[PROSE_PAGE]))
    _seed_setting(manual_id, "Visible Setting", 1)

    response = client.get("/review?manual_id=not-a-number")
    assert response.status_code == 200
    assert "Visible Setting" in response.text
