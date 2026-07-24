import errno
import tempfile

import pytest

from app.db.sqlite_storage import SQLiteStorageError, verify_sqlite_storage


def test_verify_sqlite_storage_accepts_writable_parent_and_database(tmp_path):
    database = tmp_path / "knowledge_builder.sqlite3"
    database.write_bytes(b"")

    assert verify_sqlite_storage(database) == database.resolve()
    assert list(tmp_path.glob("*-write-probe-*.journal")) == []


def test_verify_sqlite_storage_accepts_writable_parent_before_database_exists(tmp_path):
    database = tmp_path / "knowledge_builder.sqlite3"

    assert verify_sqlite_storage(database) == database.resolve()
    assert not database.exists()


def test_verify_sqlite_storage_rejects_missing_parent(tmp_path):
    database = tmp_path / "missing" / "knowledge_builder.sqlite3"

    with pytest.raises(SQLiteStorageError, match="parent directory does not exist"):
        verify_sqlite_storage(database)


def test_verify_sqlite_storage_reports_sidecar_creation_failure(tmp_path, monkeypatch):
    database = tmp_path / "knowledge_builder.sqlite3"
    database.write_bytes(b"")

    def denied_mkstemp(**_kwargs):
        raise OSError(errno.EACCES, "permission denied")

    monkeypatch.setattr(tempfile, "mkstemp", denied_mkstemp)

    with pytest.raises(SQLiteStorageError) as captured:
        verify_sqlite_storage(database)

    message = str(captured.value)
    assert "transaction sidecar files" in message
    assert str(database.resolve()) in message
    assert "KNOWLEDGE_BUILDER_DATA_DIR" in message
