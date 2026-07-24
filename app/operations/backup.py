"""Verified TuneForge data archives with non-destructive restore."""

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import time
import zipfile
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from app import config

FORMAT_VERSION = 1
DATABASE_MEMBER = "database/knowledge_builder.sqlite3"
MANIFEST_MEMBER = "manifest.json"
MAX_ARCHIVE_FILES = 20_000
MAX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 10 * 1024 * 1024
ARCHIVE_COPY_CHUNK_BYTES = 1024 * 1024
ProgressCallback = Callable[[str], None]


class BackupError(RuntimeError):
    pass


@dataclass(frozen=True)
class BackupResult:
    archive_path: Path
    archive_sha256: str
    database_revision: str
    file_count: int
    uncompressed_bytes: int


@dataclass(frozen=True)
class RestoreResult:
    target_dir: Path
    database_revision: str
    file_count: int


@dataclass(frozen=True)
class BackupInspection:
    database_revision: str
    created_at: str
    app_version: str
    file_count: int
    uncompressed_bytes: int


def create_backup(
    data_dir: Path,
    output_path: Path,
    *,
    progress: ProgressCallback | None = None,
) -> BackupResult:
    started_at = time.perf_counter()
    data_dir = data_dir.resolve()
    output_path = output_path.resolve()
    _progress(progress, f"Starting backup: {output_path}")
    source_database = data_dir / "knowledge_builder.sqlite3"
    if not source_database.is_file():
        raise BackupError("TuneForge database was not found in the selected data directory.")
    if output_path.exists():
        raise BackupError("Backup output already exists; choose a new archive name.")
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise BackupError("Backup output directory could not be created.") from error

    snapshot: Path | None = None
    partial: Path | None = None
    try:
        snapshot = _temporary_path(
            output_path.parent,
            prefix=".tuneforge-database-",
            suffix=".sqlite3",
        )
        partial = _temporary_path(
            output_path.parent,
            prefix=".tuneforge-archive-",
            suffix=".zip",
        )
        stage_started = time.perf_counter()
        _progress(progress, "Snapshotting database...")
        _snapshot_database(source_database, snapshot)
        _progress(
            progress,
            f"Database snapshot complete ({time.perf_counter() - stage_started:.2f}s)",
        )
        stage_started = time.perf_counter()
        _progress(progress, "Reading database inventory...")
        revision, referenced_files = _database_inventory(snapshot)
        _progress(
            progress,
            "Database inventory complete "
            f"({len(referenced_files)} referenced files, "
            f"{time.perf_counter() - stage_started:.2f}s)",
        )
        members: list[tuple[str, Path]] = [(DATABASE_MEMBER, snapshot)]
        for kind, stored_filename in referenced_files:
            _validate_stored_filename(stored_filename)
            source = data_dir / kind / stored_filename
            if not source.is_file():
                raise BackupError(
                    f"Referenced {kind} file is missing: {stored_filename}. No backup was created."
                )
            members.append((f"{kind}/{stored_filename}", source))

        stage_started = time.perf_counter()
        _progress(progress, "Hashing archive members and generating manifest...")
        file_records = [_file_record(member, source) for member, source in members]
        manifest = {
            "format": "tuneforge-data-backup",
            "format_version": FORMAT_VERSION,
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "app_version": config.APP_VERSION,
            "database_revision": revision,
            "sessions_included": False,
            "files": file_records,
        }
        _progress(
            progress,
            f"Manifest generated ({time.perf_counter() - stage_started:.2f}s)",
        )
        stage_started = time.perf_counter()
        _progress(progress, "Beginning archive creation...")
        with zipfile.ZipFile(
            partial, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
        ) as archive:
            archive.writestr(
                MANIFEST_MEMBER,
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            )
            for record, (member, source) in zip(file_records, members, strict=True):
                member_started = time.perf_counter()
                _progress(
                    progress,
                    f"Writing {member} ({source.stat().st_size:,} bytes)...",
                )
                _write_archive_member(
                    archive,
                    member,
                    source,
                    expected_sha256=str(record["sha256"]),
                )
                _progress(
                    progress,
                    f"Finished {member} ({time.perf_counter() - member_started:.2f}s)",
                )
        _progress(
            progress,
            f"Archive stream finalized ({time.perf_counter() - stage_started:.2f}s)",
        )
        verification_started = time.perf_counter()
        _progress(progress, "Verifying staged archive contents...")
        _verify_staged_archive(partial)
        _progress(
            progress,
            "Staged archive verification complete "
            f"({time.perf_counter() - verification_started:.2f}s)",
        )
        os.replace(partial, output_path)
        _progress(progress, "Archive published atomically.")
    except BackupError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile) as error:
        raise BackupError(
            "Backup archive creation failed; no final archive was published."
        ) from error
    finally:
        if snapshot is not None:
            snapshot.unlink(missing_ok=True)
        if partial is not None:
            partial.unlink(missing_ok=True)

    checksum_started = time.perf_counter()
    _progress(progress, "Verifying final archive checksum...")
    result = BackupResult(
        archive_path=output_path,
        archive_sha256=_sha256(output_path),
        database_revision=revision,
        file_count=len(file_records),
        uncompressed_bytes=sum(record["size_bytes"] for record in file_records),
    )
    _progress(
        progress,
        f"Final archive checksum complete ({time.perf_counter() - checksum_started:.2f}s)",
    )
    _progress(
        progress,
        f"Backup complete ({time.perf_counter() - started_at:.2f}s)",
    )
    return result


