"""Reusable PDF page-text reader (Implementation Plan sections 7-8).

Pure extraction layer: a file path in, data structures out. Per plan
section 15 (M2) it exposes per-page lines with font metadata so the future
heuristic parser can detect visually distinguished headings; interpreting
that metadata is the parser's job, not this module's. No database access,
no business logic, no knowledge of BIOS settings or drafts.
"""

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber
from pdfminer.pdfdocument import PDFEncryptionError
from pdfplumber.page import Page

REASON_MISSING_FILE = "missing_file"
REASON_ENCRYPTED = "encrypted"
REASON_UNREADABLE = "unreadable"
REASON_NO_PAGES = "no_pages"


class PdfReadError(Exception):
    """PDF could not be read; message is safe to show the reviewer."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True)
class PageLine:
    text: str
    font_name: str  # dominant font among the line's characters; "" if unknown
    font_size: float  # largest character size on the line; 0.0 if unknown


@dataclass(frozen=True)
class PageText:
    page_number: int  # 1-based, matching settings.page_number in plan section 9
    text: str
    lines: list[PageLine] = field(default_factory=list)  # ordered top-to-bottom


@dataclass(frozen=True)
class PdfText:
    page_count: int
    pages: list[PageText]  # ordered by ascending page_number


def read_pdf_text(path: Path) -> PdfText:
    """Read every page of the PDF at `path` and return its text per page.

    Pages with no extractable text yield an empty string; judging that (for
    example the scanned-PDF threshold) is the caller's concern.

    Raises PdfReadError with a machine-usable `reason` for a missing file,
    an encrypted document, an unreadable/corrupted document, or a document
    with no pages.
    """
    if not path.is_file():
        raise PdfReadError(REASON_MISSING_FILE, "The stored PDF file is missing.")
    try:
        with pdfplumber.open(path) as pdf:
            pages = [
                PageText(
                    page_number=number,
                    text=page.extract_text() or "",
                    lines=_extract_lines(page),
                )
                for number, page in enumerate(pdf.pages, start=1)
            ]
    except Exception as error:
        if _is_encryption_error(error):
            raise PdfReadError(
                REASON_ENCRYPTED, "The PDF is encrypted and cannot be read."
            ) from None
        raise PdfReadError(REASON_UNREADABLE, "The file could not be read as a PDF.") from None
    if not pages:
        raise PdfReadError(REASON_NO_PAGES, "The PDF contains no pages.")
    return PdfText(page_count=len(pages), pages=pages)


def _extract_lines(page: Page) -> list[PageLine]:
    lines = []
    for raw_line in page.extract_text_lines(return_chars=True):
        chars = raw_line.get("chars") or []
        font_names = Counter(char["fontname"] for char in chars)
        lines.append(
            PageLine(
                text=raw_line.get("text", ""),
                font_name=font_names.most_common(1)[0][0] if font_names else "",
                font_size=round(max(char["size"] for char in chars), 2) if chars else 0.0,
            )
        )
    return lines


def _is_encryption_error(error: Exception) -> bool:
    # pdfplumber wraps pdfminer errors (original in args/__context__), so look
    # through the wrapper as well as at the error itself.
    candidates = (error, error.__cause__, error.__context__, *error.args)
    return any(isinstance(candidate, PDFEncryptionError) for candidate in candidates)
