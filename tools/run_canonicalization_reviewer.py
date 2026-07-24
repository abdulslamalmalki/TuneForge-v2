"""Run TuneForge's conservative canonicalization reviewer and write its audit report."""

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from app.db.session import SessionLocal
from app.domain import canonicalization
from app.domain.extraction_runs import utc_now_iso


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Evaluate the backlog without persisting suggestions or mappings.",
    )
    args = parser.parse_args()

    with SessionLocal() as session:
        run_report = (
            None if args.dry_run else canonicalization.run_reviewer(session)
        )
        evaluation = canonicalization.evaluate_backlog(session)

    payload = {
        "generated_at": utc_now_iso(),
        "mode": "dry_run" if args.dry_run else "applied",
        "engine_version": canonicalization.ENGINE_VERSION,
        "confidence_threshold_basis_points": (
            canonicalization.CONFIDENCE_THRESHOLD_BP
        ),
        "summary": {
            "evaluated_count": (
                len(evaluation.entries)
                if run_report is None
                else run_report.evaluated_count
            ),
            "auto_approved_mapping_count": (
                0
                if run_report is None
                else len(run_report.auto_approved_mappings)
            ),
            "new_canonical_setting_count": (
                0
                if run_report is None
                else len(run_report.new_canonical_settings)
            ),
            "remaining_manual_review_count": len(evaluation.entries),
            "proposed_new_canonical_group_count": len(
                evaluation.proposed_groups
            ),
            "pending_suggestion_count": (
                0
                if run_report is None
                else run_report.pending_suggestion_count
            ),
        },
        "auto_approved_mappings": (
            []
            if run_report is None
            else [asdict(item) for item in run_report.auto_approved_mappings]
        ),
        "new_canonical_settings": (
            []
            if run_report is None
            else [asdict(item) for item in run_report.new_canonical_settings]
        ),
        "proposed_new_canonical_groups": [
            asdict(item) for item in evaluation.proposed_groups
        ],
        "remaining_manual_review": [
            asdict(item) for item in evaluation.entries
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(f"Report: {args.output.resolve()}")
    for key, value in payload["summary"].items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
