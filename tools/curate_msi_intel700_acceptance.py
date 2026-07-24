"""Apply the reviewed MSI Intel 700 BIOS-guide acceptance manifest.

Dry-run is the default. Pass ``--apply`` only against the pinned production
manual after taking a database backup. The script records every correction,
decision, page check, omission, and final certification through the normal
domain services so the acceptance history remains auditable.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from sqlalchemy import select

from app.db.models import ExtractionRun, Manual, Setting
from app.db.session import SessionLocal
from app.domain import acceptance, review

MANUAL_ID = 5
EXPECTED_SHA256 = "c59d17516e594f187bcb8d1a60b9e746f0a1849435f76ae93e726040f731af8e"
EXPECTED_DRAFTS = 612
EXPECTED_RUN_PARSER = "1.2"

# These parser candidates look like settings because MSI prefixes most menu
# entries with the same triangle glyph. The source text identifies them as
# submenus, informational displays, illustrations, or read-only values.
EXPLICIT_REJECTIONS = {
    4498: "Connected-device information display, not a configurable setting.",
    4500: "Read-only system information display.",
    4501: "Read-only DMI information display.",
    4503: "Submenu heading without a standalone value.",
    4514: "Submenu heading without a standalone value.",
    4525: "Submenu heading without a standalone value.",
    4529: "Submenu heading without a standalone value.",
    4540: "Submenu heading without a standalone value.",
    4563: "Submenu heading without a standalone value.",
    4567: "Submenu heading without a standalone value.",
    4573: "Submenu heading without a standalone value.",
    4582: "Submenu heading without a standalone value.",
    4585: "Submenu heading without a standalone value.",
    4586: "Submenu heading without a standalone value.",
    4587: "Submenu heading without a standalone value.",
    4590: "Submenu heading without a standalone value.",
    4594: "Submenu heading without a standalone value.",
    4600: "Submenu heading without a standalone value.",
    4615: "Ethernet-controller information display, not a configurable setting.",
    4634: "Submenu heading without a standalone value.",
    4645: "Submenu heading without a standalone value.",
    4647: "Submenu occurrence; the configurable Secure Boot item follows separately.",
    4652: "Key-management submenu heading without a standalone value.",
    4658: "Platform-key enrollment status, not a configurable setting.",
    4661: "Key-exchange-key enrollment status, not a configurable setting.",
    4665: "Authorized-signature database status, not a configurable setting.",
    4669: "Forbidden-signature database status, not a configurable setting.",
    4673: "Authorized-timestamp database status, not a configurable setting.",
    4676: "OS-recovery-signature database status, not a configurable setting.",
    4705: "Read-only adjusted frequency display.",
    4734: "Read-only adjusted frequency display.",
    4742: "Submenu heading without a standalone value.",
    4743: "Help-window illustration control, not a BIOS tuning setting.",
    4803: "Submenu heading without a standalone value.",
    4812: "Configuration-group heading without a standalone value.",
    4817: "Read-only adjusted frequency display.",
    4819: "Read-only adjusted frequency display.",
    4833: "Read-only adjusted frequency display.",
    4837: "Submenu heading without a standalone value.",
    4838: "Help-window illustration control, not a BIOS tuning setting.",
    4840: "Submenu heading without a standalone value.",
    4844: "Submenu heading without a standalone value.",
    4845: "Configuration-group heading without a standalone value.",
    4862: "Configuration-group heading without a standalone value.",
    4876: "Configuration-group heading without a standalone value.",
    4894: "Configuration-group heading without a standalone value.",
    4922: "Configuration-group heading without a standalone value.",
    4934: "Configuration-group heading without a standalone value.",
    4942: "Configuration-group heading without a standalone value.",
    4965: "Submenu heading without a standalone value.",
    4982: "Submenu heading without a standalone value.",
    5049: "Read-only CPU information submenu.",
    5050: "Read-only CPU-feature information submenu.",
    5051: "Memory-information display, not a configurable setting.",
    5052: "Read-only memory SPD information submenu.",
    5053: "Submenu heading without a standalone value.",
    5071: "Operations-group label; its three executable actions are recorded as omissions.",
}

NAME_EDITS = {
    4659: "PK Set New Key",
    4660: "PK Delete Key",
    4662: "KEK Set New Key",
    4663: "KEK Append Key",
    4664: "KEK Delete Key",
    4666: "DB Set New Key",
    4667: "DB Append Key",
    4668: "DB Delete Key",
    4670: "DBX Set New Key",
    4671: "DBX Append Key",
    4672: "DBX Delete Key",
    4674: "DBT Set New Key",
    4675: "DBT Append Key",
}

for setting_id in range(4708, 4716):
    NAME_EDITS[setting_id] = f"P-Core {setting_id - 4708} Ratio Limit"
for setting_id, core_range in zip(range(4737, 4741), ("0-3", "4-7", "8-11", "12-15"), strict=True):
    NAME_EDITS[setting_id] = f"E-Core {core_range} Ratio Limit"
for setting_id in range(4757, 4765):
    NAME_EDITS[setting_id] = f"P-Core {setting_id - 4757} Control"
for setting_id in range(4766, 4774):
    NAME_EDITS[setting_id] = f"E-Core {setting_id - 4766} Control"
for control_id, target_id, ratio in zip(
    range(4983, 4996, 2), range(4984, 4997, 2), (8, 25, 35, 43, 48, 50, 51), strict=True
):
    NAME_EDITS[control_id] = f"CPU Ratio x{ratio} Voltage Offset Control"
    NAME_EDITS[target_id] = f"CPU Ratio x{ratio} Voltage Offset Target"

# The unrecognized long MSI headings were appended to the preceding setting's
# description. These boundaries are corrected before approval.
DESCRIPTION_EDITS = {
    4546: (
        "Enables or disables Intel Rapid Recovery Technology. Intel® Rapid Recover "
        "Technology (Intel® RRT) is a feature of Intel® Rapid Storage Technology "
        "(Intel® RST). It uses RAID 1 (mirroring) functionality to copy data from a "
        "designated master drive to a designated recovery drive."
    ),
    4624: (
        "Enables or disables the Windows 10 fast boot feature. This item will only be "
        "available when MSI Fast Boot is disabled."
    ),
    **{setting_id: "Sets the offset value." for setting_id in range(4984, 4997, 2)},
}


@dataclass(frozen=True)
class MissedSpec:
    page: int
    name: str
    description: str


MISSED = [
    MissedSpec(
        18,
        "RRT Volumes Can Span Internal and eSATA Drives",
        "Enables or disables Intel RRT volumes spanning internal and eSATA drives.",
    ),
    MissedSpec(
        27,
        "Boot Option #1 through #7",
        "Specifies the boot-device priority sequence for boot options 1 through 7.",
    ),
    MissedSpec(
        66,
        "Set Name for Overclocking Profile 1 through 6",
        "Names the selected overclocking profile.",
    ),
    MissedSpec(
        66,
        "Save Overclocking Profile 1 through 6",
        "Saves the current overclocking profile.",
    ),
    MissedSpec(
        66,
        "Load Overclocking Profile 1 through 6",
        "Loads the selected overclocking profile.",
    ),
    MissedSpec(
        66,
        "Clear Overclocking Profile 1 through 6",
        "Clears the selected overclocking profile.",
    ),
    MissedSpec(67, "Hardware Monitor All Full Speed", "Runs all fans at full speed."),
    MissedSpec(
        67,
        "Hardware Monitor All Set Default",
        "Returns all fan-speed settings to their BIOS defaults.",
    ),
    MissedSpec(
        67,
        "Hardware Monitor All Set Cancel",
        "Discards current fan changes and restores the previous settings.",
    ),
]


def _clean_description(value: str, page_number: int) -> str:
    lines = [line.strip() for line in value.splitlines()]
    while lines and (not lines[-1] or lines[-1] == str(page_number)):
        lines.pop()
    return "\n".join(lines).strip()


def _rejection_reason(setting: Setting) -> str | None:
    if setting.id in EXPLICIT_REJECTIONS:
        return EXPLICIT_REJECTIONS[setting.id]
    if not setting.extracted_name.startswith("▶"):
        return "Document navigation, warning, explanatory text, or visual annotation."
    return None


def _load_pinned(session):
    manual = session.get(Manual, MANUAL_ID)
    if manual is None or manual.sha256 != EXPECTED_SHA256:
        raise RuntimeError("Manual 5 is not the pinned MSI Intel 700 BIOS guide.")
    run = session.scalar(select(ExtractionRun).where(ExtractionRun.manual_id == manual.id))
    if run is None or run.parser_version != EXPECTED_RUN_PARSER or run.status != "succeeded":
        raise RuntimeError("The pinned MSI Intel 700 extraction run is missing or has changed.")
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
    ids = {setting.id for setting in settings}
    referenced_ids = set(EXPLICIT_REJECTIONS) | set(NAME_EDITS) | set(DESCRIPTION_EDITS)
    if not referenced_ids.issubset(ids):
        raise RuntimeError("The MSI acceptance manifest references unknown setting IDs.")
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
            f"MSI Intel 700 acceptance manifest: {len(approved)} approve, "
            f"{len(rejected)} reject, {len(NAME_EDITS)} contextual name edits, "
            f"{len(DESCRIPTION_EDITS)} description boundary edits, "
            f"{len(MISSED)} missed settings."
        )
        if not args.apply:
            print("Dry run only; pass --apply after verifying the database backup.")
            return

        for setting in settings:
            reason = _rejection_reason(setting)
            if reason:
                review.reject_setting(session, setting, reason)
                continue
            extracted_name = setting.extracted_name.removeprefix("▶").strip()
            name = NAME_EDITS.get(setting.id, extracted_name)
            description = DESCRIPTION_EDITS.get(
                setting.id, _clean_description(setting.extracted_description, setting.page_number)
            )
            review.edit_draft_setting(session, setting, name=name, description=description)
            review.approve_setting(
                session,
                setting,
                note="Configurable or executable MSI BIOS item verified against the source page.",
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
                    "Confirmed parser-1.2 omission: the MSI heading was merged into adjacent "
                    "description text or represented only inside an operations group."
                ),
            )

        acceptance.certify_ground_truth(
            session,
            manual,
            notes=(
                "Full parser-1.2 candidate review and source-page omission audit of the "
                "71-page MSI Intel 700 series BIOS User Guide."
            ),
        )
        report = acceptance.build_report(session, manual)
        print(
            f"Applied: approved={report.approved_count}, rejected={report.rejected_count}, "
            f"missed={report.missed_count}, precision={report.precision:.4f}, "
            f"recall={report.estimated_recall:.4f}, certified={report.acceptance_complete}."
        )


if __name__ == "__main__":
    main()
