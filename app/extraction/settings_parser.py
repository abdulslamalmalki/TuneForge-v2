"""Heuristic BIOS-setting parser (Implementation Plan sections 8 and 10).

Pure function layer: PdfText in, candidate settings out. No database access,
no run updates, no side effects. Everything produced here is an unverified
candidate for a Draft record; a human reviews it before it becomes knowledge.

Implemented rules from plan section 10: 1 (BIOS-page signals), 2 (heading
detection), 3 (description capture), and 4 (noise filtering). Rule 3's
blank-line-gap boundary is deferred until the reader exposes line
positions; capture stops at the next heading, a new section number, or the
end of the page.
"""

import math
import re
from collections import Counter
from dataclasses import dataclass

from app.extraction.pdf_text import PageLine, PageText, PdfText

# Stamped on every succeeded extraction run so Draft provenance stays
# traceable (plan section 10, item 5). Version 1.1 adds conservative filters
# for document scaffolding observed in the four-vendor validation corpus.
# Version 1.2 recognizes ASUS TTNormsPro-Medium setting labels even when they
# are the same size as body text. ASUS uses that weight for many single-token
# and long DRAM labels that 1.1 silently missed.
# Version 1.3 adds narrow vendor patterns backed by certified omission audits:
# MSI executable marker lines, ASRock lowercase USB controls and Sync Flood
# propagation, and the Gigabyte sleep-state LED control.
# Bump this value on ANY behavior change.
PARSER_VERSION = "1.3"

# Plan section 10, rule 1 (scope reduction): pages containing these phrases
# look like the BIOS chapter. Word boundaries keep "BIOS" from matching
# inside unrelated words; matching is case-insensitive because manuals vary
# ("BIOS SETUP", "Configuration Options:").
_BIOS_SIGNALS = re.compile(r"\b(?:BIOS|UEFI|Setup Utility|Configuration options)\b", re.IGNORECASE)


@dataclass(frozen=True)
class SettingCandidate:
    """One candidate (setting name, description) pair found on a page.

    Field names mirror what FR-15 persists on a Draft settings row.
    """

    extracted_name: str
    extracted_description: str  # may be empty (FR-14)
    page_number: int  # 1-based source page (FR-15)


def parse_settings(pdf: PdfText) -> list[SettingCandidate]:
    """Identify candidate BIOS settings in the given page text (FR-13).

    Deterministic and side-effect free. Candidates are returned in reading
    order: ascending page number, top-to-bottom within a page. Rule 4 then
    discards candidates whose name matches a known non-setting pattern.
    """
    header_footer_lines = _repeated_edge_lines(pdf)
    candidates: list[SettingCandidate] = []
    for page in pdf.pages:
        candidates.extend(_parse_page(page))
    return [
        candidate
        for candidate in candidates
        if not is_noise_heading(candidate.extracted_name)
        and _edge_fingerprint(candidate.extracted_name) not in header_footer_lines
    ]


def page_has_bios_signals(page: PageText) -> bool:
    """Rule 1 (scope reduction): does this page look like the BIOS chapter?

    Pages without signals are still parsed (plan section 10); later rules
    use this to prefer signal pages when judging candidates.
    """
    return _BIOS_SIGNALS.search(page.text) is not None


# Rule 3 boundary: a new numbered section ends the current capture. Dotted
# section numbers ("1.2 ", "3.1.4 ") and chapter markers count; bare list
# numbers ("1. ") do not, so numbered steps stay inside descriptions.
_SECTION_BOUNDARY = re.compile(r"^(?:\d+(?:\.\d+)+\s|Chapter\s+\d+\b)")


def _parse_page(page: PageText) -> list[SettingCandidate]:
    """Rules 2 and 3: detect headings and capture their descriptions.

    A description is the run of body-text lines following a heading, kept
    verbatim in reading order, until the next candidate heading, a new
    section number, or the end of the page (plan section 10, rule 3). A
    heading with nothing following keeps an empty description (FR-14).
    """
    body_font_size = _body_font_size(page)
    candidates: list[SettingCandidate] = []
    current_name: str | None = None
    current_description: list[str] = []

    def _flush() -> None:
        nonlocal current_name, current_description
        if current_name is not None:
            candidates.append(
                SettingCandidate(
                    extracted_name=current_name,
                    extracted_description="\n".join(current_description),
                    page_number=page.page_number,
                )
            )
        current_name = None
        current_description = []

    for line in page.lines:
        text = line.text.strip()
        if not text:
            continue
        inline_heading = _split_inline_vendor_heading(text)
        if inline_heading is not None:
            _flush()
            current_name, inline_description = inline_heading
            if inline_description:
                current_description.append(inline_description)
            continue
        if _is_setting_heading(line, body_font_size):
            _flush()
            current_name = text
        elif _is_section_boundary(text):
            _flush()
        elif current_name is not None:
            current_description.append(text)
    _flush()  # the end of the page closes the last capture
    return candidates


def _is_section_boundary(text: str) -> bool:
    return _SECTION_BOUNDARY.match(text) is not None


