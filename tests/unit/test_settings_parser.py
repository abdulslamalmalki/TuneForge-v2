"""Parser foundation: accepts valid input, yields the documented empty result."""

import copy

import pytest

from app.extraction import pdf_text, settings_parser


def _pdf_text_from(tmp_path, pdf_bytes) -> pdf_text.PdfText:
    path = tmp_path / "input.pdf"
    path.write_bytes(pdf_bytes)
    return pdf_text.read_pdf_text(path)


def test_parser_returns_candidates_from_reader_output(tmp_path, pdf_bytes_factory):
    # Page 1 is a lone heading (empty description per FR-14); page 2's
    # "Configuration options" is not a heading, so it yields nothing.
    pdf = _pdf_text_from(
        tmp_path, pdf_bytes_factory(texts=["Ai Overclock Tuner", "Configuration options"])
    )
    result = settings_parser.parse_settings(pdf)
    assert result == [
        settings_parser.SettingCandidate(
            extracted_name="Ai Overclock Tuner", extracted_description="", page_number=1
        )
    ]


def test_parser_accepts_blank_pages(tmp_path, pdf_bytes_factory):
    pdf = _pdf_text_from(tmp_path, pdf_bytes_factory(3))
    assert settings_parser.parse_settings(pdf) == []


def test_parser_is_deterministic(tmp_path, pdf_bytes_factory):
    pdf = _pdf_text_from(tmp_path, pdf_bytes_factory(texts=["Some page text"]))
    assert settings_parser.parse_settings(pdf) == settings_parser.parse_settings(pdf)


def test_parser_does_not_modify_its_input(tmp_path, pdf_bytes_factory):
    pdf = _pdf_text_from(tmp_path, pdf_bytes_factory(texts=["Alpha", "Bravo"]))
    snapshot = copy.deepcopy(pdf)
    settings_parser.parse_settings(pdf)
    assert pdf == snapshot


def test_parser_version_constant_exists():
    assert isinstance(settings_parser.PARSER_VERSION, str)
    assert settings_parser.PARSER_VERSION


def _page(text: str) -> pdf_text.PageText:
    return pdf_text.PageText(page_number=1, text=text, lines=[])


class TestBiosSignals:
    """Rule 1: scope reduction via BIOS-chapter signal phrases."""

    def test_detects_each_signal_phrase(self):
        for text in (
            "Chapter 3: BIOS setup",
            "Entering the UEFI firmware interface",
            "Use the Setup Utility to change settings.",
            "Configuration options: [Enabled] [Disabled]",
        ):
            assert settings_parser.page_has_bios_signals(_page(text)), text

    def test_matching_is_case_insensitive(self):
        assert settings_parser.page_has_bios_signals(_page("bios overview"))
        assert settings_parser.page_has_bios_signals(_page("CONFIGURATION OPTIONS: [Auto]"))

    def test_ignores_signal_words_inside_other_words(self):
        assert not settings_parser.page_has_bios_signals(_page("the symbiosis of components"))

    def test_negative_for_unrelated_and_blank_pages(self):
        assert not settings_parser.page_has_bios_signals(_page("Installing the CPU cooler"))
        assert not settings_parser.page_has_bios_signals(_page(""))

    def test_is_deterministic(self):
        page = _page("BIOS settings reference")
        assert settings_parser.page_has_bios_signals(page) == settings_parser.page_has_bios_signals(
            page
        )

    def test_signal_page_without_headings_yields_no_candidates(self, tmp_path, pdf_bytes_factory):
        pdf = _pdf_text_from(tmp_path, pdf_bytes_factory(texts=["BIOS Configuration options"]))
        assert settings_parser.parse_settings(pdf) == []


def _line(text: str, font_name: str = "Helvetica", font_size: float = 10.0) -> pdf_text.PageLine:
    return pdf_text.PageLine(text=text, font_name=font_name, font_size=font_size)


def _page_with_lines(*lines: pdf_text.PageLine) -> pdf_text.PageText:
    # Two plain body lines anchor the page's dominant (body) font size at 10.
    body = (
        _line("This paragraph describes the previous setting in detail."),
        _line("It continues with more explanatory body text for the page."),
    )
    all_lines = list(body) + list(lines)
    return pdf_text.PageText(
        page_number=1, text="\n".join(line.text for line in all_lines), lines=all_lines
    )


