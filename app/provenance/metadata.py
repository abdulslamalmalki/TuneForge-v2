"""Conservative metadata suggestions extracted from a manual's cover pages."""

import re
from dataclasses import asdict, dataclass
from pathlib import Path

import pdfplumber

from app.provenance.official_sources import canonical_vendor

METADATA_VERSION = "1.0"
_CHIPSET = re.compile(
    r"\b(?:(?:AMD|Intel)\s+(?:AM5|\d{3})(?:\s+Series)?|"
    r"(?:X\d{3}[A-Z]?|B\d{3}[A-Z]?|A\d{3}|Z\d{3}|H\d{3}|W\d{3}))\b",
    re.IGNORECASE,
)
_MODEL_MARKER = re.compile(
    r"\b(?:ROG|PRIME|ProArt|TUF|MPG|MAG|MEG|PRO|AORUS|TAICHI|STEEL LEGEND)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class MetadataSuggestion:
    vendor: str
    model_name: str
    document_title: str
    document_version: str
    published_on: str
    language: str
    board_revision: str
    chipset: str
    extractor_version: str = METADATA_VERSION

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def extract_metadata(
    path: Path, original_filename: str, *, max_pages: int = 5
) -> MetadataSuggestion:
    """Read only cover/front-matter pages and return editable suggestions."""
    with pdfplumber.open(path) as pdf:
        text = "\n".join((page.extract_text() or "") for page in pdf.pages[:max_pages])
    lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
    filename_text = _filename_title(original_filename)
    haystack = f"{original_filename}\n{text}"
    vendor = _detect_vendor(haystack)
    title = _detect_title(lines, filename_text)
    model = _detect_model(lines, title)
    chipset_match = _CHIPSET.search(f"{title}\n{model}\n{haystack}")
    return MetadataSuggestion(
        vendor=vendor,
        model_name=model,
        document_title=title,
        document_version=_detect_version(lines),
        published_on="",
        language=_detect_language(haystack),
        board_revision="",
        chipset=chipset_match.group(0) if chipset_match else "",
    )


def _detect_vendor(text: str) -> str:
    folded = text.casefold()
    markers = (
        ("ASRock", ("asrock",)),
        ("GIGABYTE", ("gigabyte", "aorus")),
        ("MSI", ("micro-star", "micro star", " msi ", "msi_", "msi®")),
        ("ASUS", ("asustek", " asus ", "asus_", " rog ", "rog_")),
    )
    padded = f" {folded} "
    for vendor, needles in markers:
        if any(needle in padded for needle in needles):
            return vendor
    return canonical_vendor(text) or ""


def _detect_title(lines: list[str], fallback: str) -> str:
    for index, line in enumerate(lines[:30]):
        if line.casefold() == "bios manual":
            prefix = _cover_prefix(lines, index)
            return f"{prefix} BIOS Manual" if prefix else fallback
        if line.casefold() == "user guide" and index:
            prefix = _cover_prefix(lines, index)
            if "bios" in prefix.casefold():
                return f"{prefix} User Guide"
            if prefix:
                return f"{prefix} Motherboard User Guide"
    for line in lines[:50]:
        folded = line.casefold()
        if "bios" in folded and any(word in folded for word in ("manual", "guide", "setup")):
            if (
                6 <= len(line) <= 180
                and not line.startswith(("http://", "https://"))
                and "...." not in line
            ):
                return line
    model = _detect_model(lines, "")
    if model:
        return f"{model} Motherboard User Guide"
    return fallback


def _detect_model(lines: list[str], title: str) -> str:
    title_folded = title.casefold()
    for line in lines[:20]:
        match = re.search(
            r"purchasing\s+the\s+MSI\W+(?P<model>.+?)\s+motherboard", line, re.IGNORECASE
        )
        if match:
            return match.group("model").strip(" .")
    for line in lines[:80]:
        if not 5 <= len(line) <= 100 or _MODEL_MARKER.search(line) is None:
            continue
        folded = line.casefold()
        if any(term in folded for term in ("bios manual", "bios setup", "user guide")):
            continue
        if folded == title_folded:
            continue
        if _CHIPSET.search(line) or any(marker in folded for marker in ("motherboard", "wifi")):
            cleaned = re.sub(r"^Thank you for purchasing the MSI\W+", "", line, flags=re.I)
            return re.sub(r"\s+(?:motherboard|user guide).*$", "", cleaned, flags=re.I).strip()
    if "series" in title_folded and "bios" in title_folded:
        return re.sub(r"\s+BIOS\s+(?:Manual|User Guide).*$", "", title, flags=re.I).strip()
    return ""


def _cover_prefix(lines: list[str], end_index: int) -> str:
    selected: list[str] = []
    for line in reversed(lines[max(0, end_index - 6) : end_index]):
        folded = line.casefold()
        if line == "draobrehtoM" or re.fullmatch(r"[A-Z]\d{4,}", line):
            continue
        if folded in {"motherboard", "user guide"}:
            continue
        if len(line) > 80:
            break
        selected.append(line)
    selected.reverse()
    return " ".join(selected).replace(" / ", " / ").strip(" /-")


def _detect_version(lines: list[str]) -> str:
    front_matter = "\n".join(lines[:40])
    patterns = (
        re.compile(r"\b(?:Revised\s+)?Edition\s+V(?P<value>\d+(?:\.\d+)*)\b", re.I),
        re.compile(r"\bVersion\s*[:.-]?\s*(?P<value>[A-Z]?\d[\w.-]*)\b", re.I),
        re.compile(r"\bRev(?:ision)?\.?\s*[:.-]?\s*(?P<value>[A-Z]?\d[\w.-]*)\b", re.I),
    )
    for pattern in patterns:
        match = pattern.search(front_matter)
        if match:
            return match.group("value")
    return ""


def _filename_title(filename: str) -> str:
    stem = Path(filename).stem
    cleaned = re.sub(r"[_-]+", " ", stem)
    cleaned = re.sub(r"\s+\(\d+\)$", "", cleaned)
    return " ".join(cleaned.split())


def _detect_language(text: str) -> str:
    folded = text.casefold()
    if any(marker in folded for marker in ("english edition", "_en.", "_en_", " english")):
        return "en"
    return "en"
