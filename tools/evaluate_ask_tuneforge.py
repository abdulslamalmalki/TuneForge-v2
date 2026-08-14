"""Evaluate Ask TuneForge against the checked-in citation-first question dataset."""

import argparse
import json
from pathlib import Path

from app.db.session import SessionLocal
from app.evaluation.ask_tuneforge import evaluate_dataset, load_dataset

DEFAULT_DATASET = Path("evaluations/ask_tuneforge_v1.json")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    dataset = load_dataset(args.dataset)
    with SessionLocal() as session:
        report = evaluate_dataset(session, dataset)
    payload = report.to_dict()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"Report: {args.output.resolve()}")
    print(json.dumps(payload["metrics"], indent=2, sort_keys=True))
    failures = [result for result in payload["results"] if result["failures"]]
    if failures:
        print(json.dumps(failures, indent=2, sort_keys=True))
    return 0 if report.status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
