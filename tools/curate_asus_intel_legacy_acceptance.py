"""Apply the reviewed ASUS Intel 600/700 acceptance manifest.

Dry-run is the default. Pass ``--apply`` only against the pinned production
manuals after verifying the pre-ingestion backup. Decisions use the normal
review and acceptance services so edits, decisions, page checks, omissions,
and certification remain in the immutable audit history.
"""

from __future__ import annotations

import argparse
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from app.db.models import ExtractionRun, Manual, MissedSetting, Setting
from app.db.session import SessionLocal
from app.domain import acceptance, review

EXPECTED_RUN_PARSER = "1.3"
BACKUP_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "knowledge_builder.pre-asus-intel-legacy.sqlite3.backup"
)
EXPECTED_BACKUP_SHA256 = "8146a3732ceb065f12acccc51ee439506774122e096d721ea674e91165e48c24"

_FOOTER_RE = re.compile(
    r"^(?:\d+\s+)?PRIME / ProArt / TUF GAMING Intel (?:600|700) Series "
    r"BIOS Manual(?:\s+\d+)?$"
)


@dataclass(frozen=True)
class ManualManifest:
    manual_id: int
    sha256: str
    page_count: int
    expected_drafts: int
    first_setting_id: int
    last_setting_id: int
    scaffolding_last_page: int
    group_ids: frozenset[int]
    read_only_ids: frozenset[int]
    fragment_ids: frozenset[int]
    allow_empty_ids: frozenset[int]
    name_edits: dict[int, str]
    description_edits: dict[int, str]
    missed_settings: tuple[dict[str, object], ...]
    approval_note: str
    certification_notes: str


INTEL_600 = ManualManifest(
    manual_id=11,
    sha256="4e3f39d81ae255a6dc8d48a52a31d0be2e496d457e3c6a17f7c689eb54cb57cc",
    page_count=88,
    expected_drafts=640,
    first_setting_id=7072,
    last_setting_id=7711,
    scaffolding_last_page=14,
    group_ids=frozenset(
        {
            7133,
            7172,
            7232,
            7366,
            7419,
            7486,
            7487,
            7494,
            7497,
            7509,
            7526,
            7528,
            7534,
            7538,
            7542,
            7557,
            7575,
            7592,
            7605,
            7693,
        }
    ),
    read_only_ids=frozenset(range(7596, 7605)),
    fragment_ids=frozenset({7343, 7353}),
    allow_empty_ids=frozenset(),
    name_edits={
        7149: "Performance Core Specific Offset Mode Sign",
        7152: "Performance Core ALL-Core Ratio Limit",
        7159: "Efficient Core Group Offset Mode Sign",
        7162: "Efficient Core ALL-Core Ratio Limit",
        7170: "AVX2 Voltage Guardband Scale Factor Mode",
        7171: "AVX2 Voltage Guardband Scale Factor Value",
        7435: "CPU Core Voltage Offset Mode Sign",
        7439: "CPU Graphics Voltage Offset Mode Sign",
        7443: "CPU L2 Voltage Offset Mode Sign",
        7660: "PK Save To File",
        7661: "PK Set New Key",
        7662: "PK Delete Key",
        7664: "KEK Save To File",
        7665: "KEK Set New Key",
        7666: "KEK Append Key",
        7667: "KEK Delete Key",
        7669: "DB Save To File",
        7670: "DB Set New Key",
        7671: "DB Append Key",
        7672: "DB Delete Key",
        7674: "DBX Save To File",
        7675: "DBX Set New Key",
        7676: "DBX Append Key",
        7677: "DBX Delete Key",
    },
    description_edits={},
    missed_settings=(
        {
            "name": "System Date",
            "page_number": 15,
            "description": "Sets the BIOS system date shown on the Main menu.",
            "note": "Visually confirmed screenshot-only label omitted by parser 1.3.",
        },
        {
            "name": "System Time",
            "page_number": 15,
            "description": "Sets the BIOS system time shown on the Main menu.",
            "note": "Visually confirmed screenshot-only label omitted by parser 1.3.",
        },
    ),
    approval_note=(
        "Configurable or executable ASUS Intel 600 BIOS item verified against "
        "the rendered source page."
    ),
    certification_notes=(
        "All 88 pages rendered and audited. Every parser-1.3 candidate was reviewed; "
        "cover/navigation scaffolding, option fragments, submenu headings, and read-only "
        "monitor fields were rejected. System Date and System Time were recorded as "
        "visually confirmed screenshot-only omissions."
    ),
)

