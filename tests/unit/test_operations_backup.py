import json
import sqlite3
import zipfile
from contextlib import closing
from io import BytesIO

import pytest

from app import config
from app.domain import auth, manuals
from app.operations.backup import BackupError, create_backup, inspect_backup, restore_backup


def test_backup_and_restore_preserve_data_but_revoke_sessions(
    tmp_path, db_session, pdf_bytes_factory
):
    user = auth.create_first_administrator(
        db_session,
        username="owner",
        display_name="TuneForge Owner",
        password="a long backup test password",
    )
    auth.create_session(db_session, user)
    manual = manuals.create_manual(
        db_session,
        BytesIO(pdf_bytes_factory()),
        "test-manual.pdf",
        "ASUS",
        "Test board",
    )
    archive = tmp_path / "tuneforge.zip"

    result = create_backup(config.DATA_DIR, archive)
    inspection = inspect_backup(archive)
    restored = restore_backup(archive, tmp_path / "restored")

    assert result.database_revision == config.EXPECTED_DATABASE_REVISION
    assert result.file_count == 2
    assert len(result.archive_sha256) == 64
    assert inspection.database_revision == config.EXPECTED_DATABASE_REVISION
    assert inspection.app_version == config.APP_VERSION
    assert inspection.file_count == result.file_count
    assert inspection.uncompressed_bytes == result.uncompressed_bytes
    assert restored.file_count == 2
    with closing(sqlite3.connect(restored.target_dir / "knowledge_builder.sqlite3")) as connection:
        assert connection.execute("SELECT username FROM user_accounts").fetchone() == ("owner",)
        assert connection.execute("SELECT COUNT(*) FROM auth_sessions").fetchone() == (0,)
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("delete",)
    assert (restored.target_dir / "uploads" / manual.stored_filename).is_file()
    manifest = json.loads((restored.target_dir / "backup-manifest.json").read_text())
    assert manifest["sessions_included"] is False


def test_backup_refuses_to_overwrite_an_existing_archive(tmp_path):
    output = tmp_path / "existing.zip"
    output.write_bytes(b"do not overwrite")

    with pytest.raises(BackupError, match="already exists"):
        create_backup(config.DATA_DIR, output)

    assert output.read_bytes() == b"do not overwrite"


def test_backup_uses_temporary_files_and_cleans_them(tmp_path):
    output = tmp_path / "checkpoint.zip"

    create_backup(config.DATA_DIR, output)

    assert output.is_file()
    assert not tuple(tmp_path.glob(".tuneforge-database-*"))
    assert not tuple(tmp_path.glob(".tuneforge-archive-*"))
    assert not tuple(path for path in tmp_path.iterdir() if path.is_dir())


def test_backup_progress_reports_stages_and_each_member(
    tmp_path,
    db_session,
    pdf_bytes_factory,
):
    manual = manuals.create_manual(
        db_session,
        BytesIO(pdf_bytes_factory()),
        "progress-manual.pdf",
        "ASUS",
        "Progress board",
    )
    output = tmp_path / "progress.zip"
    messages: list[str] = []

    create_backup(config.DATA_DIR, output, progress=messages.append)

    assert messages[0].startswith("Starting backup:")
    assert "Database snapshot complete" in "\n".join(messages)
    assert "Database inventory complete" in "\n".join(messages)
    assert "Manifest generated" in "\n".join(messages)
    assert "Beginning archive creation..." in messages
    assert any(
        message.startswith(f"Writing uploads/{manual.stored_filename}")
        for message in messages
    )
    assert any(
        message.startswith(f"Finished uploads/{manual.stored_filename}")
        for message in messages
    )
    assert "Verifying staged archive contents..." in messages
    assert any(
        message.startswith("Staged archive verification complete")
        for message in messages
    )
    assert "Archive published atomically." in messages
    assert messages[-1].startswith("Backup complete")


def test_restore_refuses_existing_target_and_unsafe_archive_member(tmp_path):
    archive = tmp_path / "valid.zip"
    create_backup(config.DATA_DIR, archive)
    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(BackupError, match="already exists"):
        restore_backup(archive, existing)

    malicious = tmp_path / "malicious.zip"
    manifest = {
        "format": "tuneforge-data-backup",
        "format_version": 1,
        "database_revision": config.EXPECTED_DATABASE_REVISION,
        "sessions_included": False,
        "files": [{"path": "../escape", "size_bytes": 1, "sha256": "0" * 64}],
    }
    with zipfile.ZipFile(malicious, "w") as output:
        output.writestr("manifest.json", json.dumps(manifest))
        output.writestr("../escape", b"x")
    target = tmp_path / "unsafe-restore"

    with pytest.raises(BackupError, match="unsafe member"):
        restore_backup(malicious, target)

    assert not target.exists()


def test_restore_detects_archive_corruption(tmp_path):
    original = tmp_path / "original.zip"
    create_backup(config.DATA_DIR, original)
    corrupted = tmp_path / "corrupted.zip"
    with zipfile.ZipFile(original) as source, zipfile.ZipFile(corrupted, "w") as destination:
        for info in source.infolist():
            payload = source.read(info.filename)
            if info.filename == "database/knowledge_builder.sqlite3":
                payload = payload[:-1] + bytes([payload[-1] ^ 1])
            destination.writestr(info, payload)

    with pytest.raises(BackupError, match="checksum failed"):
        restore_backup(corrupted, tmp_path / "corrupt-restore")
