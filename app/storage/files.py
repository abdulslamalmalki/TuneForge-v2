"""Local-disk storage for uploaded manual PDFs (Implementation Plan sections 7-8).

Stored filenames are always server-generated; user-provided names are never
used as filesystem paths (FR-5, NFR-3).
"""

import hashlib
import re
import uuid
from pathlib import Path
from typing import BinaryIO

from app import config

_CHUNK_SIZE = 1024 * 1024
_MANUAL_FILENAME = re.compile(r"manual_[1-9][0-9]*\.pdf")
_EVIDENCE_FILENAME = re.compile(r"evidence_source_[1-9][0-9]*\.pdf")


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
    return _stored_path(config.UPLOADS_DIR, stored_filename, _MANUAL_FILENAME)


def evidence_source_file_path(stored_filename: str) -> Path:
    return _stored_path(config.EVIDENCE_SOURCES_DIR, stored_filename, _EVIDENCE_FILENAME)


def promote_upload(temp_path: Path, stored_filename: str) -> Path:
    _require_upload_temp(temp_path)
    final_path = manual_file_path(stored_filename)
    temp_path.replace(final_path)
    return final_path


def promote_evidence_source_upload(temp_path: Path, stored_filename: str) -> Path:
    _require_upload_temp(temp_path)
    final_path = evidence_source_file_path(stored_filename)
    temp_path.replace(final_path)
    return final_path


def delete_evidence_source_file(stored_filename: str) -> bool:
    path = evidence_source_file_path(stored_filename)
    existed = path.is_file()
    path.unlink(missing_ok=True)
    return existed


def discard_upload(temp_path: Path) -> None:
    _require_upload_temp(temp_path)
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
    expected = staged_path.name.removesuffix(".deleting")
    if not staged_path.name.endswith(".deleting"):
        raise ValueError("Invalid staged manual deletion path.")
    if staged_path != manual_file_path(expected).with_name(expected + ".deleting"):
        raise ValueError("Invalid staged manual deletion path.")
    staged_path.unlink()


def has_pdf_header(path: Path) -> bool:
    """Require the PDF signature in the first 1,024 bytes permitted by the format."""

    with path.open("rb") as stream:
        return b"%PDF-" in stream.read(1024)


def _stored_path(root: Path, stored_filename: str, pattern: re.Pattern[str]) -> Path:
    if pattern.fullmatch(stored_filename) is None:
        raise ValueError("Invalid server-generated stored filename.")
    return root / stored_filename


def _require_upload_temp(path: Path) -> None:
    expected_parent = config.UPLOADS_DIR.resolve()
    if path.parent.resolve() != expected_parent or not (
        path.name.startswith("upload-") and path.name.endswith(".tmp")
    ):
        raise ValueError("Invalid staged upload path.")
