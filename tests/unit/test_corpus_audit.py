"""Staged manual corpus audit utility."""

from pathlib import Path

from tools.audit_manual_corpus import (
    audit_corpus,
    build_report,
    infer_identity,
    sample_page_indexes,
)


def test_identity_inference_covers_series_and_document_types():
    assert infer_identity("Asus_AMD_800_Series_BIOS_manual.pdf") == (
        "bios_setup_guide",
        "AMD",
        ["AMD 800 Series"],
    )
    document_type, platform, chipsets = infer_identity("mb_manual_Z790_BIOS_e.pdf")
    assert document_type == "bios_setup_guide"
    assert platform == "Intel"
    assert chipsets == ["Z790"]
    assert infer_identity(
        "Software_BIOS Setup Guide_English.pdf",
        content="AMD 800 Series Motherboard Software/BIOS Setup Guide",
    ) == ("bios_setup_guide", "AMD", ["AMD 800 Series"])


def test_page_sampling_is_distributed_and_supports_exhaustive_mode():
    assert sample_page_indexes(100, 5) == [0, 25, 50, 74, 99]
    assert sample_page_indexes(3, 12) == [0, 1, 2]
    assert sample_page_indexes(3, 0) == [0, 1, 2]


def test_audit_detects_exact_duplicate_and_invalid_pdf(tmp_path, pdf_bytes_factory):
    corpus = tmp_path / "staging"
    vendor = corpus / "ASUS"
    vendor.mkdir(parents=True)
    pdf = pdf_bytes_factory(texts=["BIOS setup reference text. " * 20])
    (vendor / "AMD_800_BIOS.pdf").write_bytes(pdf)
    (vendor / "AMD_800_BIOS copy.pdf").write_bytes(pdf)
    (vendor / "broken.pdf").write_bytes(b"not a PDF")

    records = audit_corpus(corpus)
    assert len(records) == 3
    duplicate = next(record for record in records if record.duplicate_of is not None)
    assert duplicate.ingestion_priority == "exclude_duplicate"
    assert duplicate.filename == "AMD_800_BIOS copy.pdf"
    assert duplicate.duplicate_of == "ASUS/AMD_800_BIOS.pdf"
    invalid = next(record for record in records if record.filename == "broken.pdf")
    assert invalid.quality_status == "invalid_pdf"
    report = build_report(corpus, records)
    assert report["summary"]["unique_files"] == 2
    assert report["summary"]["duplicate_files"] == 1


def test_audit_paths_are_relative_and_vendor_scoped(tmp_path, pdf_bytes_factory):
    corpus = tmp_path / "staging"
    path = corpus / "Gigabyte" / "mb_manual_b550_bios.pdf"
    path.parent.mkdir(parents=True)
    path.write_bytes(pdf_bytes_factory(texts=["Firmware setting details. " * 20]))

    (record,) = audit_corpus(corpus)
    assert record.relative_path == "Gigabyte/mb_manual_b550_bios.pdf"
    assert record.vendor == "GIGABYTE"
    assert record.platform == "AMD"
    assert record.chipsets == ["B550"]
    assert Path(record.relative_path).is_absolute() is False
