from pathlib import Path

from sqlalchemy import text

from app import config
from app.db.session import engine
from app.operations import preflight


def test_runtime_sqlite_connection_uses_production_safety_pragmas():
    with engine.connect() as connection:
        assert connection.scalar(text("PRAGMA foreign_keys")) == 1
        assert connection.scalar(text("PRAGMA busy_timeout")) == 30_000
        assert connection.scalar(text("PRAGMA journal_mode")) == "wal"
        assert connection.scalar(text("PRAGMA synchronous")) == 2


def test_preflight_validates_all_durable_directories(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    backups_dir = tmp_path / "off-volume-backups"
    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    monkeypatch.setattr(config, "BACKUPS_DIR", backups_dir)
    monkeypatch.setattr(config, "UPLOADS_DIR", data_dir / "uploads")
    monkeypatch.setattr(config, "EVIDENCE_SOURCES_DIR", data_dir / "evidence_sources")
    monkeypatch.setattr(config, "DATABASE_PATH", data_dir / "knowledge_builder.sqlite3")

    result = preflight.check()

    assert Path(result.data_dir) == data_dir.resolve()
    assert Path(result.backups_dir) == backups_dir.resolve()
    assert Path(result.database_path) == config.DATABASE_PATH.resolve()
    assert result.sqlite_journal_mode == "wal"
    assert list(data_dir.rglob(".tuneforge-preflight-*")) == []
    assert list(backups_dir.glob(".tuneforge-preflight-*")) == []
