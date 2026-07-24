from pathlib import Path

import pytest

from app.provenance.metadata import MetadataSuggestion, extract_metadata
from app.provenance.official_sources import (
    canonical_vendor,
    is_official_support_page,
    is_official_vendor_url,
    support_page_error,
)
from app.provenance.search import build_query, discover_support_pages


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("ASUSTeK", "ASUS"),
        ("Micro-Star International", "MSI"),
        ("Gigabyte Technology", "GIGABYTE"),
        ("ASRock", "ASRock"),
        ("Unknown", None),
    ],
)
def test_canonical_vendor_aliases(value, expected):
    assert canonical_vendor(value) == expected


@pytest.mark.parametrize(
    ("vendor", "url"),
    [
        ("ASUS", "https://www.asus.com/us/supportonly/rog-x870e/helpdesk_manual/"),
        (
            "ASUS",
            "https://www.asus.com/motherboards-components/motherboards/"
            "tuf-gaming/tuf-gaming-b760m-plus/helpdesk_manual?model2Name=TUF-GAMING-B760M-PLUS",
        ),
        ("MSI", "https://www.msi.com/Motherboard/MPG-X670E-CARBON-WIFI/support"),
        (
            "MSI",
            "https://www.msi.com/support/download-manual/MPG-Z790-EDGE-TI-MAX-WIFI",
        ),
        (
            "GIGABYTE",
            "https://www.gigabyte.com/Motherboard/B650M-AORUS-ELITE-AX/support#support-manual",
        ),
        ("ASRock", "https://www.asrock.com/mb/AMD/X870E%20Taichi/index.asp#Manual"),
    ],
)
def test_recognizes_official_support_pages(vendor, url):
    assert is_official_vendor_url(vendor, url)
    assert is_official_support_page(vendor, url)
    assert support_page_error(vendor, url) is None


@pytest.mark.parametrize(
    "url",
    [
        "http://www.asus.com/supportonly/board/helpdesk_manual/",
        "https://asus.com.evil.example/supportonly/board/helpdesk_manual/",
        "https://user:pass@asus.com/supportonly/board/helpdesk_manual/",
        "https://asus.com:444/supportonly/board/helpdesk_manual/",
        "https://asus.com:notaport/supportonly/board/helpdesk_manual/",
        "https://asus.com/product/board",
        "https://asus.com/motherboards-components/motherboards/tuf-gaming/board/helpdesk_download",
    ],
)
def test_rejects_unsafe_or_non_support_urls(url):
    assert not is_official_support_page("ASUS", url)
    assert support_page_error("ASUS", url)


def test_extracts_cover_metadata(tmp_path, pdf_bytes_factory):
    path = tmp_path / "ROG_X870E_Series_BIOS_Manual.pdf"
    path.write_bytes(
        pdf_bytes_factory(
            texts=[
                "ASUS\nROG X870E Series BIOS Manual\nEnglish Edition\nVersion 1.2",
                "BIOS setup reference",
            ]
        )
    )
    result = extract_metadata(Path(path), path.name)
    assert result.vendor == "ASUS"
    assert result.document_title == "ROG X870E Series BIOS Manual"
    assert result.document_version == "1.2"
    assert result.chipset == "X870E"
    assert result.language == "en"


def test_search_filters_nonofficial_and_ranks_official_candidate():
    metadata = MetadataSuggestion(
        vendor="MSI",
        model_name="MPG X670E CARBON WIFI",
        document_title="MPG X670E CARBON WIFI User Guide",
        document_version="",
        published_on="",
        language="en",
        board_revision="",
        chipset="X670E",
    )
    official = "https://www.msi.com/Motherboard/MPG-X670E-CARBON-WIFI/support"
    malicious = "https://www.msi.com.evil.example/Motherboard/MPG-X670E/support"
    body = f"""
    <a class="result__a" href="//duckduckgo.com/l/?uddg={malicious}">Fake result</a>
    <a class="result__snippet">not official</a>
    <a class="result__a" href="//duckduckgo.com/l/?uddg={official}">
      MPG X670E CARBON WIFI Support
    </a>
    <a class="result__snippet">Official manual and document support page for X670E.</a>
    """
    query, candidates = discover_support_pages(metadata, fetch=lambda _url: body)
    assert "site:msi.com" in query
    assert [candidate.url for candidate in candidates] == [official]
    assert candidates[0].confidence >= 80
    assert "official vendor domain" in candidates[0].evidence


def test_generic_series_query_uses_document_title():
    metadata = MetadataSuggestion(
        vendor="ASUS",
        model_name="",
        document_title="PRIME PROART TUF GAMING INTEL 800 Series BIOS Manual",
        document_version="",
        published_on="",
        language="en",
        board_revision="",
        chipset="Intel 800",
    )
    assert metadata.document_title in build_query(metadata)
