"""Fail-fast checks for SQLite's database and transaction-sidecar storage."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


class SQLiteStorageError(RuntimeError):
    """The configured SQLite location cannot safely support writes."""


def verify_sqlite_storage(database_path: Path) -> Path:
    """Verify the database and its parent support SQLite transaction files.

    A writable database file is insufficient. SQLite also needs to create
    rollback-journal or WAL sidecar files in the database's parent directory.
    Test that capability before Alembic starts a migration so an environmental
    denial cannot surface halfway through batch table recreation.
    """

    database = database_path.expanduser().resolve()
    parent = database.parent
    if not parent.is_dir():
        raise SQLiteStorageError(
            f"SQLite database parent directory does not exist: {parent}"
        )
    if database.exists() and not database.is_file():
        raise SQLiteStorageError(
            f"SQLite database path is not a regular file: {database}"
        )

    if database.exists():
        try:
            descriptor = os.open(database, os.O_RDWR)
        except OSError as error:
            raise SQLiteStorageError(
                f"SQLite database file cannot be opened for writing: {database}"
            ) from error
        else:
            os.close(descriptor)

    descriptor: int | None = None
    probe_path: Path | None = None
    try:
        descriptor, raw_probe_path = tempfile.mkstemp(
            dir=parent,
            prefix=f"{database.name}-write-probe-",
            suffix=".journal",
        )
        probe_path = Path(raw_probe_path)
        os.write(descriptor, b"TuneForge SQLite write probe\n")
        os.fsync(descriptor)
    except OSError as error:
        raise SQLiteStorageError(
            "SQLite cannot create transaction sidecar files in the configured "
            f"database directory: {parent}. Database: {database}. "
            "Grant the process create/delete permission on the directory or "
            "set KNOWLEDGE_BUILDER_DATA_DIR to writable application storage."
        ) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if probe_path is not None:
            try:
                probe_path.unlink(missing_ok=True)
            except OSError as error:
                raise SQLiteStorageError(
                    "SQLite storage write probe could not be removed from "
                    f"{parent}. Verify delete permission before migrating."
                ) from error

    return database
