"""End-to-end flow: upload -> extraction -> parser -> Draft rows (plan section 10).

The test client executes the upload's background task synchronously, so
after POST /manuals returns, the run and its drafts are fully persisted.
"""

from sqlalchemy import select

from app.db.models import ExtractionRun, Setting
from app.db.session import SessionLocal
from app.extraction.settings_parser import PARSER_VERSION
from tests.conftest import make_pdf_bytes

_PAGE_ONE = (
    "Ai Overclock Tuner\n"
    "Allows you to select the memory overclocking profile for this system.\n"
    "Configuration options: [Auto] [Manual]"
)
_PAGE_TWO = (
    "DRAM Voltage Control\nSets the voltage supplied to the installed memory modules on this board."
)


def _upload(client, pdf_bytes, filename="manual.pdf"):
    return client.post(
        "/manuals",
        files={"file": (filename, pdf_bytes, "application/pdf")},
        data={"vendor": "", "model_name": "", "eligibility_workflow": "legacy"},
        follow_redirects=False,
    )


def _run_and_drafts():
    with SessionLocal() as session:
        run = session.scalars(select(ExtractionRun)).one()
        drafts = session.scalars(select(Setting).order_by(Setting.page_number)).all()
        session.expunge_all()
    return run, drafts


def test_upload_produces_draft_settings_with_provenance(client):
    response = _upload(client, make_pdf_bytes(texts=[_PAGE_ONE, _PAGE_TWO]))
    assert response.status_code == 303

    run, drafts = _run_and_drafts()
    assert run.status == "succeeded"
    assert run.settings_found == 2 == len(drafts)
    assert run.parser_version == PARSER_VERSION

    first, second = drafts
    assert first.extracted_name == "Ai Overclock Tuner"
    assert first.extracted_description == (
        "Allows you to select the memory overclocking profile for this system.\n"
        "Configuration options: [Auto] [Manual]"
    )
    assert first.page_number == 1
    assert second.extracted_name == "DRAM Voltage Control"
    assert second.extracted_description == (
        "Sets the voltage supplied to the installed memory modules on this board."
    )
    assert second.page_number == 2
    for draft in drafts:
        assert draft.status == "draft"
        assert (draft.manual_id, draft.extraction_run_id) == (run.manual_id, run.id)
        assert draft.edited_name is None and draft.edited_description is None


def test_manual_detail_shows_settings_found_count(client):
    _upload(client, make_pdf_bytes(texts=[_PAGE_ONE, _PAGE_TWO]))
    run, _ = _run_and_drafts()
    page = client.get(f"/manuals/{run.manual_id}").text
    assert "Settings found" in page
    assert ">2<" in page


def test_zero_candidate_upload_succeeds_with_stamped_version(client):
    # Prose above the FR-10 threshold but with nothing heading-shaped: the
    # parser ran (version stamped) and legitimately found nothing.
    prose = "plain lowercase reference prose for extraction threshold padding. " * 4
    response = _upload(client, make_pdf_bytes(texts=[prose]))
    assert response.status_code == 303

    run, drafts = _run_and_drafts()
    assert run.status == "succeeded"
    assert run.settings_found == 0
    assert run.parser_version == PARSER_VERSION
    assert drafts == []


def test_failed_upload_leaves_no_drafts_and_null_version(client):
    response = _upload(client, make_pdf_bytes(3))  # pages without a text layer
    assert response.status_code == 303

    run, drafts = _run_and_drafts()
    assert run.status == "failed"
    assert run.settings_found == 0
    assert run.parser_version is None
    assert drafts == []


def test_deleting_manual_removes_its_drafts(client):
    _upload(client, make_pdf_bytes(texts=[_PAGE_ONE, _PAGE_TWO]))
    run, drafts = _run_and_drafts()
    assert len(drafts) == 2

    response = client.post(
        f"/manuals/{run.manual_id}/delete", data={"confirm": "yes"}, follow_redirects=False
    )
    assert response.status_code == 303
    with SessionLocal() as session:
        assert session.scalars(select(Setting)).all() == []
