"""Fail-fast production storage checks that run before schema migrations."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from app import config
from app.db.sqlite_storage import verify_sqlite_storage


@dataclass(frozen=True)
class ProductionPreflight:
    environment: str
    data_dir: str
    backups_dir: str
    database_path: str
    sqlite_journal_mode: str
    sqlite_busy_timeout_seconds: int


def check() -> ProductionPreflight:
    """Validate configuration and every durable write boundary before migration."""

    config.validate_runtime_config()
    config.ensure_data_dirs()
    database_path = verify_sqlite_storage(config.DATABASE_PATH)
    for path in (config.UPLOADS_DIR, config.EVIDENCE_SOURCES_DIR, config.BACKUPS_DIR):
        _verify_writable_directory(path)
    return ProductionPreflight(
        environment=config.ENVIRONMENT,
        data_dir=str(config.DATA_DIR.resolve()),
        backups_dir=str(config.BACKUPS_DIR.resolve()),
        database_path=str(database_path),
        sqlite_journal_mode=config.SQLITE_JOURNAL_MODE,
        sqlite_busy_timeout_seconds=config.SQLITE_BUSY_TIMEOUT_SECONDS,
    )


def _verify_writable_directory(path: Path) -> None:
    try:
        with tempfile.NamedTemporaryFile(
            prefix=".tuneforge-preflight-", dir=path, delete=True
        ) as probe:
            probe.write(b"TuneForge production write probe\n")
            probe.flush()
    except OSError as error:
        raise RuntimeError(
            f"TuneForge cannot create and remove files in durable storage: {path.resolve()}"
        ) from error
