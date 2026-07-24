"""Pinned parser outputs for the committed parser fixtures (plan section 14).

These tests lock the exact candidate set the current parser (rules 1-4,
PARSER_VERSION) produces for fixtures 1, 2, and 4. Any behavior change in
the heuristics must show up here as a failing diff — and per plan section
10 item 5, must be accompanied by a PARSER_VERSION bump.
"""

from pathlib import Path

from app.extraction import pdf_text, settings_parser
from app.extraction.settings_parser import SettingCandidate

PDFS_DIR = Path(__file__).parent.parent / "fixtures" / "pdfs"


def _candidates(filename: str) -> list[SettingCandidate]:
    return settings_parser.parse_settings(pdf_text.read_pdf_text(PDFS_DIR / filename))


def test_basic_manual_pinned_output():
    # Fixture 1: bold headings with descriptions; "Configuration options"
    # lines stay inside descriptions verbatim (plan section 10, rule 3).
    assert _candidates("basic_manual.pdf") == [
        SettingCandidate(
            extracted_name="Ai Overclock Tuner",
            extracted_description=(
                "Allows you to select the memory overclocking profile.\n"
                "Configuration options: [Auto] [Manual] [XMP]"
            ),
            page_number=1,
        ),
        SettingCandidate(
            extracted_name="DRAM Frequency",
            extracted_description=(
                "Sets the operating frequency of the installed memory.\n"
                "Configuration options: [Auto] [DDR5-4800] [DDR5-5600]"
            ),
            page_number=1,
        ),
        SettingCandidate(
            extracted_name="CPU Core Ratio",
            extracted_description="Adjusts the multiplier applied to the base clock.",
            page_number=2,
        ),
        SettingCandidate(
            extracted_name="Precision Boost Overdrive",
            extracted_description=(
                "Extends the processor boost limits beyond stock values.\n"
                "Use with adequate cooling installed."
            ),
            page_number=2,
        ),
        SettingCandidate(
            extracted_name="Secure Boot Control",
            extracted_description=(
                "Enables or disables secure boot validation for the operating system.\n"
                "Configuration options: [Enabled] [Disabled]"
            ),
            page_number=3,
        ),
    ]


def test_no_descriptions_pinned_output():
    # Fixture 2: heading-only pages; every candidate keeps an empty
    # description (FR-14) and its correct page number.
    assert _candidates("no_descriptions.pdf") == [
        SettingCandidate("CPU Load-line Calibration", "", 1),
        SettingCandidate("DRAM Voltage", "", 1),
        SettingCandidate("Ai Tweaker Mode", "", 1),
        SettingCandidate("Boot Logo Display", "", 2),
        SettingCandidate("Fast Boot Control", "", 2),
    ]


def test_noisy_pinned_output():
    # Fixture 4: rule 4 removes the ToC dot-leader lines, the "BIOS Setup"
    # running header (three page tops), the "- N -" footers, the "2.3 ..."
    # section heading, and the copyright line. "Pin Signal Voltage Notes"
    # is a flattened table header pinned deliberately: tables producing
    # garbled candidates is an accepted v0.1 limitation (plan section 10).
    assert _candidates("noisy.pdf") == [
        SettingCandidate(
            extracted_name="CPU Ratio Apply Mode",
            extracted_description="Selects how the ratio multiplier is applied to the cores.",
            page_number=2,
        ),
        SettingCandidate(
            extracted_name="Pin Signal Voltage Notes",
            extracted_description="1 VCC 3.3V primary supply rail for the module.",
            page_number=2,
        ),
        SettingCandidate(
            extracted_name="DRAM Timing Mode",
            extracted_description="Selects link or unlink timing control for the memory channels.",
            page_number=2,
        ),
        SettingCandidate(
            extracted_name="CPU Core Voltage",
            extracted_description="Sets the primary voltage supplied to the processor cores.",
            page_number=3,
        ),
    ]


def test_pinned_outputs_are_deterministic():
    for filename in ("basic_manual.pdf", "no_descriptions.pdf", "noisy.pdf"):
        assert _candidates(filename) == _candidates(filename), filename