# Rule 2 (plan section 10): heading detection.

_MAX_HEADING_WORDS = 8
_MAX_MEDIUM_HEADING_WORDS = 16
_TRAILING_MARKERS = {"Mode", "Control", "Limit", "Voltage", "Configuration"}
# "Setting Name [Auto]": exactly one bracketed group at the end of the line.
_BRACKETED_DEFAULT = re.compile(r"(?P<name>[^\[\]]+)\[[^\]]+\]")
_MSI_EXECUTABLE_MARKER = re.compile(
    r"^(?:▶\s*)?(?:"
    r"RRT volumes can span internal and eSATA drives|"
    r"Boot Option #\d+(?:/\s*#\d+)+|"
    r"(?:Set Name for|Save|Load|Clear) Overclocking Profile\b.*|"
    r"(?:▪\s*)?All (?:Full Speed|Set Default|Set Cancel)"
    r")$",
    re.IGNORECASE,
)
_ASROCK_USB_CONTROL = re.compile(
    r"^USB\d+\s+(?:controller enable|\d+\.\d+\s+port\s+\d+)$", re.IGNORECASE
)
_AUDITED_VENDOR_CONTROL = re.compile(
    r"^(?:Disable DF to external downstram IP Sync Flood Propagation|"
    r"(?:&\s*)?LEDs in Sleep, Hibernation, and Soft Off States)$",
    re.IGNORECASE,
)
_MSI_INLINE_OPERATION = re.compile(
    r"^(?P<name>▪\s*All (?:Full Speed|Set Default|Set Cancel))\s+-\s+"
    r"(?P<description>.+)$",
    re.IGNORECASE,
)


def find_heading_lines(page: PageText) -> list[PageLine]:
    """Rule 2: lines that look like candidate setting names, in page order.

    A line qualifies when it is short (<= 8 words), does not end in a
    period, and is either visually distinguished (bold font or larger than
    the page's body font size) or matches a strong lexical pattern.
    """
    body_font_size = _body_font_size(page)
    return [line for line in page.lines if _is_setting_heading(line, body_font_size)]


def _body_font_size(page: PageText) -> float:
    """The page's dominant (body) font size: most common among non-blank
    lines, preferring the smaller size on ties so headings stay above it."""
    sizes = [line.font_size for line in page.lines if line.text.strip()]
    if not sizes:
        return 0.0
    counts = Counter(sizes)
    highest = max(counts.values())
    return min(size for size, count in counts.items() if count == highest)


def _is_setting_heading(line: PageLine, body_font_size: float) -> bool:
    text = line.text.strip()
    if not text:
        return False
    if _matches_audited_vendor_pattern(text):
        return True
    word_count = len(text.split())
    if _is_medium_weight(line):
        return word_count <= _MAX_MEDIUM_HEADING_WORDS
    if text.endswith("."):
        return False
    if word_count > _MAX_HEADING_WORDS:
        return False
    return _is_visually_distinguished(line, body_font_size) or _matches_lexical_pattern(text)


def _is_visually_distinguished(line: PageLine, body_font_size: float) -> bool:
    return "bold" in line.font_name.lower() or line.font_size > body_font_size


def _is_medium_weight(line: PageLine) -> bool:
    """ASUS manual labels use a Medium face at the body text's point size."""
    return "medium" in line.font_name.lower()


def _matches_lexical_pattern(text: str) -> bool:
    words = text.split()
    if words[-1] in _TRAILING_MARKERS:
        return True
    bracketed = _BRACKETED_DEFAULT.fullmatch(text)
    if bracketed:
        name = bracketed.group("name").strip()
        # An options list ("Configuration options: [Enabled]") is not a name.
        if name and not name.endswith(":"):
            return True
    return _is_title_case_run(words)


def _matches_audited_vendor_pattern(text: str) -> bool:
    return (
        _MSI_EXECUTABLE_MARKER.fullmatch(text) is not None
        or _ASROCK_USB_CONTROL.fullmatch(text) is not None
        or _AUDITED_VENDOR_CONTROL.fullmatch(text) is not None
    )


def _split_inline_vendor_heading(text: str) -> tuple[str, str] | None:
    match = _MSI_INLINE_OPERATION.fullmatch(text)
    if match is None:
        return None
    return match.group("name"), match.group("description")


def _is_title_case_run(words: list[str]) -> bool:
    """A run (>= 2) of tokens whose first alphabetic character is uppercase."""
    if len(words) < 2:
        return False
    for word in words:
        first_alpha = next((char for char in word if char.isalpha()), None)
        if first_alpha is not None and not first_alpha.isupper():
            return False
    return True


# Rule 4 (plan section 10): noise filters. Candidates whose name matches a
# known non-setting pattern are discarded after capture. Only the patterns
# the plan lists are filtered; anything uncertain is kept, because a
# reviewer rejecting noise is cheaper than silently losing a real setting.