def restore_backup(archive_path: Path, target_dir: Path) -> RestoreResult:
    archive_path = archive_path.resolve()
    target_dir = target_dir.resolve()
    if not archive_path.is_file():
        raise BackupError("Backup archive was not found.")
    if target_dir.exists():
        raise BackupError("Restore target already exists; choose a new empty path.")
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target_dir.name}.restore-", dir=target_dir.parent))
    try:
        with zipfile.ZipFile(archive_path) as archive:
            manifest = _read_manifest(archive)
            records = _validate_archive_inventory(archive, manifest)
            for record in records:
                member = record["path"]
                destination = _restore_destination(staging, member)
                destination.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                with archive.open(member) as source, destination.open("wb") as output:
                    while chunk := source.read(1024 * 1024):
                        digest.update(chunk)
                        output.write(chunk)
                if digest.hexdigest() != record["sha256"]:
                    raise BackupError(f"Backup checksum failed for {member}.")
            (staging / "backup-manifest.json").write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        revision = _verify_restored_database(
            staging / "knowledge_builder.sqlite3", manifest["database_revision"]
        )
        os.replace(staging, target_dir)
        return RestoreResult(
            target_dir=target_dir,
            database_revision=revision,
            file_count=len(records),
        )
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def inspect_backup(archive_path: Path) -> BackupInspection:
    """Validate archive structure and expose non-sensitive checkpoint metadata.

    This is intentionally cheaper than a restore drill: it verifies the manifest
    and member inventory, while restore remains responsible for hashing every
    extracted member and checking SQLite integrity.
    """

    try:
        with zipfile.ZipFile(archive_path.resolve()) as archive:
            manifest = _read_manifest(archive)
            records = _validate_archive_inventory(archive, manifest)
    except (OSError, zipfile.BadZipFile) as error:
        raise BackupError("Backup archive could not be inspected.") from error
    created_at = manifest.get("created_at")
    app_version = manifest.get("app_version")
    if not isinstance(created_at, str) or not isinstance(app_version, str):
        raise BackupError("Backup checkpoint metadata is invalid.")
    return BackupInspection(
        database_revision=str(manifest["database_revision"]),
        created_at=created_at,
        app_version=app_version,
        file_count=len(records),
        uncompressed_bytes=sum(int(record["size_bytes"]) for record in records),
    )


