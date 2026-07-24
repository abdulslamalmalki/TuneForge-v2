"""The rebuild-index maintenance command (plan sections 9 and 15, M5)."""

import io

from sqlalchemy import text

from app.domain import manuals as manuals_domain
from app.domain import review
from app.domain import settings as settings_domain
from app.domain.extraction_runs import get_run_for_manual
from app.extraction.settings_parser import SettingCandidate
from app.search import query as search_query
from app.search import rebuild


def _approved_setting(db_session, pdf_bytes):
    manual = manuals_domain.create_manual(
        db_session,
        io.BytesIO(pdf_bytes),
        original_filename="manual.pdf",
        vendor="",
        model_name="",
    )
    run = get_run_for_manual(db_session, manual.id)
    (setting,) = settings_domain.create_draft_settings(
        db_session,
        manual_id=manual.id,
        extraction_run_id=run.id,
        candidates=[SettingCandidate("Ai Overclock Tuner", "Selects the memory profile.", 3)],
    )
    db_session.commit()
    review.approve_setting(db_session, setting)
    return setting


def test_command_restores_a_wiped_index(db_session, pdf_bytes_factory, capsys):
    setting = _approved_setting(db_session, pdf_bytes_factory())
    db_session.execute(text("DELETE FROM settings_fts"))
    db_session.commit()
    assert search_query.search_approved(db_session, "memory") == []

    assert rebuild.main() == 1
    assert "1 approved setting(s) indexed" in capsys.readouterr().out

    db_session.expire_all()
    (result,) = search_query.search_approved(db_session, "memory")
    assert result.setting_id == setting.id


def test_command_recreates_a_missing_table(db_session, capsys):
    db_session.execute(text("DROP TABLE settings_fts"))
    db_session.commit()

    assert rebuild.main() == 0
    assert "0 approved setting(s) indexed" in capsys.readouterr().out
    assert search_query.search_approved(db_session, "anything") == []


def test_command_purges_entries_that_should_not_exist(db_session, pdf_bytes_factory):
    setting = _approved_setting(db_session, pdf_bytes_factory())
    # Simulate drift: a stale extra row and a reviewed setting downgraded
    # behind the domain's back.
    db_session.execute(
        text("INSERT INTO settings_fts(rowid, name, description) VALUES (999, 'Stale', '')")
    )
    db_session.execute(
        text("UPDATE settings SET status = 'draft' WHERE id = :id"), {"id": setting.id}
    )
    db_session.commit()

    assert rebuild.main() == 0
    db_session.expire_all()
    assert search_query.search_approved(db_session, "Stale") == []
    assert search_query.search_approved(db_session, "memory") == []
