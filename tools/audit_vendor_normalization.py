"""Preview vendor normalization across production manuals and the staged corpus.

This command is deliberately read-only. It reports proposed canonical IDs and
display names but does not update the database, provenance, or corpus files.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Manual
from app.db.session import SessionLocal
from app.normalization.vendors import (
    STATUS_BLANK,
    STATUS_RECOGNIZED,
    STATUS_UNKNOWN,
    contract,
    normalize_vendor,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CORPUS_ROOT = PROJECT_ROOT.parent
DEFAULT_MANIFEST = CORPUS_ROOT / "assets" / "manuals" / "staging-manifest.csv"


@dataclass(frozen=True)
class VendorAuditInput:
    source: str
    record_id: str
    raw_value: str


@dataclass(frozen=True)
class VendorAuditRow:
    source: str
    record_id: str
    raw_value: str
    normalized_key: str
    status: str
    vendor_id: str | None
    canonical_name: str | None
    matched_alias: str | None
    changed: bool


def audit_values(values: Iterable[VendorAuditInput]) -> list[VendorAuditRow]:
    rows = []
    for value in values:
        result = normalize_vendor(value.raw_value)
        rows.append(
            VendorAuditRow(
                source=value.source,
                record_id=value.record_id,
                raw_value=value.raw_value,
                normalized_key=result.normalized_key,
                status=result.status,
                vendor_id=result.vendor_id,
                canonical_name=result.canonical_name,
                matched_alias=result.matched_alias,
                changed=result.changed,
            )
        )
    return rows


def production_inputs(session: Session) -> list[VendorAuditInput]:
    manuals = session.scalars(select(Manual).order_by(Manual.id))
    return [
        VendorAuditInput(
            source="production_manual",
            record_id=str(manual.id),
            raw_value=manual.vendor or "",
        )
        for manual in manuals
    ]


def staging_inputs(manifest_path: Path) -> list[VendorAuditInput]:
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Staging manifest not found: {manifest_path}")
    with manifest_path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"relative_path", "vendor"}
        missing = required - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"Staging manifest is missing column(s): {', '.join(sorted(missing))}")
        return [
            VendorAuditInput(
                source="staging_manifest",
                record_id=row["relative_path"],
                raw_value=row["vendor"],
            )
            for row in reader
        ]


def summarize(rows: list[VendorAuditRow]) -> dict[str, object]:
    return {
        "total": len(rows),
        "recognized": sum(row.status == STATUS_RECOGNIZED for row in rows),
        "unchanged": sum(row.status == STATUS_RECOGNIZED and not row.changed for row in rows),
        "proposed_changes": sum(row.changed for row in rows),
        "unknown": sum(row.status == STATUS_UNKNOWN for row in rows),
        "blank": sum(row.status == STATUS_BLANK for row in rows),
        "canonical_vendors": dict(
            sorted(Counter(row.vendor_id for row in rows if row.vendor_id).items())
        ),
    }


def build_report(
    production_rows: list[VendorAuditRow],
    staging_rows: list[VendorAuditRow],
) -> dict[str, object]:
    all_rows = [*production_rows, *staging_rows]
    return {
        "contract": contract(),
        "summary": {
            "all_sources": summarize(all_rows),
            "production": summarize(production_rows),
            "staging": summarize(staging_rows),
        },
        "conflicts": [
            asdict(row) for row in all_rows if row.status in (STATUS_UNKNOWN, STATUS_BLANK)
        ],
        "proposed_changes": [asdict(row) for row in all_rows if row.changed],
        "records": [asdict(row) for row in all_rows],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--json", type=Path, help="Optional JSON report destination")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit unsuccessfully when an unknown or blank vendor is found",
    )
    args = parser.parse_args()

    with SessionLocal() as session:
        production_rows = audit_values(production_inputs(session))
    staging_rows = audit_values(staging_inputs(args.manifest))
    report = build_report(production_rows, staging_rows)
    rendered = json.dumps(report, indent=2, ensure_ascii=True) + "\n"
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    if args.strict and report["conflicts"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
