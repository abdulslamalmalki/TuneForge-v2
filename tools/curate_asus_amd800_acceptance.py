"""Apply the reviewed ASUS AMD 800 acceptance manifest to manual 8.

Dry-run is the default. Pass ``--apply`` only against the pinned production
manual after verifying the pre-ingestion backup. Decisions use the normal
review and acceptance services so every edit, decision, page check, and
certification remains in the immutable audit history.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from sqlalchemy import select

from app.db.models import ExtractionRun, Manual, Setting
from app.db.session import SessionLocal
from app.domain import acceptance, review

MANUAL_ID = 8
EXPECTED_SHA256 = "2e7a4b2d504e9f9286b584fcfbe9cba85e0ccd4a061ad545025160408b308213"
EXPECTED_DRAFTS = 1032
EXPECTED_RUN_PARSER = "1.3"
BACKUP_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "knowledge_builder.pre-asus-amd800.sqlite3.backup"
)

# These have explanatory text but are still menus/submenus without a
# standalone value. Empty-description headings are handled separately.
GROUP_IDS = {
    5773,  # Security
    5787,  # CPU Core Ratio (Per CCX)
    5801,  # DRAM Timing Control
    6120,  # USB Single Port Control
    6149,  # Serial Port Configuration
    6152,  # PCIE Link Speed
    6421,  # USB4 configuration (ASM4242 Controller)
    6679,  # CSM (Compatibility Support Module)
    6685,  # Secure Boot
    6692,  # PK Management
    6696,  # KEK Management
    6701,  # DB Management
    6706,  # DBX Management
}
READ_ONLY_IDS = {6068}  # Processor Utilization
ALLOW_EMPTY_IDS = {6360}  # UMA Version; its option prose was parsed as fragments.

NAME_EDITS = {
    5772: "System Time",
    5785: "CPU Core Ratio Control Mode",
    5786: "CPU Core Ratio Value",
    5950: "Ai Tweaker Precision Boost Overdrive",
    5962: "Per-Core Boost Clock Limit Value",
    5963: "Per-Core Boost Clock Limit Core 0-15",
    5964: "Platform Thermal Throttle Control",
    5965: "Platform Thermal Throttle Limit",
    5967: "Ai Tweaker Curve Optimizer",
    5973: "Ai Tweaker GFX Curve Optimizer",
    6360: "UMA Version",
    6377: "AMD CBS ECO Mode",
    6401: "AMD CBS VDDP Voltage Control",
    6403: "AMD CBS Infinity Fabric Frequency and Dividers",
    6438: "AMD PBS SMT Control",
    6538: "AMD Overclocking Infinity Fabric Frequency and Dividers",
    6541: "AMD Overclocking Precision Boost Overdrive",
    6554: "AMD Overclocking GFX Curve Optimizer",
    6558: "AMD Overclocking Curve Optimizer",
    6572: "AMD Overclocking VDDP Voltage Control",
    6595: "AMD Overclocking ECO Mode",
    6693: "PK Save To File",
    6694: "PK Set New Key",
    6695: "PK Delete Key",
    6697: "KEK Save To File",
    6698: "KEK Set New Key",
    6699: "KEK Append Key",
    6700: "KEK Delete Key",
    6702: "DB Save To File",
    6703: "DB Set New Key",
    6704: "DB Append Key",
    6705: "DB Delete Key",
    6707: "DBX Save To File",
    6708: "DBX Set New Key",
    6709: "DBX Append Key",
    6710: "DBX Delete Key",
}

DESCRIPTION_EDITS = {
    6360: ("Selects the UMA implementation: Auto, Legacy, Non-Legacy, or Hybrid Secure.")
}


def _clean_description(value: str, page_number: int) -> str:
    lines = [line.strip() for line in value.splitlines()]
    while lines and (not lines[-1] or lines[-1] == str(page_number)):
        lines.pop()
    return "\n".join(lines).strip()


def _rejection_reason(setting: Setting) -> str | None:
    if setting.page_number <= 14:
        return "Document cover, navigation, screenshot annotation, or explanatory scaffolding."
    if setting.page_number == 94:
        return "Read-only temperature, fan-speed, voltage, or current monitor display."
    if setting.id in GROUP_IDS:
        return "Menu or subgroup heading without a standalone configurable value."
    if setting.id in READ_ONLY_IDS:
        return "Read-only status or information field."
    if setting.extracted_name.lstrip().startswith(("[", "(")):
        return "Parser-created option-list or menu-path fragment."
    if not (setting.extracted_description or "").strip() and setting.id not in ALLOW_EMPTY_IDS:
        return "Menu, section, subgroup, duplicate heading, or wrapped option fragment."
    return None


def _load_pinned(session):
    manual = session.get(Manual, MANUAL_ID)
    if manual is None or manual.sha256 != EXPECTED_SHA256:
        raise RuntimeError("Manual 8 is not the pinned ASUS AMD 800 guide.")
    run = session.scalar(select(ExtractionRun).where(ExtractionRun.manual_id == manual.id))
    if (
        run is None
        or run.parser_version != EXPECTED_RUN_PARSER
        or run.status != "succeeded"
        or run.settings_found != EXPECTED_DRAFTS
    ):
        raise RuntimeError("The pinned ASUS AMD 800 extraction run is missing or changed.")
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
            f"Expected {EXPECTED_DRAFTS} untouched drafts; found "
            f"{len(settings)} settings and {len(drafts)} drafts."
        )
    ids = {setting.id for setting in settings}
    referenced_ids = GROUP_IDS | READ_ONLY_IDS | ALLOW_EMPTY_IDS | set(NAME_EDITS)
    if not referenced_ids.issubset(ids):
        raise RuntimeError("The acceptance manifest references unknown setting IDs.")
    if not BACKUP_PATH.is_file():
        raise RuntimeError(f"Verified pre-ingestion backup is missing: {BACKUP_PATH}")
    return manual, settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Commit the pinned manifest")
    args = parser.parse_args()

    with SessionLocal() as session:
        manual, settings = _load_pinned(session)
        rejected = [setting for setting in settings if _rejection_reason(setting)]
        approved = [setting for setting in settings if not _rejection_reason(setting)]
        approved_empty = [
            setting for setting in approved if not (setting.extracted_description or "").strip()
        ]
        if {setting.id for setting in approved_empty} != ALLOW_EMPTY_IDS:
            raise RuntimeError("An unreviewed empty-description candidate would be approved.")
        print(
            f"ASUS AMD 800 manifest: {len(approved)} approve, {len(rejected)} reject, "
            f"{len(NAME_EDITS)} name edits, {len(DESCRIPTION_EDITS)} reconstructed description."
        )
        if not args.apply:
            print("Dry run only; pass --apply after verifying the production backup.")
            return

        for setting in settings:
            reason = _rejection_reason(setting)
            if reason:
                review.reject_setting(session, setting, reason)
                continue

            name = NAME_EDITS.get(setting.id, setting.extracted_name)
            description = DESCRIPTION_EDITS.get(
                setting.id,
                _clean_description(setting.extracted_description, setting.page_number),
            )
            if name != setting.extracted_name or description != setting.extracted_description:
                review.edit_draft_setting(
                    session,
                    setting,
                    name=name,
                    description=description,
                )
            review.approve_setting(
                session,
                setting,
                note=(
                    "Configurable or executable ASUS AMD 800 BIOS item verified "
                    "against the rendered source page."
                ),
            )
            acceptance.review_page_reference(
                session,
                manual,
                setting.id,
                page_number=setting.page_number,
            )

        acceptance.certify_ground_truth(
            session,
            manual,
            notes=(
                "All 116 pages rendered and audited. Every parser-1.3 candidate was "
                "reviewed; cover/navigation scaffolding, option fragments, submenu "
                "headings, and read-only monitor fields were rejected. No additional "
                "setting-label omissions were found in the ASUS medium-weight BIOS catalog."
            ),
        )
        report = acceptance.build_report(session, manual)
        print(
            f"Applied: approved={report.approved_count}, rejected={report.rejected_count}, "
            f"missed={report.missed_count}, precision={report.precision:.4f}, "
            f"recall={report.estimated_recall:.4f}, page_accuracy={report.page_accuracy:.4f}, "
            f"certified={report.acceptance_complete}."
        )


if __name__ == "__main__":
    main()
