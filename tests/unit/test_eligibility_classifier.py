from app.eligibility.classifier import (
    TYPE_BIOS_GUIDE,
    TYPE_FULL_MANUAL,
    TYPE_SOFTWARE_GUIDE,
    TYPE_UNKNOWN,
    classify_document,
)


def test_explicit_bios_guide_is_recommended_for_extraction():
    result = classify_document(
        filename="Intel700BIOS.pdf",
        document_title="Intel 700 series BIOS User Guide",
        page_count=71,
        sample_text=(
            "OC Menu\nDRAM Frequency\nSecure Boot\n"
            "Configuration options: [Auto] [Enabled] [Disabled]"
        ),
    )
    assert result.document_type == TYPE_BIOS_GUIDE
    assert result.eligible_recommendation is True
    assert result.confidence >= 90


def test_compact_vendor_bios_filename_is_recognized():
    result = classify_document(
        filename="AMDAM5BIOS.pdf",
        document_title="AMDAM5BIOS.pdf",
        page_count=49,
        sample_text="OC Menu\nDRAM Frequency",
    )
    assert result.document_type == TYPE_BIOS_GUIDE
    assert result.eligible_recommendation is True


def test_vendor_filename_with_manual_before_bios_is_recognized():
    result = classify_document(
        filename="mb_manual_intel800-bios_e_v2.pdf",
        document_title="mb_manual_intel800-bios_e_v2.pdf",
        page_count=30,
        sample_text="System Time\nFan Speed Control",
    )
    assert result.document_type == TYPE_BIOS_GUIDE
    assert result.eligible_recommendation is True
    assert result.confidence >= 90


def test_full_motherboard_manual_is_not_recommended():
    result = classify_document(
        filename="MPGX670ECARBONWIFI.pdf",
        document_title="MPG X670E CARBON WIFI Motherboard User Guide",
        page_count=534,
        sample_text=(
            "Package Contents\nSafety Information\nMotherboard Layout\n"
            "Installing the CPU\nRear I/O Panel"
        ),
    )
    assert result.document_type == TYPE_FULL_MANUAL
    assert result.eligible_recommendation is False
    assert result.confidence >= 90


def test_software_guide_is_separate_from_bios_guide():
    result = classify_document(
        filename="control-center.pdf",
        document_title="Motherboard Software User Guide",
        page_count=40,
        sample_text="Install the utility and launch the application.",
    )
    assert result.document_type == TYPE_SOFTWARE_GUIDE
    assert result.eligible_recommendation is False


def test_ambiguous_document_requires_reviewer_classification():
    result = classify_document(
        filename="reference.pdf",
        document_title="Technical Reference",
        page_count=20,
        sample_text="General information.",
    )
    assert result.document_type == TYPE_UNKNOWN
    assert result.eligible_recommendation is False
