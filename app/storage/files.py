"""Local-disk storage for uploaded manual PDFs (Implementation Plan sections 7-8).

Stored filenames are always server-generated; user-provided names are never
used as filesystem paths (FR-5, NFR-3).
"""

import hashlib
import uuid
from pathlib import Path
from typing import BinaryIO

from app import config

_CHUNK_SIZE = 1024 * 1024


class FileTooLargeError(Exception):
    pass


def stage_upload(stream: BinaryIO, max_bytes: int) -> tuple[Path, str, int]:
    """Stream an upload into a temp file under the uploads directory.

    Returns (temp_path, sha256_hex, size_bytes). Raises FileTooLargeError and
    removes the temp file if the stream exceeds max_bytes.
    """
    config.ensure_data_dirs()
    temp_path = config.UPLOADS_DIR / f"upload-{uuid.uuid4().hex}.tmp"
    digest = hashlib.sha256()
    size = 0
    too_large = False
    with temp_path.open("wb") as out:
        while chunk := stream.read(_CHUNK_SIZE):
            size += len(chunk)
            if size > max_bytes:
                too_large = True
                break
            digest.update(chunk)
            out.write(chunk)
    if too_large:
        temp_path.unlink(missing_ok=True)
        raise FileTooLargeError
    return temp_path, digest.hexdigest(), size


def manual_file_path(stored_filename: str) -> Path:
    return config.UPLOADS_DIR / stored_filename


def evidence_source_file_path(stored_filename: str) -> Path:
    return config.EVIDENCE_SOURCES_DIR / stored_filename


def promote_upload(temp_path: Path, stored_filename: str) -> Path:
    final_path = manual_file_path(stored_filename)
    temp_path.replace(final_path)
    return final_path


def promote_evidence_source_upload(temp_path: Path, stored_filename: str) -> Path:
    final_path = evidence_source_file_path(stored_filename)
    temp_path.replace(final_path)
    return final_path


def delete_evidence_source_file(stored_filename: str) -> bool:
    path = evidence_source_file_path(stored_filename)
    existed = path.is_file()
    path.unlink(missing_ok=True)
    return existed


def discard_upload(temp_path: Path) -> None:
    temp_path.unlink(missing_ok=True)


def delete_manual_file(stored_filename: str) -> bool:
    """Remove a stored manual file; returns False if it was already missing."""
    path = manual_file_path(stored_filename)
    existed = path.is_file()
    path.unlink(missing_ok=True)
    return existed


def stage_file_deletion(stored_filename: str) -> Path | None:
    """Rename the stored file aside so its deletion can still be undone.

    Returns the staged path, or None if the file was already missing.
    """
    path = manual_file_path(stored_filename)
    if not path.is_file():
        return None
    staged_path = path.with_name(path.name + ".deleting")
    path.replace(staged_path)
    return staged_path


def restore_staged_deletion(staged_path: Path, stored_filename: str) -> None:
    staged_path.replace(manual_file_path(stored_filename))


def finalize_staged_deletion(staged_path: Path) -> None:
    staged_path.unlink()
