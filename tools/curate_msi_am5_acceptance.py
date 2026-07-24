"""Apply the reviewed MSI AMD AM5 BIOS-guide acceptance manifest.

Dry-run is the default. Pass ``--apply`` only against the pinned production
manual after taking a database backup.
"""

from __future__ import annotations

import argparse

from sqlalchemy import select

from app.db.models import ExtractionRun, Manual, Setting
from app.db.session import SessionLocal
from app.domain import acceptance, review

MANUAL_ID = 6
EXPECTED_SHA256 = "5ad148e9ec850442778a3cc090df281660712919299a1c4127d012f780ae622c"
EXPECTED_DRAFTS = 408
EXPECTED_RUN_PARSER = "1.3"

GROUP_IDS = {
    5114,
    5120,
    5123,
    5135,
    5139,
    5142,
    5143,
    5146,
    5150,
    5155,
    5165,
    5175,
    5185,
    5198,
    5200,
    5205,
    5230,
    5253,
    5255,
    5268,
    5272,
    5282,
    5330,
    5331,
    5337,
    5353,
    5372,
    5382,
    5389,
    5400,
    5410,
    5419,
    5439,
    5447,
    5476,
}
READ_ONLY_IDS = {
    5109,
    5111,
    5112,
    5166,
    5188,
    5189,
    5197,
    5211,
    5214,
    5218,
    5222,
    5226,
    5229,
    5243,
    5325,
    5458,
    5459,
    5460,
    5461,
    5462,
}
DUPLICATE_IDS = {5207, 5208}

NAME_EDITS = {
    5203: "Secure Boot Enroll All Factory Default Keys",
    5204: "Secure Boot Delete All Secure Boot Variables",
    5206: "Key Management Provision Factory Default Keys",
    5209: "Key Management Save All Secure Boot Variables",
    5210: "Key Management Enroll EFI Image",
    5212: "PK Set New Key",
    5213: "PK Delete Key",
    5215: "KEK Set New Key",
    5216: "KEK Append Key",
    5217: "KEK Delete Key",
    5219: "DB Set New Key",
    5220: "DB Append Key",
    5221: "DB Delete Key",
    5223: "DBX Set New Key",
    5224: "DBX Append Key",
    5225: "DBX Delete Key",
    5227: "DBT Set New Key",
    5228: "DBT Append Key",
    5334: "Write User Profile To Memory",
    5356: "Write User Profile To Memory",
    5413: "Processor ODT Impedance",
    5415: "DRAM ODT Impedance RTT_NOM_WR",
    5416: "DRAM ODT Impedance RTT_NOM_RD",
    5417: "DRAM ODT Impedance RTT_WR",
    5418: "DRAM ODT Impedance RTT_PARK",
    5477: "Hardware Monitor All Full Speed",
    5478: "Hardware Monitor All Set Default",
    5479: "Hardware Monitor All Set Cancel",
}


def _clean_description(value: str, page_number: int) -> str:
    lines = [line.strip() for line in value.splitlines()]
    while lines and (not lines[-1] or lines[-1] in {str(page_number), "BIOS Setup"}):
        lines.pop()
    if lines and lines[-1].endswith(f"BIOS Setup {page_number}"):
        lines.pop()
    return "\n".join(lines).strip()


def _rejection_reason(setting: Setting) -> str | None:
    if setting.id in GROUP_IDS:
        return "Menu, submenu, or configuration-group heading without a standalone value."
    if setting.id in READ_ONLY_IDS:
        return "Read-only status, device-information, or support-information display."
    if setting.id in DUPLICATE_IDS:
        return "Duplicate occurrence without additional setting semantics."
    if setting.extracted_name.startswith(("▶", "▪")):
        return None
    return "Document navigation, warning, shortcut, explanatory text, or illustration label."


def _load_pinned(session):
    manual = session.get(Manual, MANUAL_ID)
    if manual is None or manual.sha256 != EXPECTED_SHA256:
        raise RuntimeError("Manual 6 is not the pinned MSI AMD AM5 BIOS guide.")
    run = session.scalar(select(ExtractionRun).where(ExtractionRun.manual_id == manual.id))
    if run is None or run.parser_version != EXPECTED_RUN_PARSER or run.status != "succeeded":
        raise RuntimeError("The pinned MSI AMD AM5 extraction run is missing or changed.")
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
    referenced_ids = GROUP_IDS | READ_ONLY_IDS | DUPLICATE_IDS | set(NAME_EDITS)
    if not referenced_ids.issubset({setting.id for setting in settings}):
        raise RuntimeError("The MSI AM5 manifest references unknown setting IDs.")
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
            f"MSI AMD AM5 acceptance manifest: {len(approved)} approve, "
            f"{len(rejected)} reject, {len(NAME_EDITS)} contextual name edits."
        )
        if not args.apply:
            print("Dry run only; pass --apply after verifying the database backup.")
            return

        for setting in settings:
            reason = _rejection_reason(setting)
            if reason:
                review.reject_setting(session, setting, reason)
                continue
            name = NAME_EDITS.get(
                setting.id,
                setting.extracted_name.removeprefix("▶").removeprefix("▪").strip(),
            )
            description = _clean_description(setting.extracted_description, setting.page_number)
            review.edit_draft_setting(session, setting, name=name, description=description)
            review.approve_setting(
                session,
                setting,
                note="Configurable or executable MSI BIOS item verified on the source page.",
            )
            acceptance.review_page_reference(
                session, manual, setting.id, page_number=setting.page_number
            )

        acceptance.certify_ground_truth(
            session,
            manual,
            notes=(
                "Full parser-1.3 candidate review and visual source-page audit of the "
                "49-page MSI AMD AM5 series BIOS User Guide."
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