class TestHeadingDetection:
    """Rule 2: heading detection via font metadata and lexical patterns."""

    def _headings(self, *lines):
        return [line.text for line in settings_parser.find_heading_lines(_page_with_lines(*lines))]

    def test_bold_line_is_a_heading(self):
        assert self._headings(_line("Ai Overclock Tuner", font_name="Helvetica-Bold")) == [
            "Ai Overclock Tuner"
        ]

    def test_larger_font_line_is_a_heading_even_without_bold(self):
        assert self._headings(_line("DRAM frequency selector", font_size=14.0)) == [
            "DRAM frequency selector"
        ]

    def test_body_sized_regular_lowercase_line_is_not_a_heading(self):
        assert self._headings(_line("and then press enter to continue")) == []

    def test_ordinary_body_sentences_are_not_headings(self):
        # The two anchor body lines themselves must not qualify.
        assert self._headings() == []

    def test_bold_line_ending_in_period_is_rejected(self):
        assert self._headings(_line("Note the following.", font_name="Helvetica-Bold")) == []

    def test_long_bold_line_is_rejected(self):
        long_text = "This bold line has far too many words to be a setting name"
        assert self._headings(_line(long_text, font_name="Helvetica-Bold")) == []

    def test_medium_weight_single_token_is_a_heading(self):
        assert self._headings(_line("AVX2", font_name="TTNormsPro-Medium", font_size=10)) == [
            "AVX2"
        ]

    def test_medium_weight_long_asus_label_is_a_heading(self):
        text = "DRAM Internal Write to Write Command Delay Time"
        assert self._headings(_line(text, font_name="TTNormsPro-Medium", font_size=10)) == [text]

    def test_medium_weight_asus_acronym_ending_in_period_is_a_heading(self):
        assert self._headings(_line("D.O.C.P.", font_name="TTNormsPro-Medium", font_size=10)) == [
            "D.O.C.P."
        ]

    @pytest.mark.parametrize(
        "name",
        [
            "▶ RRT volumes can span internal and eSATA drives",
            "▶ Boot Option #1/ #2/ #3/ #4/ #5/ #6/ #7",
            "▶ Save Overclocking Profile 1/ 2/ 3/ 4/ 5/ 6",
            "▪ All Full Speed",
            "USB0 controller enable",
            "USB1 3.1 port 0",
            "Disable DF to external downstram IP Sync Flood Propagation",
            "& LEDs in Sleep, Hibernation, and Soft Off States",
        ],
    )
    def test_parser_13_recognizes_audited_vendor_omissions(self, name):
        assert self._headings(_line(name)) == [name]

    @pytest.mark.parametrize(
        "name",
        [
            "▶ Set Voltage Offset When Running CPU Ratio x8",
            "▶ Overclocking Profile 1/ 2/ 3/ 4/ 5/ 6",
            "▪ Read the following instructions",
            "USB controller information",
        ],
    )
    def test_parser_13_does_not_generalize_to_neighboring_groups_or_prose(self, name):
        assert self._headings(_line(name)) == []

    def test_parser_13_splits_msi_inline_operation_description(self):
        pdf = _capture_pdf(
            _line("▪ All Full Speed - configures all fans to run at full operating speed.")
        )
        assert settings_parser.parse_settings(pdf) == [
            settings_parser.SettingCandidate(
                extracted_name="▪ All Full Speed",
                extracted_description="configures all fans to run at full operating speed.",
                page_number=1,
            )
        ]

    def test_trailing_marker_qualifies_plain_line(self):
        assert self._headings(_line("CPU Load-line Calibration Mode")) == [
            "CPU Load-line Calibration Mode"
        ]

    def test_bracketed_default_qualifies_plain_line(self):
        assert self._headings(_line("Ai Overclock Tuner [Auto]")) == ["Ai Overclock Tuner [Auto]"]

    def test_options_list_line_is_not_a_heading(self):
        # False positive guard: an options line ends in brackets but is no name.
        assert self._headings(_line("Configuration options: [Enabled] [Disabled]")) == []

    def test_title_case_run_qualifies_plain_line(self):
        assert self._headings(_line("Advanced Memory Settings")) == ["Advanced Memory Settings"]

    def test_single_title_case_word_is_not_a_heading(self):
        assert self._headings(_line("Overview")) == []

    def test_headings_returned_in_page_order(self):
        first = _line("Memory Frequency", font_name="Helvetica-Bold")
        second = _line("DRAM Voltage", font_name="Helvetica-Bold")
        assert self._headings(first, second) == ["Memory Frequency", "DRAM Voltage"]

    def test_blank_lines_are_ignored(self):
        assert self._headings(_line("   ", font_name="Helvetica-Bold")) == []

    def test_detection_is_deterministic(self):
        page = _page_with_lines(_line("Memory Frequency", font_name="Helvetica-Bold"))
        assert settings_parser.find_heading_lines(page) == settings_parser.find_heading_lines(page)