def _snapshot_database(source: Path, destination: Path) -> None:
    try:
        with closing(sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)) as source_db:
            with closing(sqlite3.connect(destination)) as destination_db:
                source_db.backup(destination_db)
                # Sessions are ephemeral security credentials, not durable
                # knowledge. A restored system always requires a fresh login.
                destination_db.execute("DELETE FROM auth_sessions")
                destination_db.commit()
                integrity = destination_db.execute("PRAGMA integrity_check").fetchone()
                if integrity != ("ok",):
                    raise BackupError("Database snapshot failed its integrity check.")
    except sqlite3.Error as error:
        raise BackupError("Database snapshot could not be created.") from error


def _database_inventory(database: Path) -> tuple[str, list[tuple[str, str]]]:
    try:
        with closing(
            sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
        ) as connection:
            revision_row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
            if revision_row is None:
                raise BackupError("Database migration revision is unavailable.")
            files = [
                ("uploads", row[0])
                for row in connection.execute(
                    "SELECT stored_filename FROM manuals ORDER BY id"
                ).fetchall()
            ]
            files.extend(
                ("evidence_sources", row[0])
                for row in connection.execute(
                    "SELECT stored_filename FROM evidence_sources ORDER BY id"
                ).fetchall()
            )
            return str(revision_row[0]), files
    except sqlite3.Error as error:
        raise BackupError("Database inventory could not be read.") from error


def _file_record(member: str, source: Path) -> dict[str, object]:
    return {
        "path": member,
        "size_bytes": source.stat().st_size,
        "sha256": _sha256(source),
    }


def _write_archive_member(
    archive: zipfile.ZipFile,
    member: str,
    source: Path,
    *,
    expected_sha256: str,
) -> None:
    """Stream one member with bounded memory and verify it did not change."""
    digest = hashlib.sha256()
    source_info = zipfile.ZipInfo.from_file(source, member)
    source_info.compress_type = archive.compression
    source_info._compresslevel = archive.compresslevel
    with source.open("rb") as input_stream, archive.open(source_info, "w") as output_stream:
        while chunk := input_stream.read(ARCHIVE_COPY_CHUNK_BYTES):
            digest.update(chunk)
            output_stream.write(chunk)
    if digest.hexdigest() != expected_sha256:
        raise BackupError(
            f"Source file changed while the backup was running: {member}. "
            "No backup was created."
        )


def _temporary_path(parent: Path, *, prefix: str, suffix: str) -> Path:
    try:
        handle = tempfile.NamedTemporaryFile(
            prefix=prefix,
            suffix=suffix,
            dir=parent,
            delete=False,
        )
    except OSError as error:
        raise BackupError("Backup staging files could not be created.") from error
    path = Path(handle.name)
    handle.close()
    return path


def _read_manifest(archive: zipfile.ZipFile) -> dict[str, object]:
    names = archive.namelist()
    if names.count(MANIFEST_MEMBER) != 1:
        raise BackupError("Backup must contain exactly one manifest.")
    if archive.getinfo(MANIFEST_MEMBER).file_size > MAX_MANIFEST_BYTES:
        raise BackupError("Backup manifest is too large.")
    try:
        value = json.loads(archive.read(MANIFEST_MEMBER))
    except (json.JSONDecodeError, UnicodeDecodeError, KeyError) as error:
        raise BackupError("Backup manifest is invalid.") from error
    if not isinstance(value, dict):
        raise BackupError("Backup manifest is invalid.")
    if (
        value.get("format") != "tuneforge-data-backup"
        or value.get("format_version") != FORMAT_VERSION
    ):
        raise BackupError("Backup format is not supported.")
    if not isinstance(value.get("database_revision"), str):
        raise BackupError("Backup database revision is invalid.")
    if value.get("sessions_included") is not False:
        raise BackupError("Backup session policy is invalid.")
    return value


