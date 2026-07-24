"""FTS5 index module: add/remove/rebuild and the approved-only invariant.

The virtual table has no foreign keys, so add/remove tests can use bare
ids; rebuild and manual-scoped removal read the real settings table.
"""

import io

from sqlalchemy import text

from app.domain import manuals as manuals_domain
from app.domain import review
from app.domain import settings as settings_domain
from app.domain.extraction_runs import get_run_for_manual
from app.extraction.settings_parser import SettingCandidate
from app.search import index as search_index


def _rows(db_session):
    query = text("SELECT rowid, name, description FROM settings_fts ORDER BY rowid")
    return [tuple(row) for row in db_session.execute(query)]


def _match(db_session, keywords):
    query = text("SELECT rowid FROM settings_fts WHERE settings_fts MATCH :q ORDER BY rowid")
    return [row.rowid for row in db_session.execute(query, {"q": keywords})]


def _manual_with_drafts(db_session, pdf_bytes, candidates):
    manual = manuals_domain.create_manual(
        db_session,
        io.BytesIO(pdf_bytes),
        original_filename="manual.pdf",
        vendor="",
        model_name="",
    )
    run = get_run_for_manual(db_session, manual.id)
    settings = settings_domain.create_draft_settings(
        db_session, manual_id=manual.id, extraction_run_id=run.id, candidates=candidates
    )
    db_session.commit()
    return manual, settings


def test_add_setting_is_an_idempotent_replace(db_session):
    search_index.add_setting(db_session, 7, "CPU Ratio", "Sets the ratio.")
    search_index.add_setting(db_session, 7, "CPU Ratio Mode", "Sets the ratio mode.")
    assert _rows(db_session) == [(7, "CPU Ratio Mode", "Sets the ratio mode.")]


def test_remove_setting_deletes_and_tolerates_absence(db_session):
    search_index.add_setting(db_session, 5, "DRAM Voltage", "")
    search_index.remove_setting(db_session, 5)
    search_index.remove_setting(db_session, 5)  # no-op, no error
    assert _rows(db_session) == []


def test_keyword_match_hits_name_and_description(db_session):
    search_index.add_setting(db_session, 1, "Ai Overclock Tuner", "Selects the memory profile.")
    search_index.add_setting(db_session, 2, "CPU Core Voltage", "Primary supply for the cores.")
    assert _match(db_session, "memory") == [1]
    assert _match(db_session, "voltage") == [2]
    assert _match(db_session, "nonexistent") == []


def test_rebuild_restores_only_approved_settings_with_effective_values(
    db_session, pdf_bytes_factory
):
    _, (approved, draft, rejected) = _manual_with_drafts(
        db_session,
        pdf_bytes_factory(),
        [
            SettingCandidate("Ai Overclock Tuner", "Original description.", 1),
            SettingCandidate("Still Draft", "", 2),
            SettingCandidate("Bad Candidate", "", 3),
        ],
    )
    review.edit_draft_setting(db_session, approved, name="Renamed", description="Cleaned.")
    review.approve_setting(db_session, approved)
    review.reject_setting(db_session, rejected, "noise")

    # Corrupt the disposable index, then rebuild it from the settings table.
    db_session.execute(text("DELETE FROM settings_fts"))
    search_index.add_setting(db_session, 999, "Stale Entry", "Should vanish.")

    assert search_index.rebuild_index(db_session) == 1
    assert _rows(db_session) == [(approved.id, "Renamed", "Cleaned.")]


def test_remove_manual_settings_only_touches_that_manual(db_session, pdf_bytes_factory):
    _, (first,) = _manual_with_drafts(
        db_session, pdf_bytes_factory(), [SettingCandidate("First Setting", "", 1)]
    )
    _, (second,) = _manual_with_drafts(
        db_session, pdf_bytes_factory(2), [SettingCandidate("Second Setting", "", 1)]
    )
    review.approve_setting(db_session, first)
    review.approve_setting(db_session, second)

    search_index.remove_manual_settings(db_session, first.manual_id)
    assert _rows(db_session) == [(second.id, "Second Setting", "")]
