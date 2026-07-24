"""The committed fixture PDFs stay valid and match their generator."""

import importlib.util
from pathlib import Path

from app import config
from app.extraction import pdf_text

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
PDFS_DIR = FIXTURES_DIR / "pdfs"


def _load_make_fixtures():
    spec = importlib.util.spec_from_file_location(
        "make_fixtures", FIXTURES_DIR / "make_fixtures.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_scanned_like_fixture_has_no_usable_text_layer():
    result = pdf_text.read_pdf_text(PDFS_DIR / "scanned_like.pdf")
    assert result.page_count == 3
    total_text = sum(len(page.text) for page in result.pages)
    assert total_text < config.MIN_EXTRACTABLE_TEXT_CHARS  # trips FR-10


def test_empty_text_fixture_is_a_valid_near_blank_page():
    result = pdf_text.read_pdf_text(PDFS_DIR / "empty_text.pdf")
    assert result.page_count == 1
    total_text = sum(len(page.text) for page in result.pages)
    assert total_text < config.MIN_EXTRACTABLE_TEXT_CHARS


def test_committed_fixtures_match_their_generator():
    make_fixtures = _load_make_fixtures()
    for filename, builder in make_fixtures.FIXTURES.items():
        committed = (PDFS_DIR / filename).read_bytes()
        assert committed == builder(), f"{filename} is stale; rerun make_fixtures.py"
