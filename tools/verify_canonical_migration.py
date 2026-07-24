"""Read-only verifier for canonical-setting migration and mapping invariants."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def database_report(path: Path) -> dict[str, object]:
    uri = f"{path.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        return {
            "database": str(path.resolve()),
            "revision": connection.execute("SELECT version_num FROM alembic_version").fetchone()[0],
            "integrity": connection.execute("PRAGMA integrity_check").fetchone()[0],
            "manuals": connection.execute("SELECT COUNT(*) FROM manuals").fetchone()[0],
            "certified": connection.execute(
                "SELECT COUNT(*) FROM manuals WHERE acceptance_reviewed_at IS NOT NULL"
            ).fetchone()[0],
            "approved": connection.execute(
                "SELECT COUNT(*) FROM settings WHERE status = 'approved'"
            ).fetchone()[0],
            "rejected": connection.execute(
                "SELECT COUNT(*) FROM settings WHERE status = 'rejected'"
            ).fetchone()[0],
            "drafts": connection.execute(
                "SELECT COUNT(*) FROM settings WHERE status = 'draft'"
            ).fetchone()[0],
            "fts": connection.execute("SELECT COUNT(*) FROM settings_fts").fetchone()[0],
            "canonical_identities": connection.execute(
                "SELECT COUNT(*) FROM canonical_settings"
            ).fetchone()[0],
            "cpu_architectures": connection.execute(
                "SELECT COUNT(*) FROM cpu_architectures"
            ).fetchone()[0],
            "cpu_families": connection.execute("SELECT COUNT(*) FROM cpu_families").fetchone()[0],
            "cpu_models": connection.execute("SELECT COUNT(*) FROM cpu_models").fetchone()[0],
            "cpu_model_aliases": connection.execute(
                "SELECT COUNT(*) FROM cpu_model_aliases"
            ).fetchone()[0],
            "canonical_aliases": connection.execute(
                "SELECT COUNT(*) FROM canonical_setting_aliases"
            ).fetchone()[0],
            "canonical_mappings": connection.execute(
                "SELECT COUNT(*) FROM setting_canonical_mappings"
            ).fetchone()[0],
            "mapping_events": connection.execute(
                "SELECT COUNT(*) FROM canonical_mapping_events"
            ).fetchone()[0],
            "canonical_categories": connection.execute(
                "SELECT COUNT(*) FROM canonical_setting_categories"
            ).fetchone()[0],
            "canonical_classifications": connection.execute(
                "SELECT COUNT(*) FROM canonical_setting_classifications"
            ).fetchone()[0],
            "classification_events": connection.execute(
                "SELECT COUNT(*) FROM canonical_classification_events"
            ).fetchone()[0],
            "canonical_suggestions": connection.execute(
                "SELECT COUNT(*) FROM canonical_mapping_suggestions"
            ).fetchone()[0],
            "pending_suggestions": connection.execute(
                """
                SELECT COUNT(*) FROM canonical_mapping_suggestions
                WHERE status = 'pending'
                """
            ).fetchone()[0],
            "accepted_suggestions": connection.execute(
                """
                SELECT COUNT(*) FROM canonical_mapping_suggestions
                WHERE status = 'accepted'
                """
            ).fetchone()[0],
            "dismissed_suggestions": connection.execute(
                """
                SELECT COUNT(*) FROM canonical_mapping_suggestions
                WHERE status = 'dismissed'
                """
            ).fetchone()[0],
            "suggestion_events": connection.execute(
                "SELECT COUNT(*) FROM canonical_suggestion_events"
            ).fetchone()[0],
            "mapped_vendors": connection.execute(
                """
                SELECT COUNT(DISTINCT manuals.vendor_id)
                FROM setting_canonical_mappings
                JOIN settings ON settings.id = setting_canonical_mappings.setting_id
                JOIN manuals ON manuals.id = settings.manual_id
                """
            ).fetchone()[0],
            "nonapproved_mappings": connection.execute(
                """
                SELECT COUNT(*)
                FROM setting_canonical_mappings
                JOIN settings ON settings.id = setting_canonical_mappings.setting_id
                WHERE settings.status != 'approved'
                """
            ).fetchone()[0],
            "orphan_mappings": connection.execute(
                """
                SELECT COUNT(*)
                FROM setting_canonical_mappings AS mapping
                LEFT JOIN settings ON settings.id = mapping.setting_id
                LEFT JOIN canonical_settings AS canonical
                  ON canonical.canonical_id = mapping.canonical_id
                WHERE settings.id IS NULL OR canonical.canonical_id IS NULL
                """
            ).fetchone()[0],
            "identities_without_alias": connection.execute(
                """
                SELECT COUNT(*)
                FROM canonical_settings AS canonical
                LEFT JOIN canonical_setting_aliases AS alias
                  ON alias.canonical_id = canonical.canonical_id
                WHERE alias.normalized_alias IS NULL
                """
            ).fetchone()[0],
            "identities_without_classification": connection.execute(
                """
                SELECT COUNT(*)
                FROM canonical_settings AS canonical
                LEFT JOIN canonical_setting_classifications AS classification
                  ON classification.canonical_id = canonical.canonical_id
                WHERE classification.canonical_id IS NULL
                """
            ).fetchone()[0],
            "invalid_classifications": connection.execute(
                """
                SELECT COUNT(*)
                FROM canonical_setting_classifications AS classification
                LEFT JOIN canonical_setting_categories AS category
                  ON category.category_id = classification.category_id
                WHERE category.category_id IS NULL
                   OR classification.risk_level NOT IN
                      ('unclassified', 'low', 'moderate', 'high', 'critical')
                   OR trim(classification.risk_rationale) = ''
                """
            ).fetchone()[0],
            "invalid_suggestions": connection.execute(
                """
                SELECT COUNT(*)
                FROM canonical_mapping_suggestions AS suggestion
                LEFT JOIN settings ON settings.id = suggestion.setting_id
                LEFT JOIN canonical_settings AS canonical
                  ON canonical.canonical_id = suggestion.canonical_id
                WHERE settings.id IS NULL OR canonical.canonical_id IS NULL
                   OR suggestion.match_type != 'exact_alias'
                """
            ).fetchone()[0],
        }


def _check(report: dict[str, object], args: argparse.Namespace) -> None:
    expected = {
        "revision": args.expect_revision,
        "canonical_identities": args.expect_identities,
        "canonical_aliases": args.expect_aliases,
        "canonical_mappings": args.expect_mappings,
        "mapping_events": args.expect_events,
        "mapped_vendors": args.expect_vendors,
        "canonical_categories": args.expect_categories,
        "canonical_classifications": args.expect_classifications,
        "classification_events": args.expect_classification_events,
        "canonical_suggestions": args.expect_suggestions,
        "pending_suggestions": args.expect_pending_suggestions,
        "accepted_suggestions": args.expect_accepted_suggestions,
        "dismissed_suggestions": args.expect_dismissed_suggestions,
        "suggestion_events": args.expect_suggestion_events,
        "cpu_architectures": args.expect_cpu_architectures,
        "cpu_families": args.expect_cpu_families,
        "cpu_models": args.expect_cpu_models,
        "cpu_model_aliases": args.expect_cpu_aliases,
    }
    failures = [
        f"{field}: expected {value!r}, found {report[field]!r}"
        for field, value in expected.items()
        if value is not None and report[field] != value
    ]
    if report["integrity"] != "ok":
        failures.append(f"integrity: expected 'ok', found {report['integrity']!r}")
    if args.strict:
        for field in (
            "nonapproved_mappings",
            "orphan_mappings",
            "identities_without_alias",
            "identities_without_classification",
            "invalid_classifications",
            "invalid_suggestions",
        ):
            if report[field]:
                failures.append(f"{field}: expected 0, found {report[field]}")
        corpus = {
            "manuals": 12,
            "certified": 12,
            "approved": 4018,
            "rejected": 4356,
            "drafts": 0,
            "fts": 4018,
        }
        for field, value in corpus.items():
            if report[field] != value:
                failures.append(f"{field}: expected {value}, found {report[field]}")
    if failures:
        raise SystemExit("Verification failed:\n- " + "\n- ".join(failures))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--expect-revision")
    parser.add_argument("--expect-identities", type=int)
    parser.add_argument("--expect-aliases", type=int)
    parser.add_argument("--expect-mappings", type=int)
    parser.add_argument("--expect-events", type=int)
    parser.add_argument("--expect-vendors", type=int)
    parser.add_argument("--expect-categories", type=int)
    parser.add_argument("--expect-classifications", type=int)
    parser.add_argument("--expect-classification-events", type=int)
    parser.add_argument("--expect-suggestions", type=int)
    parser.add_argument("--expect-pending-suggestions", type=int)
    parser.add_argument("--expect-accepted-suggestions", type=int)
    parser.add_argument("--expect-dismissed-suggestions", type=int)
    parser.add_argument("--expect-suggestion-events", type=int)
    parser.add_argument("--expect-cpu-architectures", type=int)
    parser.add_argument("--expect-cpu-families", type=int)
    parser.add_argument("--expect-cpu-models", type=int)
    parser.add_argument("--expect-cpu-aliases", type=int)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    report = database_report(args.database)
    _check(report, args)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