INTEL_700 = ManualManifest(
    manual_id=12,
    sha256="49d55cb253e29b523cb69a78934d1df0f8160dd7feb9c68a76ceecb42927da37",
    page_count=94,
    expected_drafts=663,
    first_setting_id=7712,
    last_setting_id=8374,
    scaffolding_last_page=16,
    group_ids=frozenset(
        {
            7773,
            7812,
            7872,
            8018,
            8141,
            8142,
            8153,
            8157,
            8164,
            8183,
            8185,
            8191,
            8195,
            8196,
            8207,
            8232,
            8248,
            8261,
            8356,
        }
    ),
    read_only_ids=frozenset(range(8253, 8261)),
    fragment_ids=frozenset({8004, 8014}),
    allow_empty_ids=frozenset({7931, 7933, 7935, 7937, 7939, 7941, 7983, 7985}),
    name_edits={
        7789: "Performance Core ALL-Core Ratio Limit",
        7795: "Performance Core Specific Offset Mode Sign",
        7799: "Efficient Core ALL-Core Ratio Limit",
        7804: "Efficient Core Group Offset Mode Sign",
        7810: "AVX2 Voltage Guardband Scale Factor Mode",
        7811: "AVX2 Voltage Guardband Scale Factor Value",
        8076: "Actual VRM Core Voltage Override",
        8077: "Actual VRM Core Voltage Offset Mode Sign",
        8078: "Actual VRM Core Voltage Offset",
        8080: "Global Core SVID Voltage Override",
        8081: "Global Core SVID Voltage Offset Mode Sign",
        8083: "Global Core SVID Offset Voltage",
        8085: "Cache SVID Voltage Override",
        8086: "Cache SVID Voltage Offset Mode Sign",
        8088: "Cache SVID Offset Voltage",
        8091: "CPU Graphics Voltage Offset Mode Sign",
        8095: "CPU L2 Voltage Offset Mode Sign",
        8097: "CPU L2 Offset Voltage",
        8323: "PK Save To File",
        8324: "PK Set New Key",
        8325: "PK Delete Key",
        8327: "KEK Save To File",
        8328: "KEK Set New Key",
        8329: "KEK Append Key",
        8330: "KEK Delete Key",
        8332: "DB Save To File",
        8333: "DB Set New Key",
        8334: "DB Append Key",
        8335: "DB Delete Key",
        8337: "DBX Save To File",
        8338: "DBX Set New Key",
        8339: "DBX Append Key",
        8340: "DBX Delete Key",
    },
    description_edits={
        7931: "Configuration options: [Auto] [0] - [127]",
        7933: "Configuration options: [Auto] [0] - [127]",
        7935: "Configuration options: [Auto] [0] - [127]",
        7937: "Configuration options: [Auto] [0] - [127]",
        7939: "Configuration options: [Auto] [0] - [255]",
        7941: "Configuration options: [Auto] [0] - [255]",
        7977: (
            "Allows you to enable or disable Controller 0, Channel 1.\n"
            "Configuration options: [Enabled] [Disabled]"
        ),
        7979: (
            "Allows you to enable or disable Controller 1, Channel 0.\n"
            "Configuration options: [Enabled] [Disabled]"
        ),
        7981: (
            "Allows you to enable or disable Controller 1, Channel 1.\n"
            "Configuration options: [Enabled] [Disabled]"
        ),
        7983: "Configuration options: [Auto] [0] - [65533]",
        7985: "Configuration options: [Auto] [Disabled] [Enabled]",
        8174: (
            "Allows you to enable or disable CPU Power Management. Allows CPU to go to C "
            "states when it is not 100% utilized.\n"
            "Configuration options: [Auto] [Disabled] [Enabled]"
        ),
        8176: (
            "Allows you to enable or disable C1E. When enabled, the CPU slows down to "
            "minimum speed when all cores enter C-State.\n"
            "Configuration options: [Enabled] [Disabled]"
        ),
    },
    missed_settings=(
        {
            "name": "System Date",
            "page_number": 17,
            "description": "Sets the BIOS system date shown on the Main menu.",
            "note": "Visually confirmed screenshot-only label omitted by parser 1.3.",
        },
        {
            "name": "System Time",
            "page_number": 17,
            "description": "Sets the BIOS system time shown on the Main menu.",
            "note": "Visually confirmed screenshot-only label omitted by parser 1.3.",
        },
        {
            "name": "CPU Core Current [xx A]",
            "page_number": 73,
            "description": (
                "The onboard hardware monitor detects current output; select Ignore to "
                "skip detection."
            ),
            "note": "Visually confirmed monitor label omitted by parser 1.3.",
        },
    ),
    approval_note=(
        "Configurable or executable ASUS Intel 700 BIOS item verified against "
        "the rendered source page."
    ),
    certification_notes=(
        "All 94 pages rendered and audited. Every parser-1.3 candidate was reviewed; "
        "cover/navigation scaffolding, option fragments, submenu headings, and read-only "
        "monitor fields were rejected. Eight split option descriptions were reconstructed. "
        "System Date, System Time, and CPU Core Current were recorded as visually "
        "confirmed omissions."
    ),
)

