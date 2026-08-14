"""Low-cost deployment readiness checks with no sensitive operational detail."""

import os
import tempfile
from dataclasses import dataclass

from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import config
from app.db.models import UserAccount


@dataclass(frozen=True)
class Readiness:
    status: str
    database_revision: str | None
    authentication_initialized: bool
    checks: dict[str, bool]


def check(session: Session) -> Readiness:
    database_available = False
    revision: str | None = None
    authentication_initialized = False
    database_integrity = False
    foreign_keys = False
    try:
        database_available = session.scalar(text("SELECT 1")) == 1
        revision = session.scalar(text("SELECT version_num FROM alembic_version"))
        database_integrity = session.scalar(text("PRAGMA quick_check")) == "ok"
        foreign_keys = not list(session.execute(text("PRAGMA foreign_key_check")))
        authentication_initialized = bool(
            session.scalar(select(func.count()).select_from(UserAccount))
        )
    except SQLAlchemyError:
        session.rollback()

    checks = {
        "database": database_available,
        "database_integrity": database_integrity,
        "foreign_keys": foreign_keys,
        "schema": revision == config.EXPECTED_DATABASE_REVISION,
        "uploads_storage": _writable_directory(config.UPLOADS_DIR),
        "evidence_storage": _writable_directory(config.EVIDENCE_SOURCES_DIR),
        "backups_storage": _writable_directory(config.BACKUPS_DIR),
    }
    return Readiness(
        status="ready" if all(checks.values()) else "unavailable",
        database_revision=revision,
        authentication_initialized=authentication_initialized,
        checks=checks,
    )


def _writable_directory(path) -> bool:
    if not path.is_dir() or not os.access(path, os.R_OK | os.W_OK):
        return False
    try:
        with tempfile.NamedTemporaryFile(prefix=".tuneforge-ready-", dir=path):
            return True
    except OSError:
        return False
