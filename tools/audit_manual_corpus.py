"""Audit a staged motherboard-manual PDF corpus before ingestion.

The command is intentionally read-only with respect to the corpus. It emits a
JSON report and a flat CSV manifest containing file integrity, exact duplicate
groups, PDF/text-layer health, inferred platform/chipsets, document type, and a
recommended ingestion priority.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pdfplumber

from app.normalization.vendors import canonical_vendor_name

AMD_CHIPSETS = (
    "X870E",
    "X870",
    "B850",
    "B840",
    "X670E",
    "X670",
    "B650E",
    "B650",
    "X570S",
    "X570",
    "B550",
    "A520",
    "X470",
    "B450",
)
INTEL_CHIPSETS = (
    "Z890",
    "B860",
    "H810",
    "Z790",
    "B760",
    "H770",
    "Z690",
    "B660",
    "H670",
    "H610",
    "Z590",
    "B560",
    "H570",
    "H510",
    "Z490",
)
_SERIES_PATTERNS = (
    (re.compile(r"AMD[^A-Z0-9]*800|AMD800", re.IGNORECASE), "AMD 800 Series", "AMD"),
    (re.compile(r"AMD[^A-Z0-9]*600|AMD600", re.IGNORECASE), "AMD 600 Series", "AMD"),
    (re.compile(r"INTEL[^A-Z0-9]*800|INTEL800", re.IGNORECASE), "Intel 800 Series", "Intel"),
    (re.compile(r"INTEL[^A-Z0-9]*700|INTEL700", re.IGNORECASE), "Intel 700 Series", "Intel"),
    (
        re.compile(r"INTEL[^A-Z0-9]*600|(?<!AMD)600SERIES", re.IGNORECASE),
        "Intel 600 Series",
        "Intel",
    ),
    (re.compile(r"AMD[^A-Z0-9]*AM5|AMDAM5|AM5BIOS", re.IGNORECASE), "AMD AM5", "AMD"),
)


@dataclass
class AuditRecord:
    relative_path: str
    vendor: str
    filename: str
    size_bytes: int
    sha256: str
    duplicate_of: str | None
    valid_pdf: bool
    encrypted: bool
    page_count: int
    pages_sampled: int
    sampled_text_pages: int
    sampled_text_page_ratio: float
    sampled_text_characters: int
    metadata_title: str | None
    metadata_author: str | None
    document_type: str
    platform: str
    chipsets: list[str]
    quality_status: str
    ingestion_priority: str
    error: str | None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def infer_identity(
    filename: str, title: str | None = None, content: str = ""
) -> tuple[str, str, list[str]]:
    document_haystack = f"{filename} {title or ''}".replace("_", " ")
    haystack = f"{document_haystack} {content[:2000]}"
    uppercase = haystack.upper()
    chipsets = [chipset for chipset in (*AMD_CHIPSETS, *INTEL_CHIPSETS) if chipset in uppercase]
    platform_hits: set[str] = set()
    if any(chipset in AMD_CHIPSETS for chipset in chipsets):
        platform_hits.add("AMD")
    if any(chipset in INTEL_CHIPSETS for chipset in chipsets):
        platform_hits.add("Intel")
    for pattern, family, platform in _SERIES_PATTERNS:
        if pattern.search(haystack) and family not in chipsets:
            chipsets.append(family)
            platform_hits.add(platform)

    lowered = document_haystack.lower()
    if "feature" in lowered:
        document_type = "features_guide"
    elif "bios" in lowered or "uefi" in lowered:
        document_type = "bios_setup_guide"
    else:
        document_type = "user_manual"
    platform = next(iter(platform_hits)) if len(platform_hits) == 1 else "Unknown"
    return document_type, platform, chipsets


def sample_page_indexes(page_count: int, max_pages: int) -> list[int]:
    """Return deterministic, evenly distributed zero-based page indexes."""
    if page_count <= 0:
        return []
    if max_pages <= 0 or page_count <= max_pages:
        return list(range(page_count))
    if max_pages == 1:
        return [0]
    return sorted({round(index * (page_count - 1) / (max_pages - 1)) for index in range(max_pages)})


def audit_pdf(path: Path, root: Path, *, max_pages: int = 12) -> AuditRecord:
    relative_path = path.relative_to(root).as_posix()
    raw_vendor = relative_path.split("/", 1)[0] if "/" in relative_path else "Unknown"
    vendor = canonical_vendor_name(raw_vendor) or raw_vendor
    digest = sha256_file(path)
    metadata: dict[str, Any] = {}
    page_count = 0
    pages_sampled = 0
    sampled_text_pages = 0
    sampled_text_characters = 0
    encrypted = False
    error: str | None = None
    identity_text = ""

    try:
        with pdfplumber.open(path) as pdf:
            metadata = pdf.metadata or {}
            page_count = len(pdf.pages)
            encrypted = bool(getattr(pdf.doc, "is_encrypted", False))
            page_indexes = sample_page_indexes(page_count, max_pages)
            pages_sampled = len(page_indexes)
            for page_index in page_indexes:
                page = pdf.pages[page_index]
                try:
                    text = page.extract_text() or ""
                except Exception as page_error:  # one bad page should not hide the corpus report
                    error = f"Page {page.page_number}: {type(page_error).__name__}: {page_error}"
                    continue
                stripped = text.strip()
                if not identity_text:
                    identity_text = stripped
                sampled_text_characters += len(stripped)
                if len(stripped) >= 20:
                    sampled_text_pages += 1
    except Exception as pdf_error:
        error = f"{type(pdf_error).__name__}: {pdf_error}"

    title = _metadata_text(metadata.get("Title"))
    author = _metadata_text(metadata.get("Author"))
    document_type, platform, chipsets = infer_identity(path.name, title, identity_text)
    valid_pdf = page_count > 0
    text_ratio = sampled_text_pages / pages_sampled if pages_sampled else 0.0
    if not valid_pdf:
        quality_status = "invalid_pdf"
    elif sampled_text_characters < 200 or sampled_text_pages == 0:
        quality_status = "needs_ocr"
    elif error:
        quality_status = "partial_error"
    else:
        quality_status = "ready"

    if quality_status != "ready":
        priority = "hold"
    elif document_type == "bios_setup_guide":
        priority = "high"
    elif document_type == "features_guide":
        priority = "medium"
    else:
        priority = "low"

    return AuditRecord(
        relative_path=relative_path,
        vendor=vendor,
        filename=path.name,
        size_bytes=path.stat().st_size,
        sha256=digest,
        duplicate_of=None,
        valid_pdf=valid_pdf,
        encrypted=encrypted,
        page_count=page_count,
        pages_sampled=pages_sampled,
        sampled_text_pages=sampled_text_pages,
        sampled_text_page_ratio=round(text_ratio, 4),
        sampled_text_characters=sampled_text_characters,
        metadata_title=title,
        metadata_author=author,
        document_type=document_type,
        platform=platform,
        chipsets=chipsets,
        quality_status=quality_status,
        ingestion_priority=priority,
        error=error,
    )


def audit_corpus(root: Path, *, max_pages: int = 12, progress: bool = False) -> list[AuditRecord]:
    root = root.resolve()
    paths = sorted(root.rglob("*.pdf"))
    records = []
    for index, path in enumerate(paths, start=1):
        if progress:
            print(f"[{index}/{len(paths)}] {path.relative_to(root).as_posix()}", flush=True)
        records.append(audit_pdf(path, root, max_pages=max_pages))
    records_by_hash: dict[str, list[AuditRecord]] = {}
    for record in records:
        records_by_hash.setdefault(record.sha256, []).append(record)
    for group in records_by_hash.values():
        canonical = min(group, key=lambda item: _canonical_rank(item.relative_path)).relative_path
        for record in group:
            if canonical == record.relative_path:
                continue
            record.duplicate_of = canonical
            record.ingestion_priority = "exclude_duplicate"
    return records


def _canonical_rank(relative_path: str) -> tuple[int, int, str]:
    filename = Path(relative_path).stem
    looks_like_copy = bool(re.search(r"(?:\bcopy\b|\(\d+\)$)", filename, re.IGNORECASE))
    return (int(looks_like_copy), len(relative_path), relative_path.casefold())


def build_report(root: Path, records: list[AuditRecord]) -> dict[str, Any]:
    unique_hashes = {record.sha256 for record in records}
    return {
        "corpus_root": str(root.resolve()),
        "summary": {
            "files": len(records),
            "unique_files": len(unique_hashes),
            "duplicate_files": len(records) - len(unique_hashes),
            "total_pages": sum(record.page_count for record in records),
            "vendors": dict(sorted(Counter(record.vendor for record in records).items())),
            "quality": dict(sorted(Counter(record.quality_status for record in records).items())),
            "document_types": dict(
                sorted(Counter(record.document_type for record in records).items())
            ),
            "priorities": dict(
                sorted(Counter(record.ingestion_priority for record in records).items())
            ),
        },
        "manuals": [asdict(record) for record in records],
    }


def write_outputs(records: list[AuditRecord], root: Path, json_path: Path, csv_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(build_report(root, records), indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    fieldnames = (
        list(asdict(records[0]).keys())
        if records
        else [field.name for field in AuditRecord.__dataclass_fields__.values()]
    )
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            row = asdict(record)
            row["chipsets"] = "; ".join(record.chipsets)
            writer.writerow(row)


def _metadata_text(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(str(value).split())
    return cleaned or None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path, help="Directory containing staged PDF manuals")
    parser.add_argument("--json", type=Path, required=True, help="JSON report destination")
    parser.add_argument("--csv", type=Path, required=True, help="CSV manifest destination")
    parser.add_argument(
        "--max-pages",
        type=int,
        default=12,
        help="Representative pages to inspect per PDF; use 0 for every page (default: 12)",
    )
    args = parser.parse_args()
    records = audit_corpus(args.corpus, max_pages=args.max_pages, progress=True)
    write_outputs(records, args.corpus, args.json, args.csv)
    print(json.dumps(build_report(args.corpus, records)["summary"], indent=2))


if __name__ == "__main__":
    main()