# Chapter/section numbers: "2.6 BIOS FlashBack", "1-2-4 PCI Subsystem
# Settings", "Chapter 3 BIOS Setup". At least one separator-plus-digit group
# is required so names like "1-Core Ratio Limit Override" survive.
_NOISE_SECTION_NUMBER = re.compile(r"^(?:\d+(?:[.\-]\d+)+\.?\s|chapter\s+\d+\b)", re.IGNORECASE)
# Table-of-contents dot leaders: "BIOS Setup..............4".
_NOISE_DOT_LEADER = re.compile(r"\.{2,}")
# Copyright lines on notices pages.
_NOISE_COPYRIGHT = re.compile(r"©|\bcopyright\b|\ball rights reserved\b", re.IGNORECASE)
# Figure and table captions: "Figure 2-1 ...", "Fig. 3 ...", "Table 1-2 ...".
_NOISE_CAPTION = re.compile(r"^(?:figure|fig\.?|table)\s+\d", re.IGNORECASE)
# Simple numbered chapter headings: "1. Knowing BIOS", "2. BIOS setup
# program". A space after the period keeps settings such as "1.8V Voltage".
_NOISE_SIMPLE_SECTION = re.compile(r"^\d+\.\s+")
# Bold labels ending in a colon introduce prose, lists, or form fields; BIOS
# setting names in the supported manuals do not use terminal colons.
_NOISE_TERMINAL_COLON = re.compile(r":$")
# Exact cover, contents, navigation, warning, and document-administration
# labels seen consistently across ASUS, MSI, Gigabyte, and ASRock manuals.
_NOISE_DOCUMENT_LABEL = re.compile(
    r"^(?:"
    r"contents|motherboard|user guide|bios manual|bios setup|bios setup guide|"
    r"software/bios setup guide|uefi bios|important!?|note|disclaimer|"
    r"contact information|startup screen|the main menu|advanced mode|"
    r"advanced mode function keys|function keys|setup menus|configuration items|"
    r"hardware information|smart fan 6"
    r")$",
    re.IGNORECASE,
)
# Version/date/part-number cover lines have no setting semantics.
_NOISE_DOCUMENT_VERSION = re.compile(
    r"^(?:"
    r"(?:revised\s+)?edition(?:\s+v?\d.*)?|"
    r"version\s+\d+(?:\.\d+)*|"
    r"published\s+[a-z]+\s+\d{4}|"
    r"(?:january|february|march|april|may|june|july|august|september|"
    r"october|november|december)\s+\d{4}|"
    r"e\d{4,}"
    r")$",
    re.IGNORECASE,
)


def is_noise_heading(text: str) -> bool:
    """Rule 4: does this candidate name match a known non-setting pattern?

    Covers the static patterns from the plan: chapter/section numbers,
    table-of-contents dot leaders, copyright lines, figure/table captions,
    and names without a single letter (figure annotation numbers, page
    fragments) which cannot be setting names. Header/footer repetition is
    document-level and handled separately in parse_settings.
    """
    stripped = text.strip()
    return (
        not any(char.isalpha() for char in stripped)
        or _NOISE_SECTION_NUMBER.match(stripped) is not None
        or _NOISE_DOT_LEADER.search(stripped) is not None
        or _NOISE_COPYRIGHT.search(stripped) is not None
        or _NOISE_CAPTION.match(stripped) is not None
        or _NOISE_SIMPLE_SECTION.match(stripped) is not None
        or _NOISE_TERMINAL_COLON.search(stripped) is not None
        or _NOISE_DOCUMENT_LABEL.fullmatch(stripped) is not None
        or _NOISE_DOCUMENT_VERSION.fullmatch(stripped) is not None
    )


# Running headers/footers embed the page number ("26 BIOS Setup",
# "BIOS Setup - 8 -"), so "identical" is judged with digit runs masked;
# otherwise every footer is unique and the filter never fires.
_DIGIT_RUN = re.compile(r"\d+")


def _edge_fingerprint(text: str) -> str:
    return _DIGIT_RUN.sub("#", text)


def _repeated_edge_lines(pdf: PdfText) -> frozenset[str]:
    """Page header/footer fingerprints: identical lines repeated across pages.

    A line counts as a header/footer when its digit-masked text appears as
    the first or last non-blank line on at least a quarter of the document's
    pages (minimum two). Running headers and footers recur throughout a
    document; requiring both the page-edge position and document-wide
    repetition keeps genuine settings out of the filter — settings that
    recur mid-page in several menus (e.g. memory timings), and numbered
    setting series ("Group 1", "Group 2") that happen to top a couple of
    consecutive pages.
    """
    pages_seen: Counter[str] = Counter()
    for page in pdf.pages:
        non_blank = [line.text.strip() for line in page.lines if line.text.strip()]
        if not non_blank:
            continue
        for text in {_edge_fingerprint(non_blank[0]), _edge_fingerprint(non_blank[-1])}:
            pages_seen[text] += 1
    minimum_pages = max(2, math.ceil(len(pdf.pages) / 4))
    return frozenset(text for text, count in pages_seen.items() if count >= minimum_pages)
