"""Reject and certify the pinned MSI X670E multilingual user-manual extraction.

The 534-page document is a full motherboard user guide, not a BIOS setting
guide. Dry-run is the default; ``--apply`` records one normal audited rejection
per draft and certifies the completed negative acceptance case.
"""

from __future__ import annotations

import argparse

from sqlalchemy import select

from app.db.models import ExtractionRun, Manual, Setting
from app.db.session import SessionLocal
from app.domain import acceptance, review

MANUAL_ID = 4
EXPECTED_SHA256 = "3b0a2321c463f71f1df15db5833e9c7f4a4905e870ccb6420cbe1586622e98ea"
EXPECTED_DRAFTS = 3175
EXPECTED_RUN_PARSER = "1.1"


def rejection_reason(page_number: int) -> str:
    if page_number <= 64:
        return "Full user-manual hardware or software content, not a BIOS setting."
    if page_number <= 67:
        return "BIOS entry, reset, or update instruction, not a configurable setting."
    if page_number <= 521:
        return "Translated duplicate user-manual content outside the English curation scope."
    return "Block diagram, regulatory, legal, or support appendix content."


def _load_pinned(session):
    manual = session.get(Manual, MANUAL_ID)
    if manual is None or manual.sha256 != EXPECTED_SHA256:
        raise RuntimeError("Manual 4 is not the pinned MSI MPG X670E CARBON WIFI guide.")
    run = session.scalar(select(ExtractionRun).where(ExtractionRun.manual_id == manual.id))
    if run is None or run.parser_version != EXPECTED_RUN_PARSER or run.status != "succeeded":
        raise RuntimeError("The pinned MSI extraction run is missing or has changed.")
    settings = list(
        session.scalars(
            select(Setting)
            .where(Setting.manual_id == manual.id)
            .order_by(Setting.page_number, Setting.id)
        )
    )
    drafts = [setting for setting in settings if setting.status == "draft"]
    if len(settings) != EXPECTED_DRAFTS or len(drafts) != EXPECTED_DRAFTS:
        raise RuntimeError(
            f"Expected {EXPECTED_DRAFTS} untouched MSI drafts; found "
            f"{len(settings)} settings and {len(drafts)} drafts."
        )
    return manual, settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Commit the pinned manifest")
    args = parser.parse_args()

    with SessionLocal() as session:
        manual, settings = _load_pinned(session)
        buckets = {
            "english_non_bios": sum(setting.page_number <= 64 for setting in settings),
            "bios_instructions": sum(65 <= setting.page_number <= 67 for setting in settings),
            "translations": sum(68 <= setting.page_number <= 521 for setting in settings),
            "appendices": sum(setting.page_number >= 522 for setting in settings),
        }
        print(f"MSI negative acceptance manifest: {len(settings)} reject; buckets={buckets}.")
        if not args.apply:
            print("Dry run only; pass --apply after verifying the database backup.")
            return

        for setting in settings:
            review.reject_setting(session, setting, rejection_reason(setting.page_number))
        acceptance.certify_ground_truth(
            session,
            manual,
            notes=(
                "Full 534-page multilingual user manual audited. It contains hardware and "
                "software instructions plus BIOS entry/update procedures, but no BIOS setting "
                "catalog. All parser-1.1 candidates are non-settings; no omissions apply."
            ),
        )
        report = acceptance.build_report(session, manual)
        print(
            f"Applied: approved={report.approved_count}, rejected={report.rejected_count}, "
            f"missed={report.missed_count}, precision={report.precision:.4f}, "
            f"certified={report.acceptance_complete}."
        )


if __name__ == "__main__":
    main()
