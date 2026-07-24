"""Rebuild the settings_fts search index from the settings table.

Run from the knowledge-builder directory:

    python -m app.search.rebuild

The index is disposable (plan section 9): this drops every entry and
re-inserts one row per approved setting with its effective values. Safe
to run at any time; recreates the virtual table if it is missing.
"""

from app.db.session import SessionLocal
from app.search import index


def main() -> int:
    with SessionLocal() as session:
        index.ensure_settings_fts(session.connection())
        count = index.rebuild_index(session)
        session.commit()
    print(f"Search index rebuilt: {count} approved setting(s) indexed.")
    return count


if __name__ == "__main__":
    main()
