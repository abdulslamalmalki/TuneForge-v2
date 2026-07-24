"""FTS5 search-index maintenance (FR-24, FR-26; plan sections 8 and 9).

The only module that speaks FTS5. settings_fts is a disposable index over
approved settings — effective name and description, with the setting id as
rowid — never a source of truth. The review domain calls add/remove inside
the same transaction as the triggering status change; manual deletion
removes its settings' entries the same way. Nothing here commits: the
caller owns the transaction. rebuild_index reconstructs the whole table
from the settings rows at any time.
"""

from sqlalchemy import Connection, text
from sqlalchemy.orm import Session

CREATE_TABLE_SQL = "CREATE VIRTUAL TABLE IF NOT EXISTS settings_fts USING fts5(name, description)"


def ensure_settings_fts(connection: Connection) -> None:
    """Create the virtual table if missing (test/dev bootstrap; the initial
    migration creates it for real databases)."""
    connection.execute(text(CREATE_TABLE_SQL))


def add_setting(session: Session, setting_id: int, name: str, description: str) -> None:
    """Index one approved setting; idempotent (re-adding replaces the row)."""
    remove_setting(session, setting_id)
    session.execute(
        text("INSERT INTO settings_fts(rowid, name, description) VALUES (:id, :name, :desc)"),
        {"id": setting_id, "name": name, "desc": description},
    )


def remove_setting(session: Session, setting_id: int) -> None:
    """Drop one setting from the index; a no-op when it is not indexed."""
    session.execute(text("DELETE FROM settings_fts WHERE rowid = :id"), {"id": setting_id})


def remove_manual_settings(session: Session, manual_id: int) -> None:
    """Drop every entry belonging to one manual (manual deletion, FR-8):
    the virtual table has no foreign key, so the cascade is manual."""
    session.execute(
        text(
            "DELETE FROM settings_fts WHERE rowid IN"
            " (SELECT id FROM settings WHERE manual_id = :manual_id)"
        ),
        {"manual_id": manual_id},
    )


def rebuild_index(session: Session) -> int:
    """Rebuild the whole index from the settings table; returns the row count.

    COALESCE mirrors the effective-value rule (plan section 9): edited value
    when not NULL, else extracted — an edited empty string stays an override.
    """
    session.execute(text("DELETE FROM settings_fts"))
    result = session.execute(
        text(
            "INSERT INTO settings_fts(rowid, name, description)"
            " SELECT id, COALESCE(edited_name, extracted_name),"
            " COALESCE(edited_description, extracted_description)"
            " FROM settings WHERE status = 'approved'"
        )
    )
    return result.rowcount
