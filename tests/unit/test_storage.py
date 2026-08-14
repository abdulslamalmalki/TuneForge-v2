import hashlib
import io

import pytest

from app import config
from app.storage import files


def test_stage_upload_hashes_and_sizes_content():
    payload = b"hello knowledge builder"
    temp_path, sha256, size = files.stage_upload(io.BytesIO(payload), max_bytes=1024)
    assert temp_path.parent == config.UPLOADS_DIR
    assert temp_path.read_bytes() == payload
    assert sha256 == hashlib.sha256(payload).hexdigest()
    assert size == len(payload)


def test_stage_upload_is_stable_for_identical_content():
    payload = b"same bytes"
    _, first_sha, _ = files.stage_upload(io.BytesIO(payload), max_bytes=1024)
    _, second_sha, _ = files.stage_upload(io.BytesIO(payload), max_bytes=1024)
    assert first_sha == second_sha


def test_stage_upload_rejects_oversized_stream_and_cleans_up():
    with pytest.raises(files.FileTooLargeError):
        files.stage_upload(io.BytesIO(b"x" * 100), max_bytes=10)
    assert list(config.UPLOADS_DIR.glob("*.tmp")) == []


def test_promote_moves_temp_file_into_place():
    temp_path, _, _ = files.stage_upload(io.BytesIO(b"content"), max_bytes=1024)
    final_path = files.promote_upload(temp_path, "manual_1.pdf")
    assert not temp_path.exists()
    assert final_path == config.UPLOADS_DIR / "manual_1.pdf"
    assert final_path.read_bytes() == b"content"


def test_delete_manual_file_reports_missing_file():
    files.promote_upload(
        files.stage_upload(io.BytesIO(b"content"), max_bytes=1024)[0], "manual_2.pdf"
    )
    assert files.delete_manual_file("manual_2.pdf") is True
    assert files.delete_manual_file("manual_2.pdf") is False


@pytest.mark.parametrize(
    "filename",
    ("../knowledge_builder.sqlite3", "manual_0.pdf", "manual_1.pdf.exe", "/manual_1.pdf"),
)
def test_server_generated_manual_paths_reject_traversal_and_invalid_names(filename):
    with pytest.raises(ValueError, match="server-generated"):
        files.manual_file_path(filename)


def test_promote_rejects_a_staged_path_outside_the_upload_directory(tmp_path):
    outside = tmp_path / "upload-attacker.tmp"
    outside.write_bytes(b"content")
    with pytest.raises(ValueError, match="staged upload"):
        files.promote_upload(outside, "manual_1.pdf")