def _capture_pdf(*lines: pdf_text.PageLine, page_number: int = 1) -> pdf_text.PdfText:
    page = pdf_text.PageText(
        page_number=page_number,
        text="\n".join(line.text for line in lines),
        lines=list(lines),
    )
    return pdf_text.PdfText(page_count=1, pages=[page])


def _bold(text: str) -> pdf_text.PageLine:
    return _line(text, font_name="Helvetica-Bold")


class TestDescriptionCapture:
    """Rule 3: capture the body text following each detected heading."""

    def test_heading_followed_by_one_description_line(self):
        pdf = _capture_pdf(
            _bold("Ai Overclock Tuner"),
            _line("Allows you to select the memory profile."),
        )
        assert settings_parser.parse_settings(pdf) == [
            settings_parser.SettingCandidate(
                extracted_name="Ai Overclock Tuner",
                extracted_description="Allows you to select the memory profile.",
                page_number=1,
            )
        ]

    def test_heading_followed_by_multiple_lines_in_order(self):
        pdf = _capture_pdf(
            _bold("DRAM Voltage"),
            _line("Sets the voltage supplied to the memory modules."),
            _line("Use Auto unless you know your kit's rated voltage."),
            _line("Configuration options: [Auto] [Manual]"),
        )
        (candidate,) = settings_parser.parse_settings(pdf)
        assert candidate.extracted_description == (
            "Sets the voltage supplied to the memory modules.\n"
            "Use Auto unless you know your kit's rated voltage.\n"
            "Configuration options: [Auto] [Manual]"
        )

    def test_heading_with_no_following_text_has_empty_description(self):
        pdf = _capture_pdf(_bold("Ai Overclock Tuner"))
        (candidate,) = settings_parser.parse_settings(pdf)
        assert candidate.extracted_description == ""

    def test_capture_stops_at_next_heading(self):
        pdf = _capture_pdf(
            _bold("Memory Frequency"),
            _line("Selects the operating frequency of the installed memory."),
            _bold("DRAM Voltage"),
            _line("Sets the voltage supplied to the memory modules."),
        )
        first, second = settings_parser.parse_settings(pdf)
        assert first.extracted_name == "Memory Frequency"
        assert first.extracted_description == (
            "Selects the operating frequency of the installed memory."
        )
        assert second.extracted_name == "DRAM Voltage"
        assert second.extracted_description == "Sets the voltage supplied to the memory modules."

    def test_capture_stops_at_section_boundary(self):
        pdf = _capture_pdf(
            _bold("Memory Frequency"),
            _line("Selects the operating frequency of the installed memory."),
            _line("1.3 Motherboard rear and audio connections"),
            _line("This text belongs to the new section, not the setting."),
        )
        (candidate,) = settings_parser.parse_settings(pdf)
        assert candidate.extracted_description == (
            "Selects the operating frequency of the installed memory."
        )

    def test_numbered_steps_are_not_section_boundaries(self):
        pdf = _capture_pdf(
            _bold("Clear CMOS Procedure Mode"),
            _line("1. Turn off the computer and unplug the power cord."),
            _line("2. Press the Clear CMOS button."),
        )
        (candidate,) = settings_parser.parse_settings(pdf)
        assert candidate.extracted_description == (
            "1. Turn off the computer and unplug the power cord.\n2. Press the Clear CMOS button."
        )

    def test_capture_runs_to_end_of_page(self):
        pdf = _capture_pdf(
            _bold("Memory Frequency"),
            _line("First line of the description."),
            _line("Last line, ending the page."),
        )
        (candidate,) = settings_parser.parse_settings(pdf)
        assert candidate.extracted_description == (
            "First line of the description.\nLast line, ending the page."
        )

    def test_text_is_preserved_verbatim(self):
        pdf = _capture_pdf(
            _bold("Ai Overclock Tuner [Auto]"),
            _line("ALLOWS you to Select   the MiXeD-caSe profile (XMP/EXPO)."),
        )
        (candidate,) = settings_parser.parse_settings(pdf)
        assert candidate.extracted_name == "Ai Overclock Tuner [Auto]"
        assert candidate.extracted_description == (
            "ALLOWS you to Select   the MiXeD-caSe profile (XMP/EXPO)."
        )

    def test_page_number_is_recorded_per_page(self):
        page_three = pdf_text.PageText(
            page_number=3,
            text="DRAM Voltage",
            lines=[_bold("DRAM Voltage")],
        )
        pdf = pdf_text.PdfText(
            page_count=3, pages=[_capture_pdf(_bold("A B")).pages[0], page_three]
        )
        names_and_pages = [
            (c.extracted_name, c.page_number) for c in settings_parser.parse_settings(pdf)
        ]
        assert names_and_pages == [("A B", 1), ("DRAM Voltage", 3)]

    def test_capture_is_deterministic(self):
        pdf = _capture_pdf(
            _bold("Memory Frequency"),
            _line("Selects the operating frequency of the installed memory."),
        )
        assert settings_parser.parse_settings(pdf) == settings_parser.parse_settings(pdf)

    def test_capture_does_not_modify_input(self):
        pdf = _capture_pdf(
            _bold("Memory Frequency"),
            _line("Selects the operating frequency of the installed memory."),
        )
        snapshot = copy.deepcopy(pdf)
        settings_parser.parse_settings(pdf)
        assert pdf == snapshot


