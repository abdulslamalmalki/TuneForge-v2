"""Regenerate the committed fixture PDFs (Implementation Plan section 14).

Run from the knowledge-builder directory:

    python tests/fixtures/make_fixtures.py

Output is deterministic so regeneration never produces spurious diffs.
Fixtures 1 (basic_manual.pdf), 2 (no_descriptions.pdf), and 4 (noisy.pdf)
are parser fixtures with real font metadata: bold 12pt headings over 10pt
body text so rule 2's visual path fires exactly as it does on real manuals.
Fixtures 3 (scanned_like.pdf) and 5 (empty_text.pdf) exercise the reader.
"""

from pathlib import Path

PDFS_DIR = Path(__file__).parent / "pdfs"

# (font resource name, base font) pairs available to _text_pdf pages.
_FONTS = {"body": ("F1", "Helvetica"), "bold": ("F2", "Helvetica-Bold")}


def _assemble(objects: list[bytes]) -> bytes:
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


def _stream_object(params: bytes, data: bytes) -> bytes:
    return (
        b"<< "
        + params
        + b" /Length "
        + str(len(data)).encode()
        + b" >>\nstream\n"
        + data
        + b"\nendstream"
    )


Line = tuple[str, str, int]  # (text, font key in _FONTS, size in points)


def _text_pdf(pages: list[list[Line]]) -> bytes:
    """Assemble a PDF from pages of (text, font, size) lines, top to bottom."""
    page_count = len(pages)
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>"]
    kids = " ".join(f"{3 + i} 0 R" for i in range(page_count))
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {page_count} >>".encode())
    font_numbers = {key: 3 + page_count + i for i, key in enumerate(_FONTS)}
    font_resources = " ".join(
        f"/{_FONTS[key][0]} {number} 0 R" for key, number in font_numbers.items()
    )
    first_content = 3 + page_count + len(_FONTS)
    for index in range(page_count):
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << {font_resources} >> >> "
            f"/Contents {first_content + index} 0 R >>".encode()
        )
    for key in _FONTS:
        objects.append(f"<< /Type /Font /Subtype /Type1 /BaseFont /{_FONTS[key][1]} >>".encode())
    for lines in pages:
        parts = ["BT 72 720 Td"]
        for index, (text, font_key, size) in enumerate(lines):
            if index:
                parts.append("0 -24 Td")
            escaped = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
            parts.append(f"/{_FONTS[font_key][0]} {size} Tf ({escaped}) Tj")
        parts.append("ET")
        objects.append(_stream_object(b"", " ".join(parts).encode()))
    return _assemble(objects)


def build_basic_manual() -> bytes:
    """Fixture 1: a BIOS-chapter-like manual — bold headings, descriptions,
    and "Configuration options" lines (plan section 14)."""
    return _text_pdf(
        [
            [
                ("Ai Overclock Tuner", "bold", 12),
                ("Allows you to select the memory overclocking profile.", "body", 10),
                ("Configuration options: [Auto] [Manual] [XMP]", "body", 10),
                ("DRAM Frequency", "bold", 12),
                ("Sets the operating frequency of the installed memory.", "body", 10),
                ("Configuration options: [Auto] [DDR5-4800] [DDR5-5600]", "body", 10),
            ],
            [
                ("CPU Core Ratio", "bold", 12),
                ("Adjusts the multiplier applied to the base clock.", "body", 10),
                ("Precision Boost Overdrive", "bold", 12),
                ("Extends the processor boost limits beyond stock values.", "body", 10),
                ("Use with adequate cooling installed.", "body", 10),
            ],
            [
                ("Secure Boot Control", "bold", 12),
                (
                    "Enables or disables secure boot validation for the operating system.",
                    "body",
                    10,
                ),
                ("Configuration options: [Enabled] [Disabled]", "body", 10),
            ],
        ]
    )


def build_no_descriptions() -> bytes:
    """Fixture 2: setting-like headings without prose (plan section 14);
    every candidate keeps an empty description (FR-14)."""
    return _text_pdf(
        [
            [
                ("CPU Load-line Calibration", "bold", 12),
                ("DRAM Voltage", "bold", 12),
                ("Ai Tweaker Mode", "bold", 12),
            ],
            [
                ("Boot Logo Display", "bold", 12),
                ("Fast Boot Control", "bold", 12),
            ],
        ]
    )


def build_noisy() -> bytes:
    """Fixture 4: ToC, running headers/footers, a flattened table, a section
    heading, and a copyright line mixed with real-looking settings."""
    return _text_pdf(
        [
            [
                ("BIOS Setup", "bold", 14),
                ("Contents", "body", 10),
                ("Advanced Menu......................12", "body", 10),
                ("OC Tweaker Screen..................18", "body", 10),
                ("Security Screen....................24", "body", 10),
                ("- 1 -", "body", 10),
            ],
            [
                ("BIOS Setup", "body", 10),
                ("CPU Ratio Apply Mode", "bold", 12),
                ("Selects how the ratio multiplier is applied to the cores.", "body", 10),
                ("Pin Signal Voltage Notes", "body", 10),
                ("1 VCC 3.3V primary supply rail for the module.", "body", 10),
                ("DRAM Timing Mode", "bold", 12),
                ("Selects link or unlink timing control for the memory channels.", "body", 10),
                ("- 2 -", "body", 10),
            ],
            [
                ("BIOS Setup", "body", 10),
                ("2.3 Advanced Voltage Settings", "bold", 14),
                ("CPU Core Voltage", "bold", 12),
                ("Sets the primary voltage supplied to the processor cores.", "body", 10),
                ("Copyright 2026 TuneForge Example Press", "body", 10),
                ("- 3 -", "body", 10),
            ],
        ]
    )


def build_scanned_like() -> bytes:
    """Fixture 3: three pages containing only an image, no text layer."""
    page_count = 3
    image_number = 3 + page_count
    content_number = image_number + 1
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>"]
    kids = " ".join(f"{3 + i} 0 R" for i in range(page_count))
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {page_count} >>".encode())
    for _ in range(page_count):
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /XObject << /Im0 {image_number} 0 R >> >> "
            f"/Contents {content_number} 0 R >>".encode()
        )
    # 8x8 8-bit grayscale gradient, uncompressed and deterministic.
    pixels = bytes(range(0, 256, 4))
    objects.append(
        _stream_object(
            b"/Type /XObject /Subtype /Image /Width 8 /Height 8"
            b" /ColorSpace /DeviceGray /BitsPerComponent 8",
            pixels,
        )
    )
    objects.append(_stream_object(b"", b"q 400 0 0 400 100 200 cm /Im0 Do Q"))
    return _assemble(objects)


def build_empty_text() -> bytes:
    """Fixture 5: a valid PDF with one near-blank page (a lone page number)."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        _stream_object(b"", b"BT /F1 8 Tf 300 30 Td (1) Tj ET"),
    ]
    return _assemble(objects)


FIXTURES = {
    "basic_manual.pdf": build_basic_manual,
    "no_descriptions.pdf": build_no_descriptions,
    "scanned_like.pdf": build_scanned_like,
    "noisy.pdf": build_noisy,
    "empty_text.pdf": build_empty_text,
}


def main() -> None:
    PDFS_DIR.mkdir(parents=True, exist_ok=True)
    for filename, builder in FIXTURES.items():
        path = PDFS_DIR / filename
        path.write_bytes(builder())
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
