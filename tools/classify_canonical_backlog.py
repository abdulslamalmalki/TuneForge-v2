"""Classify every approved-unmapped canonicalization record and report outcomes."""

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from app.db.session import SessionLocal
from app.domain import canonical_backlog_assessments
from app.domain.extraction_runs import utc_now_iso


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    with SessionLocal() as session:
        report = canonical_backlog_assessments.refresh_assessments(
            session, commit=not args.dry_run
        )
        metrics = canonical_backlog_assessments.metrics(session)
        if args.dry_run:
            session.rollback()
    payload = {
        "generated_at": utc_now_iso(),
        "mode": "dry_run" if args.dry_run else "applied",
        "engine_version": canonical_backlog_assessments.ENGINE_VERSION,
        "run": asdict(report),
        "metrics": asdict(metrics),
        "trust_notice": (
            "Classification is triage, not equivalence truth. It never creates a "
            "canonical mapping or weakens the 99.5% automatic mapping gate."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    print(f"Report: {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
