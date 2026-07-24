"""Apply the reviewed GIGABYTE Intel 800 BIOS-guide acceptance manifest.

Dry-run is the default. Pass ``--apply`` only against the pinned production
manual after taking a database backup.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from sqlalchemy import select

from app.db.models import ExtractionRun, Manual, Setting
from app.db.session import SessionLocal
from app.domain import acceptance, review

MANUAL_ID = 7
EXPECTED_SHA256 = "b46c52231334bfe914491cc3d6aa9265631914ba9c1de6bb6aa11841b46a2fe5"
EXPECTED_DRAFTS = 221
EXPECTED_RUN_PARSER = "1.3"

READ_ONLY_IDS = {
    5499,
    5500,
    5501,
    5637,
    5646,
    5677,
    5678,
    5679,
    5680,
    5684,
}
UTILITY_IDS = {5685}
NAME_EDITS = {
    5542: "FLL OC Mode",
    5618: "Onboard LAN Controller",
    5619: "Onboard LAN Controller #2",
    5665: "GEN5 Equalization",
}
DESCRIPTION_EDITS = {5495: "Sets the system time."}


@dataclass(frozen=True)
class MissedSpec:
    page: int
    name: str
    description: str


MISSED = [
    MissedSpec(
        page=13,
        name="Power Limit 1 (Watts) / Power Limit Time",
        description=(
            "Sets the CPU package power limit and the time window used to calculate "
            "average power consumption."
        ),
    )
]


def _clean_description(value: str, page_number: int) -> str:
    lines = [line.strip() for line in value.splitlines()]
    while lines and (not lines[-1] or lines[-1] in {str(page_number), f"- {page_number} -"}):
        lines.pop()
    return "\n".join(lines).strip()


def _rejection_reason(setting: Setting) -> str | None:
    if setting.id in READ_ONLY_IDS:
        return "Read-only sensor, status, device-information, or access-level display."
    if setting.id in UTILITY_IDS:
        return "Firmware utility launcher, not a configurable BIOS setting."
    if setting.id == 5495 or setting.extracted_name.startswith("&"):
        return None
    return "Document navigation, menu/submenu heading, explanatory text, or parser fragment."


def _load_pinned(session):
    manual = session.get(Manual, MANUAL_ID)
    if manual is None or manual.sha256 != EXPECTED_SHA256:
        raise RuntimeError("Manual 7 is not the pinned GIGABYTE Intel 800 BIOS guide.")
    run = session.scalar(select(ExtractionRun).where(ExtractionRun.manual_id == manual.id))
    if run is None or run.parser_version != EXPECTED_RUN_PARSER or run.status != "succeeded":
        raise RuntimeError("The pinned GIGABYTE Intel 800 extraction run is missing or changed.")
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
            f"Expected {EXPECTED_DRAFTS} untouched GIGABYTE drafts; found "
            f"{len(settings)} settings and {len(drafts)} drafts."
        )
    referenced_ids = READ_ONLY_IDS | UTILITY_IDS | set(NAME_EDITS) | set(DESCRIPTION_EDITS)
    if not referenced_ids.issubset({setting.id for setting in settings}):
        raise RuntimeError("The GIGABYTE Intel 800 manifest references unknown setting IDs.")
    return manual, settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Commit the pinned manifest")
    args = parser.parse_args()

    with SessionLocal() as session:
        manual, settings = _load_pinned(session)
        rejected = [setting for setting in settings if _rejection_reason(setting)]
        approved = [setting for setting in settings if not _rejection_reason(setting)]
        print(
            f"GIGABYTE Intel 800 acceptance manifest: {len(approved)} approve, "
            f"{len(rejected)} reject, {len(NAME_EDITS)} name edits, "
            f"{len(MISSED)} missed setting."
        )
        if not args.apply:
            print("Dry run only; pass --apply after verifying the database backup.")
            return

        for setting in settings:
            reason = _rejection_reason(setting)
            if reason:
                review.reject_setting(session, setting, reason)
                continue
            name = NAME_EDITS.get(setting.id, setting.extracted_name.removeprefix("&").strip())
            description = DESCRIPTION_EDITS.get(
                setting.id,
                _clean_description(setting.extracted_description, setting.page_number),
            )
            review.edit_draft_setting(session, setting, name=name, description=description)
            review.approve_setting(
                session,
                setting,
                note=("Configurable or executable GIGABYTE BIOS item verified on the source page."),
            )
            acceptance.review_page_reference(
                session, manual, setting.id, page_number=setting.page_number
            )

        for spec in MISSED:
            acceptance.add_missed_setting(
                session,
                manual,
                name=spec.name,
                page_number=spec.page,
                description=spec.description,
                note=(
                    "Confirmed parser-1.3 omission during visual source-page review; "
                    "the slash-separated heading was not emitted as a candidate."
                ),
            )

        acceptance.certify_ground_truth(
            session,
            manual,
            notes=(
                "Full parser-1.3 candidate review and visual source-page omission audit "
                "of the 30-page GIGABYTE Intel 800 Series BIOS Setup guide."
            ),
        )
        report = acceptance.build_report(session, manual)
        print(
            f"Applied: approved={report.approved_count}, rejected={report.rejected_count}, "
            f"missed={report.missed_count}, precision={report.precision:.4f}, "
            f"recall={report.estimated_recall:.4f}, "
            f"certified={report.acceptance_complete}."
        )


if __name__ == "__main__":
    main()
