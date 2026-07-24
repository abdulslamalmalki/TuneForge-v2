"""Apply the pinned ASUS TUF GAMING X870-PLUS WIFI acceptance manifest.

The source is a full user manual, so the manifest is intentionally narrow.
Three exact-model entries are corrected and approved, one parser omission is
recorded, all remaining candidates are rejected, and the audit is certified.
Dry-run is the default; pass ``--apply`` to record normal append-only actions.
"""

from __future__ import annotations

import argparse

from sqlalchemy import select

from app.db.models import ExtractionRun, Manual, Setting
from app.db.session import SessionLocal
from app.domain import acceptance, review

EXPECTED_SHA256 = "d2ae999ad28ab15b3aa3e639df1c8025e5be055908b67f0e342894a265fb4300"
EXPECTED_CANDIDATES = 345
EXPECTED_RUN_PARSER = "1.3"

APPROVED_EDITS = {
    (8, "ASUS Enhanced Memory Profile (AEMP)"): (
        "ASUS Enhanced Memory Profile (AEMP)",
        "The TUF GAMING X870-PLUS WIFI specification lists support for ASUS "
        "Enhanced Memory Profile (AEMP).",
    ),
    (45, "BIOS menu screen"): (
        "Setup Mode",
        "Selects between EZ Mode and Advanced Mode. ASUS documents it in the Boot menu "
        "and identifies F7 as the corresponding hotkey.",
    ),
    (46, "CAUTION!"): (
        "ASUS EZ Flash 3 Utility",
        "Updates the BIOS without an operating-system utility. ASUS documents it in the "
        "Tool menu while BIOS Setup is in Advanced Mode.",
    ),
}


def _rejection_reason(setting: Setting) -> str:
    if setting.page_number in {45, 46, 47}:
        return (
            "BIOS chapter navigation, safety text, procedure, or section heading; "
            "not a standalone BIOS setting after exact-page review."
        )
    if 8 <= setting.page_number <= 14:
        return (
            "Motherboard specification or capability-table content; not a standalone "
            "BIOS setting after exact-page review."
        )
    return "Full user-manual hardware, installation, support, or appendix content."


def _load_pinned(session):
    manual = session.scalar(select(Manual).where(Manual.sha256 == EXPECTED_SHA256))
    if manual is None:
        raise RuntimeError("The pinned ASUS TUF X870 manual is not ingested.")
    run = session.scalar(select(ExtractionRun).where(ExtractionRun.manual_id == manual.id))
    if run is None or run.parser_version != EXPECTED_RUN_PARSER or run.status != "succeeded":
        raise RuntimeError("The pinned ASUS extraction run is missing or changed.")
    settings = list(
        session.scalars(
            select(Setting)
            .where(Setting.manual_id == manual.id)
            .order_by(Setting.page_number, Setting.id)
        )
    )
    drafts = [setting for setting in settings if setting.status == "draft"]
    if len(settings) != EXPECTED_CANDIDATES or len(drafts) != EXPECTED_CANDIDATES:
        raise RuntimeError(
            f"Expected {EXPECTED_CANDIDATES} untouched drafts; found "
            f"{len(settings)} settings and {len(drafts)} drafts."
        )
    keys = {(setting.page_number, setting.extracted_name) for setting in settings}
    if not APPROVED_EDITS.keys() <= keys:
        missing = sorted(APPROVED_EDITS.keys() - keys)
        raise RuntimeError(f"Pinned approval candidates are missing: {missing}")
    return manual, settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Commit the pinned manifest")
    args = parser.parse_args()

    with SessionLocal() as session:
        manual, settings = _load_pinned(session)
        print(
            f"ASUS TUF X870 manifest: {len(APPROVED_EDITS)} approve, "
            f"{len(settings) - len(APPROVED_EDITS)} reject, 1 parser omission."
        )
        if not args.apply:
            print("Dry run only; pass --apply after verifying the database backup.")
            return

        approved = []
        for setting in settings:
            key = (setting.page_number, setting.extracted_name)
            edit = APPROVED_EDITS.get(key)
            if edit is None:
                review.reject_setting(session, setting, _rejection_reason(setting))
                continue
            name, description = edit
            review.edit_draft_setting(session, setting, name, description)
            review.approve_setting(
                session,
                setting,
                note=(
                    "Exact-model ASUS source; effective text corrected to the named BIOS "
                    "entry directly visible on the cited page."
                ),
            )
            acceptance.review_page_reference(
                session,
                manual,
                setting.id,
                page_number=setting.page_number,
            )
            approved.append(setting)

        acceptance.add_missed_setting(
            session,
            manual,
            name="Load Optimized Defaults",
            page_number=45,
            description=(
                "Loads the BIOS default settings. ASUS places the item under the Exit menu "
                "and documents F5 as its hotkey."
            ),
            note="Visually confirmed parser-1.3 omission in the exact-model BIOS chapter.",
        )
        acceptance.certify_ground_truth(
            session,
            manual,
            notes=(
                "Full 64-page exact-model manual audited visually. Three narrowly supported "
                "BIOS entries were corrected and approved from pages 8, 45, and 46; all "
                "hardware/manual scaffolding candidates were rejected. Load Optimized "
                "Defaults on page 45 is retained as an active parser omission. The source "
                "proves only the approved entries for this exact motherboard and does not "
                "promote the separate AMD 800 Series BIOS guide to exact-model scope."
            ),
        )
        report = acceptance.build_report(session, manual)
        if not report.acceptance_complete or report.approved_count != len(APPROVED_EDITS):
            raise RuntimeError("The ASUS exact-model acceptance result did not certify cleanly.")
        print(
            f"Applied manual={manual.id}: approved={report.approved_count}, "
            f"rejected={report.rejected_count}, missed={report.missed_count}, "
            f"precision={report.precision:.4f}, recall={report.estimated_recall:.4f}, "
            f"certified={report.acceptance_complete}."
        )


if __name__ == "__main__":
    main()