MANIFESTS = (INTEL_600, INTEL_700)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _clean_description(value: str, page_number: int) -> str:
    lines = [line.strip() for line in value.splitlines()]
    while lines and (
        not lines[-1]
        or lines[-1] == str(page_number)
        or _FOOTER_RE.fullmatch(lines[-1]) is not None
    ):
        lines.pop()
    return "\n".join(lines).strip()


def _rejection_reason(setting: Setting, manifest: ManualManifest) -> str | None:
    if setting.page_number <= manifest.scaffolding_last_page:
        return "Document cover, navigation, screenshot annotation, or explanatory scaffolding."
    if setting.id in manifest.group_ids:
        return "Menu or subgroup heading without a standalone configurable value."
    if setting.id in manifest.read_only_ids:
        return "Read-only temperature, fan-speed, voltage, or current monitor display."
    if setting.id in manifest.fragment_ids:
        return "Parser-created option-list fragment detached from its setting label."
    if setting.extracted_name.lstrip().startswith(("[", "(", "Configuration options:")):
        return "Parser-created option-list or menu-path fragment."
    if not (setting.extracted_description or "").strip() and setting.id not in (
        manifest.allow_empty_ids
    ):
        return "Menu, section, subgroup, duplicate heading, or wrapped option fragment."
    return None


def _load_pinned(session, manifest: ManualManifest) -> tuple[Manual, list[Setting]]:
    manual = session.get(Manual, manifest.manual_id)
    if (
        manual is None
        or manual.sha256 != manifest.sha256
        or manual.page_count != manifest.page_count
    ):
        raise RuntimeError(f"Manual {manifest.manual_id} is not the pinned ASUS guide.")
    run = session.scalar(select(ExtractionRun).where(ExtractionRun.manual_id == manual.id))
    if (
        run is None
        or run.parser_version != EXPECTED_RUN_PARSER
        or run.status != "succeeded"
        or run.settings_found != manifest.expected_drafts
    ):
        raise RuntimeError(f"Manual {manual.id}'s pinned extraction run is missing or changed.")
    settings = list(
        session.scalars(
            select(Setting)
            .where(Setting.manual_id == manual.id)
            .order_by(Setting.page_number, Setting.id)
        )
    )
    expected_ids = set(range(manifest.first_setting_id, manifest.last_setting_id + 1))
    actual_ids = {setting.id for setting in settings}
    if len(settings) != manifest.expected_drafts or actual_ids != expected_ids:
        raise RuntimeError(f"Manual {manual.id}'s candidate set is missing or changed.")
    referenced_ids = (
        manifest.group_ids
        | manifest.read_only_ids
        | manifest.fragment_ids
        | manifest.allow_empty_ids
        | set(manifest.name_edits)
        | set(manifest.description_edits)
    )
    if not referenced_ids.issubset(actual_ids):
        raise RuntimeError(f"Manual {manual.id}'s manifest references unknown setting IDs.")
    return manual, settings


