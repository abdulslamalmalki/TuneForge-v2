"""Verify persisted hardware applicability against normalization contract 1.0."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path

from app.normalization.hardware import (
    CHIPSET_FAMILIES,
    CHIPSETS,
    MOTHERBOARD_MODELS,
    NORMALIZATION_VERSION,
    PLATFORMS,
    document_type_for_applicability,
    normalize_applicability,
)


def database_report(path: Path) -> dict[str, object]:
    uri = f"{path.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        manuals = connection.execute(
            """
            SELECT id, original_filename, vendor, model_name, document_title,
                   chipset, document_type
            FROM manuals ORDER BY id
            """
        ).fetchall()
        projections = []
        conflicts = []
        for manual in manuals:
            expected = _expected(manual)
            stored = _stored(connection, manual["id"])
            projection = {
                "manual_id": manual["id"],
                "expected": expected,
                "stored": stored,
                "matches": expected == stored,
            }
            projections.append(projection)
            if not projection["matches"]:
                conflicts.append(projection)

        scopes = Counter(
            projection["stored"]["scope"]
            for projection in projections
            if projection["stored"] is not None
        )
        return {
            "database": str(path.resolve()),
            "revision": connection.execute("SELECT version_num FROM alembic_version").fetchone()[0],
            "integrity": connection.execute("PRAGMA integrity_check").fetchone()[0],
            "normalization_version": NORMALIZATION_VERSION,
            "reference_counts": {
                "platforms": connection.execute(
                    "SELECT COUNT(*) FROM hardware_platforms"
                ).fetchone()[0],
                "chipset_families": connection.execute(
                    "SELECT COUNT(*) FROM chipset_families"
                ).fetchone()[0],
                "chipsets": connection.execute("SELECT COUNT(*) FROM chipsets").fetchone()[0],
                "motherboard_models": connection.execute(
                    "SELECT COUNT(*) FROM motherboard_models"
                ).fetchone()[0],
            },
            "manuals": len(manuals),
            "applicability_rows": connection.execute(
                "SELECT COUNT(*) FROM manual_applicability"
            ).fetchone()[0],
            "target_rows": connection.execute(
                "SELECT COUNT(*) FROM manual_applicability_targets"
            ).fetchone()[0],
            "normalization_events": connection.execute(
                """
                SELECT COUNT(*) FROM manual_events
                WHERE action = 'hardware_applicability_normalized'
                """
            ).fetchone()[0],
            "unknown_scopes": connection.execute(
                "SELECT COUNT(*) FROM manual_applicability WHERE scope = 'unknown'"
            ).fetchone()[0],
            "warning_rows": connection.execute(
                "SELECT COUNT(*) FROM manual_applicability WHERE warnings_json != '[]'"
            ).fetchone()[0],
            "scopes": dict(sorted(scopes.items())),
            "corpus": {
                "certified": connection.execute(
                    "SELECT COUNT(*) FROM manuals WHERE acceptance_reviewed_at IS NOT NULL"
                ).fetchone()[0],
                "drafts": connection.execute(
                    "SELECT COUNT(*) FROM settings WHERE status = 'draft'"
                ).fetchone()[0],
                "approved": connection.execute(
                    "SELECT COUNT(*) FROM settings WHERE status = 'approved'"
                ).fetchone()[0],
                "rejected": connection.execute(
                    "SELECT COUNT(*) FROM settings WHERE status = 'rejected'"
                ).fetchone()[0],
                "fts": connection.execute("SELECT COUNT(*) FROM settings_fts").fetchone()[0],
            },
            "conflicts": conflicts,
            "projections": projections,
        }


def _expected(manual: sqlite3.Row) -> dict[str, object]:
    identity = " ".join(
        str(value)
        for value in (
            manual["original_filename"],
            manual["document_title"],
            manual["model_name"],
            manual["chipset"],
        )
        if value
    )
    result = normalize_applicability(
        vendor=manual["vendor"],
        model_value=manual["model_name"],
        chipset_value=manual["chipset"],
        identity_text=identity,
        document_type=document_type_for_applicability(
            manual["document_type"],
            manual["document_title"] or "",
        ),
    )
    return {
        "normalization_version": NORMALIZATION_VERSION,
        "scope": result.scope,
        "target_ids": list(result.target_ids),
        "model_status": result.model.status,
        "detected_model_id": result.model.model_id,
        "warnings": list(result.warnings),
    }


def _stored(connection: sqlite3.Connection, manual_id: int) -> dict[str, object] | None:
    row = connection.execute(
        """
        SELECT normalization_version, scope, model_status, detected_model_id,
               warnings_json
        FROM manual_applicability WHERE manual_id = ?
        """,
        (manual_id,),
    ).fetchone()
    if row is None:
        return None
    targets = connection.execute(
        """
        SELECT COALESCE(platform_id, chipset_family_id, chipset_id,
                        motherboard_model_id)
        FROM manual_applicability_targets
        WHERE manual_id = ? ORDER BY ordinal
        """,
        (manual_id,),
    ).fetchall()
    try:
        warnings = list(json.loads(row["warnings_json"]))
    except (json.JSONDecodeError, TypeError):
        warnings = ["Stored warnings JSON is invalid."]
    return {
        "normalization_version": row["normalization_version"],
        "scope": row["scope"],
        "target_ids": [target[0] for target in targets],
        "model_status": row["model_status"],
        "detected_model_id": row["detected_model_id"],
        "warnings": warnings,
    }


def _check(report: dict[str, object], args: argparse.Namespace) -> None:
    failures = []
    expected_references = {
        "platforms": len(PLATFORMS),
        "chipset_families": len(CHIPSET_FAMILIES),
        "chipsets": len(CHIPSETS),
        "motherboard_models": len(MOTHERBOARD_MODELS),
    }
    if report["integrity"] != "ok":
        failures.append(f"integrity is {report['integrity']!r}")
    if args.expect_revision and report["revision"] != args.expect_revision:
        failures.append(
            f"revision: expected {args.expect_revision!r}, found {report['revision']!r}"
        )
    if report["reference_counts"] != expected_references:
        failures.append(
            f"reference counts: expected {expected_references}, found {report['reference_counts']}"
        )
    if report["applicability_rows"] != report["manuals"]:
        failures.append("not every manual has exactly one applicability row")
    if report["normalization_events"] < report["manuals"]:
        failures.append("not every manual has an applicability normalization event")
    if report["conflicts"]:
        failures.append(f"{len(report['conflicts'])} persisted projections differ from contract")
    if args.strict and report["unknown_scopes"]:
        failures.append(f"{report['unknown_scopes']} manual(s) have unknown scope")
    if args.strict and report["warning_rows"]:
        failures.append(f"{report['warning_rows']} manual(s) have warnings")

    expected_corpus = {
        "certified": args.expect_certified,
        "drafts": args.expect_drafts,
        "approved": args.expect_approved,
        "rejected": args.expect_rejected,
        "fts": args.expect_fts,
    }
    for field, expected in expected_corpus.items():
        if expected is not None and report["corpus"][field] != expected:
            failures.append(f"corpus.{field}: expected {expected}, found {report['corpus'][field]}")
    if failures:
        raise SystemExit("Verification failed:\n- " + "\n- ".join(failures))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--expect-revision")
    parser.add_argument("--expect-certified", type=int)
    parser.add_argument("--expect-drafts", type=int)
    parser.add_argument("--expect-approved", type=int)
    parser.add_argument("--expect-rejected", type=int)
    parser.add_argument("--expect-fts", type=int)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    report = database_report(args.database)
    _check(report, args)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
