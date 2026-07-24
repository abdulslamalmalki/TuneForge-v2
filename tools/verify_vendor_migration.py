"""Read-only database fingerprint and Vendor Normalization Phase 2 verifier."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

PROTECTED_QUERIES = {
    "extraction_runs": "SELECT * FROM extraction_runs ORDER BY id",
    "settings": "SELECT * FROM settings ORDER BY id",
    "review_events": "SELECT * FROM review_events ORDER BY id",
    "missed_settings": "SELECT * FROM missed_settings ORDER BY id",
    "manual_events": "SELECT * FROM manual_events ORDER BY id",
    "acceptance": (
        "SELECT id, acceptance_reviewed_at, acceptance_reviewer, acceptance_notes "
        "FROM manuals ORDER BY id"
    ),
}


def _digest(connection: sqlite3.Connection, query: str) -> str:
    payload = json.dumps(
        connection.execute(query).fetchall(),
        separators=(",", ":"),
        default=str,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def database_report(path: Path) -> dict[str, Any]:
    uri = f"{path.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        manual_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(manuals)").fetchall()
        }
        report: dict[str, Any] = {
            "database": str(path.resolve()),
            "revision": connection.execute("SELECT version_num FROM alembic_version").fetchone()[0],
            "integrity": connection.execute("PRAGMA integrity_check").fetchone()[0],
            "manuals": connection.execute("SELECT COUNT(*) FROM manuals").fetchone()[0],
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
            "digests": {
                name: _digest(connection, query) for name, query in PROTECTED_QUERIES.items()
            },
        }
        if "vendor_id" in manual_columns:
            report["vendor_identity"] = {
                "reference_vendors": connection.execute("SELECT COUNT(*) FROM vendors").fetchone()[
                    0
                ],
                "aliases": connection.execute("SELECT COUNT(*) FROM vendor_aliases").fetchone()[0],
                "manuals_with_vendor_id": connection.execute(
                    "SELECT COUNT(*) FROM manuals WHERE vendor_id IS NOT NULL"
                ).fetchone()[0],
                "unmatched_manuals": connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM manuals
                    LEFT JOIN vendors ON vendors.vendor_id = manuals.vendor_id
                    WHERE manuals.vendor IS NOT NULL
                      AND (manuals.vendor_id IS NULL OR vendors.vendor_id IS NULL)
                    """
                ).fetchone()[0],
                "canonical_mismatches": connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM manuals
                    JOIN vendors ON vendors.vendor_id = manuals.vendor_id
                    WHERE manuals.vendor != vendors.canonical_name
                    """
                ).fetchone()[0],
            }
        return report


def _check_expected(report: dict[str, Any], args: argparse.Namespace) -> None:
    expected = {
        "revision": args.expect_revision,
        "manuals": args.expect_manuals,
        "certified": args.expect_certified,
        "drafts": args.expect_drafts,
        "approved": args.expect_approved,
        "rejected": args.expect_rejected,
        "fts": args.expect_fts,
    }
    failures = [
        f"{field}: expected {value!r}, found {report[field]!r}"
        for field, value in expected.items()
        if value is not None and report[field] != value
    ]
    if report["integrity"] != "ok":
        failures.append(f"integrity: expected 'ok', found {report['integrity']!r}")
    if args.expect_phase2:
        identity = report.get("vendor_identity")
        if identity is None:
            failures.append("vendor identity schema is missing")
        else:
            phase2_expected = {
                "reference_vendors": 4,
                "manuals_with_vendor_id": report["manuals"],
                "unmatched_manuals": 0,
                "canonical_mismatches": 0,
            }
            failures.extend(
                f"vendor_identity.{field}: expected {value}, found {identity[field]}"
                for field, value in phase2_expected.items()
                if identity[field] != value
            )
    if failures:
        raise SystemExit("Verification failed:\n- " + "\n- ".join(failures))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--expect-revision")
    parser.add_argument("--expect-manuals", type=int)
    parser.add_argument("--expect-certified", type=int)
    parser.add_argument("--expect-drafts", type=int)
    parser.add_argument("--expect-approved", type=int)
    parser.add_argument("--expect-rejected", type=int)
    parser.add_argument("--expect-fts", type=int)
    parser.add_argument("--expect-phase2", action="store_true")
    args = parser.parse_args()
    report = database_report(args.database)
    _check_expected(report, args)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