def _verify_backup() -> None:
    if not BACKUP_PATH.is_file():
        raise RuntimeError(f"Verified pre-ingestion backup is missing: {BACKUP_PATH}")
    if _sha256(BACKUP_PATH) != EXPECTED_BACKUP_SHA256:
        raise RuntimeError("The pre-ingestion backup hash does not match the reviewed checkpoint.")


def _add_missing_omissions(session, manual: Manual, manifest: ManualManifest) -> None:
    existing = {
        (item.page_number, item.name.casefold())
        for item in session.scalars(
            select(MissedSetting).where(
                MissedSetting.manual_id == manual.id,
                MissedSetting.status == acceptance.STATUS_ACTIVE,
            )
        )
    }
    for item in manifest.missed_settings:
        key = (int(item["page_number"]), str(item["name"]).casefold())
        if key not in existing:
            acceptance.add_missed_setting(session, manual, **item)


def _apply_manifest(
    session,
    manual: Manual,
    settings: list[Setting],
    manifest: ManualManifest,
) -> None:
    for setting in settings:
        reason = _rejection_reason(setting, manifest)
        expected_status = "rejected" if reason else "approved"
        if setting.status == "draft":
            if reason:
                review.reject_setting(session, setting, reason)
                continue
            name = manifest.name_edits.get(setting.id, setting.extracted_name)
            description = manifest.description_edits.get(
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
            review.approve_setting(session, setting, note=manifest.approval_note)
        elif setting.status != expected_status:
            raise RuntimeError(
                f"Setting {setting.id} is {setting.status}, expected {expected_status}; "
                "refusing to overwrite prior review work."
            )

        if expected_status == "approved" and setting.page_review_status == acceptance.PAGE_PENDING:
            acceptance.review_page_reference(
                session,
                manual,
                setting.id,
                page_number=setting.page_number,
            )

    _add_missing_omissions(session, manual, manifest)
    acceptance.certify_ground_truth(session, manual, notes=manifest.certification_notes)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Commit the pinned manifests")
    args = parser.parse_args()

    _verify_backup()
    with SessionLocal() as session:
        pinned = [(manifest, *_load_pinned(session, manifest)) for manifest in MANIFESTS]
        for manifest, manual, settings in pinned:
            rejected = [setting for setting in settings if _rejection_reason(setting, manifest)]
            approved = [setting for setting in settings if not _rejection_reason(setting, manifest)]
            approved_empty = {
                setting.id
                for setting in approved
                if not (setting.extracted_description or "").strip()
            }
            if approved_empty != manifest.allow_empty_ids:
                raise RuntimeError(
                    f"Manual {manual.id} would approve an unreviewed empty-description candidate."
                )
            print(
                f"Manual {manual.id}: {len(approved)} approve, {len(rejected)} reject, "
                f"{len(manifest.name_edits)} name edits, "
                f"{len(manifest.description_edits)} description edits, "
                f"{len(manifest.missed_settings)} omissions."
            )

        if not args.apply:
            print("Dry run only; pass --apply after reviewing these pinned manifests.")
            return

        for manifest, manual, settings in pinned:
            _apply_manifest(session, manual, settings, manifest)
            report = acceptance.build_report(session, manual)
            print(
                f"Applied manual {manual.id}: approved={report.approved_count}, "
                f"rejected={report.rejected_count}, missed={report.missed_count}, "
                f"precision={report.precision:.4f}, "
                f"recall={report.estimated_recall:.4f}, "
                f"page_accuracy={report.page_accuracy:.4f}, "
                f"certified={report.acceptance_complete}."
            )


if __name__ == "__main__":
    main()
