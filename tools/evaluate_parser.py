"""Dry-run TuneForge's parser against PDFs without touching the database."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

from app.extraction.pdf_text import read_pdf_text
from app.extraction.settings_parser import PARSER_VERSION, page_has_bios_signals, parse_settings


@dataclass(frozen=True)
class ParserEvaluation:
    path: str
    parser_version: str
    page_count: int
    bios_signal_pages: int
    candidate_count: int
    candidates_per_page: float
    candidates_with_empty_description: int
    repeated_candidate_names: int
    candidate_pages: int
    candidates_on_bios_signal_pages: int
    candidates_off_bios_signal_pages: int
    sample_candidate_names: list[str]
    sample_off_signal_names: list[str]


def evaluate_pdf(path: Path) -> ParserEvaluation:
    pdf = read_pdf_text(path)
    candidates = parse_settings(pdf)
    signal_page_numbers = {page.page_number for page in pdf.pages if page_has_bios_signals(page)}
    off_signal = [
        candidate for candidate in candidates if candidate.page_number not in signal_page_numbers
    ]
    normalized_names = Counter(candidate.extracted_name.casefold() for candidate in candidates)
    return ParserEvaluation(
        path=str(path),
        parser_version=PARSER_VERSION,
        page_count=pdf.page_count,
        bios_signal_pages=sum(page_has_bios_signals(page) for page in pdf.pages),
        candidate_count=len(candidates),
        candidates_per_page=round(len(candidates) / pdf.page_count, 3),
        candidates_with_empty_description=sum(
            not candidate.extracted_description.strip() for candidate in candidates
        ),
        repeated_candidate_names=sum(count - 1 for count in normalized_names.values() if count > 1),
        candidate_pages=len({candidate.page_number for candidate in candidates}),
        candidates_on_bios_signal_pages=len(candidates) - len(off_signal),
        candidates_off_bios_signal_pages=len(off_signal),
        sample_candidate_names=[candidate.extracted_name for candidate in candidates[:20]],
        sample_off_signal_names=[candidate.extracted_name for candidate in off_signal[:20]],
    )


def evaluate_paths(paths: list[Path], *, progress: bool = False) -> list[ParserEvaluation]:
    evaluations = []
    for index, path in enumerate(paths, start=1):
        if progress:
            print(f"[{index}/{len(paths)}] {path}", flush=True)
        evaluations.append(evaluate_pdf(path))
    return evaluations


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdfs", nargs="+", type=Path)
    parser.add_argument("--json", type=Path, help="Optional JSON report destination")
    args = parser.parse_args()
    evaluations = evaluate_paths(args.pdfs, progress=True)
    report = {
        "parser_version": PARSER_VERSION,
        "manuals": [asdict(evaluation) for evaluation in evaluations],
    }
    rendered = json.dumps(report, indent=2, ensure_ascii=True) + "\n"
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
