"""Preview normalized hardware applicability for production and staging manuals.

This Phase 1 command is deliberately read-only. It resolves model, chipset,
family, platform, and applicability-scope IDs without updating the database,
manual provenance, review history, or staging manifest.
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
from app.normalization.hardware import (
    MODEL_UNKNOWN,
    SCOPE_UNKNOWN,
    contract,
    document_type_for_applicability,
    normalize_applicability,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CORPUS_ROOT = PROJECT_ROOT.parent
DEFAULT_MANIFEST = CORPUS_ROOT / "assets" / "manuals" / "staging-manifest.csv"


@dataclass(frozen=True)
class ApplicabilityAuditInput:
    source: str
    record_id: str
    vendor: str
    model_value: str
    chipset_value: str
    identity_text: str
    document_type: str


@dataclass(frozen=True)
class ApplicabilityAuditRow:
    source: str
    record_id: str
    vendor: str
    model_value: str
    chipset_value: str
    document_type: str
    scope: str
    target_ids: tuple[str, ...]
    model_status: str
    model_id: str | None
    canonical_model: str | None
    chipset_ids: tuple[str, ...]
    family_ids: tuple[str, ...]
    platform_ids: tuple[str, ...]
    warnings: tuple[str, ...]


def audit_values(values: Iterable[ApplicabilityAuditInput]) -> list[ApplicabilityAuditRow]:
    rows = []
    for value in values:
        result = normalize_applicability(
            vendor=value.vendor,
            model_value=value.model_value,
            chipset_value=value.chipset_value,
            identity_text=value.identity_text,
            document_type=value.document_type,
        )
        rows.append(
            ApplicabilityAuditRow(
                source=value.source,
                record_id=value.record_id,
                vendor=value.vendor,
                model_value=value.model_value,
                chipset_value=value.chipset_value,
                document_type=value.document_type,
                scope=result.scope,
                target_ids=result.target_ids,
                model_status=result.model.status,
                model_id=result.model.model_id,
                canonical_model=result.model.canonical_name,
                chipset_ids=result.hardware.chipset_ids,
                family_ids=result.hardware.family_ids,
                platform_ids=result.hardware.platform_ids,
                warnings=result.warnings,
            )
        )
    return rows


def production_inputs(session: Session) -> list[ApplicabilityAuditInput]:
    manuals = session.scalars(select(Manual).order_by(Manual.id))
    return [
        ApplicabilityAuditInput(
            source="production_manual",
            record_id=str(manual.id),
            vendor=manual.vendor or "",
            model_value=manual.model_name or "",
            chipset_value=manual.chipset or "",
            identity_text=" ".join(
                value
                for value in (
                    manual.original_filename,
                    manual.document_title,
                    manual.model_name,
                    manual.chipset,
                )
                if value
            ),
            document_type=document_type_for_applicability(
                manual.document_type or "",
                manual.document_title or "",
            ),
        )
        for manual in manuals
    ]


def staging_inputs(manifest_path: Path) -> list[ApplicabilityAuditInput]:
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Staging manifest not found: {manifest_path}")
    with manifest_path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {
            "relative_path",
            "vendor",
            "filename",
            "metadata_title",
            "document_type",
            "platform",
            "chipsets",
        }
        missing = required - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"Staging manifest is missing column(s): {', '.join(sorted(missing))}")
        rows = []
        for row in reader:
            identity = " ".join(
                value
                for value in (
                    row["filename"],
                    row["metadata_title"],
                    row["platform"],
                    row["chipsets"],
                )
                if value
            )
            rows.append(
                ApplicabilityAuditInput(
                    source="staging_manifest",
                    record_id=row["relative_path"],
                    vendor=row["vendor"],
                    model_value=identity,
                    chipset_value=row["chipsets"],
                    identity_text=identity,
                    document_type=document_type_for_applicability(row["document_type"]),
                )
            )
        return rows


def summarize(rows: list[ApplicabilityAuditRow]) -> dict[str, object]:
    return {
        "total": len(rows),
        "resolved": sum(row.scope != SCOPE_UNKNOWN for row in rows),
        "unknown_scope": sum(row.scope == SCOPE_UNKNOWN for row in rows),
        "warnings": sum(bool(row.warnings) for row in rows),
        "scopes": dict(sorted(Counter(row.scope for row in rows).items())),
        "model_statuses": dict(sorted(Counter(row.model_status for row in rows).items())),
        "platforms": dict(
            sorted(Counter(item for row in rows for item in row.platform_ids).items())
        ),
    }


def build_report(
    production_rows: list[ApplicabilityAuditRow],
    staging_rows: list[ApplicabilityAuditRow],
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
            asdict(row) for row in all_rows if row.scope == SCOPE_UNKNOWN or row.warnings
        ],
        "unknown_models_with_other_scope": [
            asdict(row)
            for row in all_rows
            if row.model_status == MODEL_UNKNOWN and row.scope != SCOPE_UNKNOWN
        ],
        "records": [asdict(row) for row in all_rows],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--json", type=Path, help="Optional JSON report destination")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit unsuccessfully when any record has unknown or conflicting scope",
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
