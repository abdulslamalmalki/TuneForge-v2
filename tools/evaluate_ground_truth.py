"""Compare the current parser with immutable reviewed candidates in the database."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

from sqlalchemy import select

from app.db.models import Manual, Setting
from app.db.session import SessionLocal
from app.extraction.pdf_text import read_pdf_text
from app.extraction.settings_parser import PARSER_VERSION, parse_settings
from app.storage import files

_SPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class GroundTruthEvaluation:
    manual_id: int
    manual: str
    parser_version: str
    candidate_count: int
    reviewed_candidate_count: int
    approved_retained: int
    approved_total: int
    rejected_retained: int
    rejected_total: int
    new_candidate_count: int
    new_candidates: list[dict[str, object]]
    missing_approved: list[dict[str, object]]


def normalize_name(value: str) -> str:
    return _SPACE.sub(" ", value.strip()).casefold()


def evaluate_manual(manual: Manual, settings: list[Setting]) -> GroundTruthEvaluation:
    parsed = parse_settings(read_pdf_text(Path(files.manual_file_path(manual.stored_filename))))
    parsed_counter = Counter(
        (candidate.page_number, normalize_name(candidate.extracted_name)) for candidate in parsed
    )
    reviewed_counter = Counter(
        (setting.page_number, normalize_name(setting.extracted_name)) for setting in settings
    )
    approved_counter = Counter(
        (setting.page_number, normalize_name(setting.extracted_name))
        for setting in settings
        if setting.status == "approved"
    )
    rejected_counter = reviewed_counter - approved_counter
    retained_approved = parsed_counter & approved_counter
    retained_rejected = parsed_counter & rejected_counter
    new_counter = parsed_counter - reviewed_counter
    missing_approved_counter = approved_counter - parsed_counter

    display_by_key = {
        (candidate.page_number, normalize_name(candidate.extracted_name)): candidate.extracted_name
        for candidate in parsed
    }
    approved_display = {
        (setting.page_number, normalize_name(setting.extracted_name)): setting.extracted_name
        for setting in settings
        if setting.status == "approved"
    }
    return GroundTruthEvaluation(
        manual_id=manual.id,
        manual=f"{manual.vendor or ''} {manual.model_name or manual.document_title}".strip(),
        parser_version=PARSER_VERSION,
        candidate_count=len(parsed),
        reviewed_candidate_count=len(settings),
        approved_retained=retained_approved.total(),
        approved_total=approved_counter.total(),
        rejected_retained=retained_rejected.total(),
        rejected_total=rejected_counter.total(),
        new_candidate_count=new_counter.total(),
        new_candidates=[
            {"page": page, "name": display_by_key[(page, name)], "occurrences": count}
            for (page, name), count in sorted(new_counter.items())
        ],
        missing_approved=[
            {"page": page, "name": approved_display[(page, name)], "occurrences": count}
            for (page, name), count in sorted(missing_approved_counter.items())
        ],
    )


def evaluate_database() -> list[GroundTruthEvaluation]:
    with SessionLocal() as session:
        manuals = list(session.scalars(select(Manual).order_by(Manual.id)))
        return [
            evaluate_manual(
                manual,
                list(
                    session.scalars(
                        select(Setting).where(Setting.manual_id == manual.id).order_by(Setting.id)
                    )
                ),
            )
            for manual in manuals
        ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, help="Optional JSON report destination")
    args = parser.parse_args()
    report = {
        "parser_version": PARSER_VERSION,
        "manuals": [asdict(result) for result in evaluate_database()],
    }
    rendered = json.dumps(report, indent=2, ensure_ascii=True) + "\n"
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