def _multi_page_pdf(*pages: list[pdf_text.PageLine]) -> pdf_text.PdfText:
    built = [
        pdf_text.PageText(
            page_number=number,
            text="\n".join(line.text for line in lines),
            lines=lines,
        )
        for number, lines in enumerate(pages, start=1)
    ]
    return pdf_text.PdfText(page_count=len(built), pages=built)


def _names(pdf: pdf_text.PdfText) -> list[str]:
    return [candidate.extracted_name for candidate in settings_parser.parse_settings(pdf)]


class TestNoiseFiltering:
    """Rule 4: discard candidates matching known non-setting patterns."""

    def test_dotted_section_heading_is_filtered(self):
        pdf = _capture_pdf(
            _bold("2.6 BIOS FlashBack"),
            _line("BIOS FlashBack allows you to update the BIOS without an OS."),
            _bold("Ai Overclock Tuner"),
            _line("Allows you to select the memory profile."),
        )
        assert _names(pdf) == ["Ai Overclock Tuner"]

    def test_dashed_section_heading_is_filtered(self):
        pdf = _capture_pdf(_bold("1-2-4 PCI Subsystem Settings"))
        assert _names(pdf) == []

    def test_chapter_heading_is_filtered(self):
        pdf = _capture_pdf(_bold("Chapter 3 BIOS and RAID Support"))
        assert _names(pdf) == []

    def test_toc_dot_leader_line_is_filtered(self):
        pdf = _capture_pdf(_line("BIOS Setup...............4"))
        assert _names(pdf) == []

    def test_copyright_heading_is_filtered(self):
        pdf = _capture_pdf(_bold("Copyright"), _bold("Copyright and Trademarks"))
        assert _names(pdf) == []

    def test_figure_caption_is_filtered(self):
        pdf = _capture_pdf(_bold("Figure 2-1 Rear Panel Connectors"))
        assert _names(pdf) == []

    def test_table_caption_is_filtered(self):
        pdf = _capture_pdf(_bold("Table 3 Fan Connector Pinouts"))
        assert _names(pdf) == []

    @pytest.mark.parametrize(
        "heading",
        [
            "Contents",
            "Motherboard",
            "USER GUIDE",
            "BIOS Manual",
            "UEFI BIOS",
            "Important!",
            "NOTE:",
            "Function Keys:",
            "1. Knowing BIOS",
            "2. BIOS setup program",
            "Version 1.1",
            "Revised Edition V3",
            "Published February 2026",
            "April 2025",
            "E26236",
        ],
    )
    def test_document_scaffolding_is_filtered(self, heading):
        assert settings_parser.is_noise_heading(heading)

    @pytest.mark.parametrize(
        "setting",
        ["1.8V Voltage", "P-Core 2", "Above 4G Memory Configuration", "CPU Core Voltage"],
    )
    def test_new_scaffolding_filters_preserve_setting_patterns(self, setting):
        assert not settings_parser.is_noise_heading(setting)

    def test_repeated_page_edge_line_is_filtered_as_header(self):
        # "BIOS Setup" runs across the top of both pages: a page header.
        page_one = [
            _line("BIOS Setup"),
            _bold("DRAM Voltage"),
            _line("Sets the voltage supplied to the memory modules."),
        ]
        page_two = [
            _line("BIOS Setup"),
            _bold("Memory Frequency"),
            _line("Selects the operating frequency of the installed memory."),
        ]
        assert _names(_multi_page_pdf(page_one, page_two)) == [
            "DRAM Voltage",
            "Memory Frequency",
        ]

    def test_repeated_page_edge_line_is_filtered_as_footer(self):
        page_one = [_bold("DRAM Voltage"), _line("Body text on page one."), _line("OC Menu")]
        page_two = [_bold("CPU Ratio Mode"), _line("Body text on page two."), _line("OC Menu")]
        assert _names(_multi_page_pdf(page_one, page_two)) == ["DRAM Voltage", "CPU Ratio Mode"]

    def test_footer_with_embedded_page_number_is_filtered(self):
        # Real footers embed the page number ("25 BIOS Setup"), so no two
        # are byte-identical; they must still be recognized as one footer.
        page_one = [
            _bold("DRAM Voltage"),
            _line("Sets the voltage supplied to the memory modules."),
            _line("BIOS Setup 25"),
        ]
        page_two = [
            _bold("Memory Frequency"),
            _line("Selects the operating frequency of the installed memory."),
            _line("BIOS Setup 26"),
        ]
        assert _names(_multi_page_pdf(page_one, page_two)) == [
            "DRAM Voltage",
            "Memory Frequency",
        ]

    def test_numbered_settings_away_from_page_edges_survive_digit_masking(self):
        # "P-Core 0" and "P-Core 1" share a digit-masked form; mid-page they
        # must never be mistaken for a running header or footer.
        page_one = [
            _line("Introductory body text for this page of the manual."),
            _bold("P-Core 0"),
            _line("Sets the ratio for this single P-core."),
            _line("Closing body text keeps the setting away from the page edge."),
        ]
        page_two = [
            _line("Introductory body text for this page of the manual."),
            _bold("P-Core 1"),
            _line("Sets the ratio for this single P-core."),
            _line("Closing body text keeps the setting away from the page edge."),
        ]
        assert _names(_multi_page_pdf(page_one, page_two)) == ["P-Core 0", "P-Core 1"]

    def test_numbered_setting_series_topping_few_pages_of_long_document_is_kept(self):
        # "Numbers of E-Core of Group 1" and "... Group 2" share a
        # digit-masked form and can start consecutive pages, but a run of
        # two pages in a long document is not a running header.
        setting_pages = [
            [
                _bold(f"Numbers of E-Core of Group {number}"),
                _line("Sets a number of E cores for this group."),
                _line("Closing body text for the page."),
            ]
            for number in (1, 2)
        ]
        filler_pages = [
            [
                _line(f"Unrelated introduction paragraph number {number}."),
                _line("More explanatory body text follows on this page."),
            ]
            for number in range(7)
        ]
        pdf = _multi_page_pdf(*setting_pages, *filler_pages)
        assert _names(pdf) == [
            "Numbers of E-Core of Group 1",
            "Numbers of E-Core of Group 2",
        ]

    def test_repeated_setting_name_away_from_page_edges_is_kept(self):
        # The same setting legitimately recurs mid-page in several menus
        # (e.g. memory timings); repetition alone must not discard it.
        page = [
            _line("Introductory body text for this page of the manual."),
            _bold("DRAM Voltage"),
            _line("Sets the voltage supplied to the memory modules."),
            _line("Closing body text keeps the setting away from the page edge."),
        ]
        assert _names(_multi_page_pdf(list(page), list(page))) == [
            "DRAM Voltage",
            "DRAM Voltage",
        ]

    def test_unique_heading_at_page_edge_is_kept(self):
        # Edge position alone is not noise; only repetition across pages is.
        pdf = _capture_pdf(_bold("DRAM Voltage"), _line("Sets the memory voltage."))
        assert _names(pdf) == ["DRAM Voltage"]

    def test_genuine_settings_pass_through_unchanged(self):
        pdf = _capture_pdf(
            _bold("Ai Overclock Tuner [Auto]"),
            _line("Allows you to select the memory profile."),
            _line("CPU Load-line Calibration Mode"),
            _line("Configures the load-line calibration level."),
        )
        candidates = settings_parser.parse_settings(pdf)
        assert [c.extracted_name for c in candidates] == [
            "Ai Overclock Tuner [Auto]",
            "CPU Load-line Calibration Mode",
        ]
        assert candidates[0].extracted_description == "Allows you to select the memory profile."

    def test_ambiguous_hardware_heading_without_section_number_is_kept(self):
        # Recall bias: an unnumbered hardware heading may be noise, but it
        # matches no plan-listed pattern, so it stays for reviewer judgment.
        pdf = _capture_pdf(_bold("Clear CMOS Button"), _line("Clears the RTC RAM."))
        assert _names(pdf) == ["Clear CMOS Button"]

    def test_names_without_any_letter_are_filtered(self):
        # Figure annotation numbers and page fragments ("22", "11") are
        # extracted bold and pass rule 2; a name with no letter is not a
        # setting name.
        pdf = _capture_pdf(
            _bold("22"),
            _bold("33"),
            _bold("1 2 3"),
            _bold("DRAM Voltage"),
            _line("Sets the voltage supplied to the memory modules."),
        )
        assert _names(pdf) == ["DRAM Voltage"]

    def test_names_mixing_letters_and_digits_are_kept(self):
        for name in ("P-Core 0", "3DMark01 Enhancement", "Port 60/64 Emulation", "+5V Rail"):
            assert not settings_parser.is_noise_heading(name), name

    def test_bracketed_default_names_are_kept(self):
        pdf = _capture_pdf(_line("Ai Overclock Tuner [Auto]"))
        assert _names(pdf) == ["Ai Overclock Tuner [Auto]"]

    def test_numeric_prefixed_setting_names_are_not_section_numbers(self):
        for name in ("1-Core Ratio Limit Override", "Above 4G Memory Configuration"):
            assert not settings_parser.is_noise_heading(name), name

    def test_noise_pattern_matching_is_case_insensitive(self):
        assert settings_parser.is_noise_heading("CHAPTER 1 Getting Started")
        assert settings_parser.is_noise_heading("COPYRIGHT NOTICE")
        assert settings_parser.is_noise_heading("FIGURE 3 Board Layout")

    def test_filtering_is_deterministic(self):
        pdf = _capture_pdf(_bold("2.6 BIOS FlashBack"), _bold("DRAM Voltage"))
        assert settings_parser.parse_settings(pdf) == settings_parser.parse_settings(pdf)

    def test_filtering_does_not_modify_input(self):
        pdf = _capture_pdf(_bold("2.6 BIOS FlashBack"), _bold("DRAM Voltage"))
        snapshot = copy.deepcopy(pdf)
        settings_parser.parse_settings(pdf)
        assert pdf == snapshot
