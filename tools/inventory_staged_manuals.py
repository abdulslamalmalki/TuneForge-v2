"""Inventory staged manuals without changing files or the production database.

The report combines immutable file facts, cover-page provenance suggestions,
eligibility classification, staged duplicate detection, and production-corpus
membership. It is intended to rank a controlled ingestion wave before any
manual enters the reviewer workflow.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import pdfplumber
from sqlalchemy import select

from app.db.models import Manual
from app.db.session import SessionLocal
from app.eligibility.classifier import classify_document, read_bounded_sample
from app.provenance.metadata import extract_metadata

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CORPUS_ROOT = PROJECT_ROOT.parent
DEFAULT_PATHS = (
    CORPUS_ROOT / "assets" / "manuals" / "staging" / "ASUS",
    CORPUS_ROOT / "assets" / "manuals" / "staging" / "MSI",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pdf_paths(inputs: list[Path]) -> list[Path]:
    paths: list[Path] = []
    for item in inputs:
        if item.is_dir():
            paths.extend(item.glob("*.pdf"))
        elif item.is_file() and item.suffix.casefold() == ".pdf":
            paths.append(item)
        else:
            raise RuntimeError(f"Not a PDF or directory: {item}")
    return sorted(set(paths), key=lambda path: (path.parent.name, len(path.name), path.name))


def build_inventory(inputs: list[Path]) -> dict[str, object]:
    paths = _pdf_paths(inputs)
    with SessionLocal() as session:
        ingested_by_hash = {
            sha256: manual_id
            for sha256, manual_id in session.execute(select(Manual.sha256, Manual.id))
        }

    rows: list[dict[str, object]] = []
    first_staged_path: dict[str, str] = {}
    for path in paths:
        sha256 = _sha256(path)
        metadata = extract_metadata(path, path.name)
        with pdfplumber.open(path) as pdf:
            page_count = len(pdf.pages)
        classification = classify_document(
            filename=path.name,
            document_title=metadata.document_title,
            page_count=page_count,
            sample_text=read_bounded_sample(path),
        )
        duplicate_of = first_staged_path.get(sha256)
        first_staged_path.setdefault(sha256, str(path))
        ingested_manual_id = ingested_by_hash.get(sha256)
        if ingested_manual_id is not None:
            disposition = "already_ingested"
        elif duplicate_of is not None:
            disposition = "staged_duplicate"
        elif classification.eligible_recommendation:
            disposition = "eligible_candidate"
        else:
            disposition = "hold_non_bios_document"
        rows.append(
            {
                "path": str(path),
                "vendor_directory": path.parent.name,
                "filename": path.name,
                "sha256": sha256,
                "file_size_bytes": path.stat().st_size,
                "page_count": page_count,
                "metadata": metadata.as_dict(),
                "classification": asdict(classification),
                "ingested_manual_id": ingested_manual_id,
                "duplicate_of": duplicate_of,
                "disposition": disposition,
            }
        )

    counts: dict[str, int] = {}
    for row in rows:
        disposition = str(row["disposition"])
        counts[disposition] = counts.get(disposition, 0) + 1
    return {"manual_count": len(rows), "dispositions": counts, "manuals": rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--json", type=Path, help="Optional JSON report destination")
    args = parser.parse_args()
    report = build_inventory(args.paths or list(DEFAULT_PATHS))
    rendered = json.dumps(report, indent=2, ensure_ascii=True) + "\n"
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
