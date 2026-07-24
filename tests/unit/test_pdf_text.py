from pathlib import Path

import pytest

from app.extraction import pdf_text


def _write(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def _encrypted_pdf_bytes() -> bytes:
    """A one-page PDF with a standard /Encrypt dictionary whose owner/user
    hashes are garbage, so any password check fails."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>",
        b"<< /Filter /Standard /V 1 /R 2 /O <"
        + b"11" * 32
        + b"> /U <"
        + b"22" * 32
        + b"> /P -44 >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_position = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    doc_id = "33" * 16
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Encrypt 4 0 R "
        f"/ID [<{doc_id}> <{doc_id}>] >>\nstartxref\n{xref_position}\n%%EOF\n"
    ).encode()
    return bytes(out)


def _styled_pdf_bytes() -> bytes:
    """One page with a bold 16pt heading line above a regular 10pt body line."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R /F2 5 0 R >> >> /Contents 6 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
    ]
    stream = (
        b"BT /F2 16 Tf 72 720 Td (Ai Overclock Tuner) Tj ET\n"
        b"BT /F1 10 Tf 72 700 Td (Allows you to select the memory profile.) Tj ET"
    )
    objects.append(
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
    )
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_position = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_position}\n%%EOF\n"
    ).encode()
    return bytes(out)


def test_reads_single_page_with_text(tmp_path, pdf_bytes_factory):
    path = _write(tmp_path, "one.pdf", pdf_bytes_factory(texts=["Hello BIOS"]))
    result = pdf_text.read_pdf_text(path)
    assert result.page_count == 1
    assert result.pages[0].page_number == 1
    assert "Hello BIOS" in result.pages[0].text


def test_reads_multi_page_pdf_in_page_order(tmp_path, pdf_bytes_factory):
    texts = ["Alpha page", "Bravo page", "Charlie page"]
    path = _write(tmp_path, "multi.pdf", pdf_bytes_factory(texts=texts))
    result = pdf_text.read_pdf_text(path)
    assert result.page_count == 3
    assert [page.page_number for page in result.pages] == [1, 2, 3]
    for page, expected in zip(result.pages, texts, strict=True):
        assert expected in page.text


def test_blank_pages_yield_empty_text_and_no_lines(tmp_path, pdf_bytes_factory):
    path = _write(tmp_path, "blank.pdf", pdf_bytes_factory(2))
    result = pdf_text.read_pdf_text(path)
    assert result.page_count == 2
    assert [page.text for page in result.pages] == ["", ""]
    assert [page.lines for page in result.pages] == [[], []]


def test_lines_carry_font_metadata_in_top_to_bottom_order(tmp_path):
    path = _write(tmp_path, "styled.pdf", _styled_pdf_bytes())
    result = pdf_text.read_pdf_text(path)
    lines = result.pages[0].lines
    assert len(lines) == 2

    heading, body = lines
    assert heading.text == "Ai Overclock Tuner"
    assert "Bold" in heading.font_name
    assert heading.font_size == 16.0
    assert body.text == "Allows you to select the memory profile."
    assert "Bold" not in body.font_name
    assert body.font_size == 10.0


def test_factory_pdf_lines_match_page_text(tmp_path, pdf_bytes_factory):
    path = _write(tmp_path, "plain.pdf", pdf_bytes_factory(texts=["Alpha page", "Bravo page"]))
    result = pdf_text.read_pdf_text(path)
    assert [page.lines[0].text for page in result.pages] == ["Alpha page", "Bravo page"]
    assert all(page.lines[0].font_size == 12.0 for page in result.pages)


def test_output_is_deterministic(tmp_path, pdf_bytes_factory):
    path = _write(tmp_path, "same.pdf", pdf_bytes_factory(texts=["Alpha page", "Bravo page"]))
    assert pdf_text.read_pdf_text(path) == pdf_text.read_pdf_text(path)


def test_missing_file_is_a_structured_error(tmp_path):
    with pytest.raises(pdf_text.PdfReadError) as excinfo:
        pdf_text.read_pdf_text(tmp_path / "absent.pdf")
    assert excinfo.value.reason == pdf_text.REASON_MISSING_FILE
    assert excinfo.value.message


def test_pdf_with_no_pages_is_a_structured_error(tmp_path, pdf_bytes_factory):
    path = _write(tmp_path, "empty.pdf", pdf_bytes_factory(0))
    with pytest.raises(pdf_text.PdfReadError) as excinfo:
        pdf_text.read_pdf_text(path)
    assert excinfo.value.reason == pdf_text.REASON_NO_PAGES


def test_corrupted_pdf_is_a_structured_error(tmp_path):
    path = _write(tmp_path, "corrupt.pdf", b"%PDF-1.7\nnot really a pdf")
    with pytest.raises(pdf_text.PdfReadError) as excinfo:
        pdf_text.read_pdf_text(path)
    assert excinfo.value.reason == pdf_text.REASON_UNREADABLE
    assert excinfo.value.message


def test_encrypted_pdf_is_a_structured_error(tmp_path):
    path = _write(tmp_path, "encrypted.pdf", _encrypted_pdf_bytes())
    with pytest.raises(pdf_text.PdfReadError) as excinfo:
        pdf_text.read_pdf_text(path)
    assert excinfo.value.reason == pdf_text.REASON_ENCRYPTED
