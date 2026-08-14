"""Run the complete read-only TuneForge data-quality and public-visibility audit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.db.session import SessionLocal
from app.domain import data_quality
from tools.audit_public_bios_knowledge import build_report as build_public_report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--skip-file-hashes",
        action="store_true",
        help="Skip stored-file size/hash checks for a faster diagnostic run.",
    )
    args = parser.parse_args()
    with SessionLocal() as session:
        quality = data_quality.build_report(
            session, verify_files=not args.skip_file_hashes
        ).as_dict()
        public = build_public_report(session)
    report = {
        "status": (
            "pass" if quality["status"] == "pass" and public["status"] == "pass" else "fail"
        ),
        "quality": quality,
        "public_knowledge": {
            "status": public["status"],
            "summary": public["summary"],
            "failures": public["failures"],
            "warnings": public["warnings"],
        },
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(f"Report: {args.output.resolve()}")
    print(json.dumps({"status": report["status"], **quality["summary"]}, indent=2))
    if quality["findings"]:
        print(json.dumps(quality["findings"], indent=2))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