def _verify_staged_archive(archive_path: Path) -> None:
    """Verify structure, CRC/decompression, and every manifest checksum."""
    try:
        with zipfile.ZipFile(archive_path) as archive:
            manifest = _read_manifest(archive)
            records = _validate_archive_inventory(archive, manifest)
            for record in records:
                digest = hashlib.sha256()
                with archive.open(str(record["path"])) as stream:
                    while chunk := stream.read(ARCHIVE_COPY_CHUNK_BYTES):
                        digest.update(chunk)
                if digest.hexdigest() != record["sha256"]:
                    raise BackupError(
                        f"Staged backup checksum failed for {record['path']}."
                    )
    except (OSError, RuntimeError, zipfile.BadZipFile) as error:
        raise BackupError("Staged backup archive could not be verified.") from error


def _validate_archive_inventory(
    archive: zipfile.ZipFile, manifest: dict[str, object]
) -> list[dict[str, object]]:
    raw_records = manifest.get("files")
    if not isinstance(raw_records, list) or not raw_records:
        raise BackupError("Backup file inventory is invalid.")
    if len(raw_records) > MAX_ARCHIVE_FILES:
        raise BackupError("Backup contains too many files.")
    records: list[dict[str, object]] = []
    expected_names = {MANIFEST_MEMBER}
    total_size = 0
    info_by_name = {info.filename: info for info in archive.infolist()}
    if len(info_by_name) != len(archive.infolist()):
        raise BackupError("Backup contains duplicate member names.")
    for raw in raw_records:
        if not isinstance(raw, dict):
            raise BackupError("Backup file inventory is invalid.")
        member = raw.get("path")
        size = raw.get("size_bytes")
        digest = raw.get("sha256")
        if not isinstance(member, str) or not _safe_member(member):
            raise BackupError("Backup contains an unsafe member path.")
        if not isinstance(size, int) or size < 0:
            raise BackupError("Backup contains an invalid file size.")
        if not isinstance(digest, str) or len(digest) != 64:
            raise BackupError("Backup contains an invalid checksum.")
        if member in expected_names:
            raise BackupError("Backup contains duplicate inventory paths.")
        info = info_by_name.get(member)
        if info is None or info.is_dir() or info.file_size != size or _is_symlink(info):
            raise BackupError(f"Backup member metadata is invalid for {member}.")
        expected_names.add(member)
        total_size += size
        records.append({"path": member, "size_bytes": size, "sha256": digest})
    if DATABASE_MEMBER not in expected_names:
        raise BackupError("Backup database is missing.")
    if set(info_by_name) != expected_names:
        raise BackupError("Backup contains files outside its manifest.")
    if total_size > MAX_UNCOMPRESSED_BYTES:
        raise BackupError("Backup exceeds the restore size limit.")
    return records


def _restore_destination(staging: Path, member: str) -> Path:
    if member == DATABASE_MEMBER:
        return staging / "knowledge_builder.sqlite3"
    path = PurePosixPath(member)
    if path.parts[0] not in {"uploads", "evidence_sources"} or len(path.parts) != 2:
        raise BackupError("Backup contains an unsupported member path.")
    return staging / path.parts[0] / path.parts[1]


def _verify_restored_database(database: Path, expected_revision: str) -> str:
    try:
        with closing(sqlite3.connect(database)) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
            sessions = connection.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()
    except sqlite3.Error as error:
        raise BackupError("Restored database could not be verified.") from error
    if integrity != ("ok",) or revision != (expected_revision,) or sessions != (0,):
        raise BackupError("Restored database failed integrity, revision, or session checks.")
    return expected_revision


def _validate_stored_filename(value: str) -> None:
    if not value or Path(value).name != value or "/" in value or "\\" in value:
        raise BackupError("Database contains an unsafe stored filename.")


def _safe_member(value: str) -> bool:
    path = PurePosixPath(value)
    return not path.is_absolute() and ".." not in path.parts and "\\" not in value


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return (info.external_attr >> 16) & 0o170000 == 0o120000


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _progress(callback: ProgressCallback | None, message: str) -> None:
    if callback is not None:
        callback(message)
