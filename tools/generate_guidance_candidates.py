"""Generate deterministic guidance candidates from approved source settings."""

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from app.db.session import SessionLocal
from app.domain import guidance_candidates
from app.domain.extraction_runs import utc_now_iso


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--after-setting-id", type=int, default=0)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    with SessionLocal() as session:
        report = guidance_candidates.generate_candidates(
            session,
            after_setting_id=args.after_setting_id,
            limit=args.limit,
            commit=not args.dry_run,
        )
        metrics = guidance_candidates.coverage_metrics(session)
        counts = guidance_candidates.status_counts(session)
        if args.dry_run:
            session.rollback()

    payload = {
        "generated_at": utc_now_iso(),
        "mode": "dry_run" if args.dry_run else "applied",
        "generator_version": guidance_candidates.GENERATOR_VERSION,
        "resume": {
            "after_setting_id": args.after_setting_id,
            "limit": args.limit,
            "last_setting_id": report.last_setting_id,
        },
        "run": asdict(report),
        "coverage": asdict(metrics),
        "status_counts": counts,
        "trust_notice": (
            "Generated candidates are not approved knowledge. Promotion creates a draft "
            "claim and human claim approval remains mandatory."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    print(f"Report: {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
