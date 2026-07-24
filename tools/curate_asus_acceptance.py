"""Apply the reviewed ASUS Intel 800 acceptance manifest to manual 3.

Dry-run is the default. Pass ``--apply`` only against the pinned production
manual after taking a database backup. The script uses the normal domain
services so edits, decisions, page checks, missed settings, and certification
all retain their ordinary audit history.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from app.db.models import ExtractionRun, Manual, Setting
from app.db.session import SessionLocal
from app.domain import acceptance, review
from app.extraction.pdf_text import read_pdf_text
from app.extraction.settings_parser import PARSER_VERSION, SettingCandidate, parse_settings
from app.storage import files

MANUAL_ID = 3
EXPECTED_SHA256 = "586c9e7fc4a78e22afc5234b3d5f13511b9e923131a76f4af963bd4a50293054"
EXPECTED_DRAFTS = 671
EXPECTED_RUN_PARSER = "1.1"

DOCUMENT_IDS = set(range(625, 677)) | {1287, 1289, 1295}
GROUP_IDS = {
    680,
    696,
    705,
    715,
    718,
    722,
    727,
    734,
    735,
    751,
    770,
    784,
    838,
    839,
    860,
    883,
    885,
    906,
    923,
    927,
    932,
    936,
    941,
    945,
    952,
    966,
    987,
    1037,
    1045,
    1066,
    1070,
    1083,
    1094,
    1096,
    1100,
    1101,
    1102,
    1111,
    1124,
    1136,
    1141,
    1150,
    1152,
    1167,
    1170,
    1174,
    1177,
    1178,
    1180,
    1204,
    1253,
    1256,
    1257,
    1259,
    1261,
    1263,
    1265,
}
FRAGMENT_IDS = {
    736,
    752,
    863,
    874,
    879,
    910,
    1159,
    1175,
    1176,
    1179,
    1208,
    1212,
    1233,
}
READ_ONLY_IDS = {864, 988, 1073, 1074}
DUPLICATE_IDS = {719}

REJECTION_REASON_BY_ID = {
    **{
        setting_id: "Document navigation or explanatory scaffolding." for setting_id in DOCUMENT_IDS
    },
    **{
        setting_id: "Menu, section, or subgroup heading without a standalone value."
        for setting_id in GROUP_IDS
    },
    **{
        setting_id: "Parser-created option, wrapped-line, or context fragment."
        for setting_id in FRAGMENT_IDS
    },
    **{setting_id: "Read-only status or information field." for setting_id in READ_ONLY_IDS},
    **{
        setting_id: "Duplicate occurrence without additional setting semantics."
        for setting_id in DUPLICATE_IDS
    },
}

NAME_EDITS = {
    679: "System Time",
    695: "Performance Core ALL-Core Ratio Limit",
    704: "Efficient Core ALL-Core Ratio Limit",
    700: "Performance Core Specific Voltage Offset Mode Sign",
    709: "Efficient Core Group Specific Voltage Offset Mode Sign",
    843: "Memory Training Fast Boot",
    913: "P-core 0-7 Temperature A-B",
    914: "P-core 0-7 Negative Ratio Offset A-B",
    916: "E-core Cluster 0 Temperature A-B",
    917: "E-core Cluster 0 Negative Ratio Offset A-B",
    918: (
        "1-Core Active / 2-Core Active / 3-Core Active / 4-Core Active / "
        "5-Core Active / 6-Core Active / 7-Core Active / 8-Core Active"
    ),
    919: "Active P-core Group Temperature A-B",
    920: "Active P-core Group Negative Ratio Offset A-B",
    928: "Performance Core V/F Point Present Mode",
    929: "Performance Core All-Core V/F Point Voltage Control Mode",
    930: "Performance Core All-Core Offset Mode Sign 1~15",
    931: "Performance Core All-Core V/F Point 1~15 Offset",
    933: "Performance Core Per-Core V/F Point Voltage Control Mode",
    934: "Performance Core Per-Core Offset Mode Sign 1~15",
    935: "Performance Core Per-Core V/F Point 1~15 Offset",
    937: "Efficient Core V/F Point Present Mode",
    938: "Efficient Core All-Core V/F Point Voltage Control Mode",
    939: "Efficient Core All-Core Offset Mode Sign 1~15",
    940: "Efficient Core All-Core V/F Point 1~15 Offset",
    942: "Efficient Core Per-Group V/F Point Voltage Control Mode",
    943: "Efficient Core Per-Group Offset Mode Sign 1~15",
    944: "Efficient Core Per-Group V/F Point 1~15 Offset",
    969: "CPU NGU Offset Mode Sign",
    970: "CPU NGU Offset Voltage",
    1005: "CPU Core Voltage Offset Mode Sign",
    1006: "CPU Core Offset Voltage",
    1010: "Performance Core DLVR Additional Turbo Mode Voltage",
    1011: "Performance Core DLVR Offset Mode Sign",
    1012: "Performance Core DLVR Offset Voltage",
    1015: "Efficient Core DLVR Additional Turbo Mode Voltage",
    1016: "Efficient Core DLVR Offset Mode Sign",
    1017: "Efficient Core DLVR Offset Voltage",
    1020: "Ring DLVR Additional Turbo Mode Voltage",
    1021: "Ring DLVR Offset Mode Sign",
    1022: "Ring DLVR Offset Voltage",
    1025: "CPU Graphics Voltage Offset Mode Sign",
    1026: "CPU Graphics Offset Voltage",
    1029: "CPU System Agent Voltage Offset Mode Sign",
    1030: "CPU System Agent Offset Voltage",
    1035: "Advanced Memory Voltages DRAM VDD Voltage",
    1036: "Advanced Memory Voltages DRAM VDDQ Voltage",
    1048: "PMIC DRAM VDD Voltage",
    1049: "PMIC DRAM VDDQ Voltage",
    1068: "PCH PCI Express ASPM",
    1072: "System Agent PCI Express ASPM",
    1143: "ITBT Root Port 0 Extra Bus Reserved",
    1144: "ITBT Root Port 0 Reserved Memory",
    1145: "ITBT Root Port 0 Memory Alignment",
    1146: "ITBT Root Port 0 Reserved PMemory",
    1147: "ITBT Root Port 0 PMemory Alignment",
    1154: "TBT Host Router Extra Bus Reserved",
    1155: "TBT Host Router Reserved Memory",
    1156: "TBT Host Router Reserved PMemory",
    1157: "TBT Host Router Reserved I/O",
    1258: "PK Save To File",
    1260: "KEK Append Key",
    1262: "DB Append Key",
    1264: "DBX Append Key",
    1266: "Boot Fast Boot",
}

for setting_id, name in zip(
    range(737, 744),
    (
        "RTT WR",
        "RTT NOM RD",
        "RTT NOM WR",
        "RTT PARK",
        "RTT PARK DQS",
        "Pull-up Output Driver Impedance",
        "Pull-Down Output Driver Impedance",
    ),
    strict=True,
):
    NAME_EDITS[setting_id] = f"Basic DIMM ODT synchronized {name}"
for setting_id, name in zip(
    range(744, 751),
    (
        "RTT WR",
        "RTT NOM RD",
        "RTT NOM WR",
        "RTT PARK",
        "RTT PARK DQS",
        "Pull-up Output Driver Impedance",
        "Pull-Down Output Driver Impedance",
    ),
    strict=True,
):
    NAME_EDITS[setting_id] = f"Basic DIMM ODT per-controller {name}"
for setting_id, name in zip(
    range(753, 759),
    (
        "GroupA CA ODT",
        "GroupA CS ODT",
        "GroupA CK ODT",
        "GroupB CA ODT",
        "GroupB CS ODT",
        "GroupB CK ODT",
    ),
    strict=True,
):
    NAME_EDITS[setting_id] = f"Advanced DIMM ODT synchronized {name}"
for setting_id, name in zip(
    range(759, 765),
    (
        "GroupA CA ODT",
        "GroupA CS ODT",
        "GroupA CK ODT",
        "GroupB CA ODT",
        "GroupB CS ODT",
        "GroupB CK ODT",
    ),
    strict=True,
):
    NAME_EDITS[setting_id] = f"Advanced DIMM ODT per-controller {name}"


@dataclass(frozen=True)
class MissedSpec:
    page: int
    source_name: str
    name: str | None = None
    occurrence: int = 1
    description: str | None = None


MISSED = [
    MissedSpec(20, "D.O.C.P."),
    MissedSpec(
        21,
        "",
        name="Performance Core 1-Core through 8-Core Ratio Limits",
        description=(
            "Per-active-core ratio limits; each N-core limit must preserve the documented "
            "ordering constraints."
        ),
    ),
    MissedSpec(
        22,
        "",
        name="Efficient Core 1-Core through 8-Core Ratio Limits",
        description=(
            "Ratio limits for Efficient cores according to the number of active Efficient cores."
        ),
    ),
    MissedSpec(24, "AVX2"),
    MissedSpec(24, "Delete the current DIMM Fit Store"),
    *[
        MissedSpec(25, name)
        for name in (
            "DRAM RAS# to CAS# Delay Read",
            "DRAM RAS# to CAS# Delay Write",
            "DRAM RAS# to RAS# Delay L",
            "DRAM RAS# to RAS# Delay S",
            "DRAM READ to PRE Time",
            "DRAM WRITE to READ Delay L",
            "DRAM WRITE to READ Delay S",
            "DRAM Internal Write to Write Command Delay Time",
            "Sync Basic Dimm ODT mode",
        )
    ],
    MissedSpec(27, "Sync Advanced Dimm ODT mode"),
    *[
        MissedSpec(28, name)
        for name in (
            "DqOdtVrefUp",
            "DqOdtVrefDn",
            "CmdSlewRate",
            "CmdScompPC",
            "CtlSlewRate",
            "CtlScompPC",
            "DisableCompRotate",
            "RcompVrefVdd2",
            "CmdDrvVrefDnVdd2",
            "CmdDrvVrefUpVdd2",
            "CmdDrvVrefUp",
            "CtlDrvVrefUp",
            "ClkDrvVrefUp",
            "DqDrvVrefUp",
            "CmdDrvVrefDn",
            "CtlDrvVrefDn",
            "ClkDrvVrefDn",
        )
    ],
    MissedSpec(29, "DqDrvVrefDn"),
    MissedSpec(29, "RcompTarget"),
    *[
        MissedSpec(32, name)
        for name in (
            "Functional Duty Cycle Correction for DDR5 DQS",
            "Functional Duty Cycle Correction for DDR5 CLK",
            "Functional Duty Cycle Correction for LP5 WCK",
            "Functional Duty Cycle Correction for Data DQ",
            "Duty Cycle Correction for LP5 DCA",
            "tRDRD_sg",
            "tRDRD_dg",
            "tWRWR_sg",
        )
    ],
    *[
        MissedSpec(33, name)
        for name in (
            "tWRWR_dg",
            "tRDWR_sg",
            "tRDWR_dg",
            "tWRRD_sg",
            "tWRRD_dg",
            "tRDRD_dr",
            "tRDRD_dd",
            "tWRWR_dr",
            "tWRWR_dd",
            "tRDWR_dr",
            "tRDWR_dd",
            "tWRRD_dr",
            "tWRRD_dd",
            "tRPRE",
            "tWPRE",
            "tWPOST",
            "tCCD_L_tDLLK",
        )
    ],
    MissedSpec(35, "SAGV"),
    MissedSpec(35, "Synch ACDC Loadline with VRM Loadline"),
    MissedSpec(38, "CPU Core/Cache Current Limit Max."),
    MissedSpec(40, "Max Boost Target in MHz"),
    *[
        MissedSpec(42, name)
        for name in (
            "P-Core Max Voltage limits",
            "E-Core Max Voltage limits",
            "GT Max Voltage limits",
            "Ring Max Voltage limits",
            "SA Max Voltage limits",
            "NGU Max Voltage limits",
        )
    ],
    *[
        MissedSpec(45, name)
        for name in (
            "PVD Ratio Threshold for SOC",
            "PVD Mode Select for SOC",
            "PVD Ratio Threshold for CPU",
            "PVD Mode Select for CPU",
            "FLL OC mode",
        )
    ],
    MissedSpec(48, "Regulate Frequency by above Threshold"),
    MissedSpec(58, "Boot performance mode"),
    MissedSpec(60, "Enable VMD controller"),
    MissedSpec(60, "Map PCIE Storage under VMD"),
    MissedSpec(60, "Map SATA Controller under VMD"),
    MissedSpec(65, "Connect Topology Timeout value for ITBT"),
    MissedSpec(65, "Force Poweron Timeout value for ITBT"),
    MissedSpec(66, "GPIO filter"),
    MissedSpec(67, "Memory alignment", name="TBT Host Router Memory Alignment"),
    MissedSpec(67, "PMemory alignment", name="TBT Host Router PMemory Alignment"),
    MissedSpec(68, "When system is in sleep, hibernate or soft off states"),
    MissedSpec(81, "Install Default Secure Boot keys"),
    MissedSpec(81, "Clear Secure Boot keys"),
    MissedSpec(81, "Save all Secure Boot variables"),
    MissedSpec(81, "Set New key", name="PK Set New Key", occurrence=1),
    MissedSpec(81, "Delete key", name="PK Delete Key", occurrence=1),
    MissedSpec(81, "Save to file", name="KEK Save To File", occurrence=1),
    MissedSpec(81, "Set New key", name="KEK Set New Key", occurrence=2),
    MissedSpec(81, "Delete key", name="KEK Delete Key", occurrence=2),
    MissedSpec(82, "Save to file", name="DB Save To File", occurrence=1),
    MissedSpec(82, "Set New key", name="DB Set New Key", occurrence=1),
    MissedSpec(82, "Delete key", name="DB Delete Key", occurrence=1),
    MissedSpec(82, "Save to file", name="DBX Save To File", occurrence=2),
    MissedSpec(82, "Set New key", name="DBX Set New Key", occurrence=2),
    MissedSpec(82, "Delete key", name="DBX Delete Key", occurrence=2),
    MissedSpec(83, "Next Boot after AC Power Loss"),
    MissedSpec(83, "Boot up NumLock State"),
]


def _clean_description(value: str, page_number: int) -> str:
    lines = [line.strip() for line in value.splitlines()]
    while lines and (not lines[-1] or lines[-1] == str(page_number)):
        lines.pop()
    return "\n".join(lines).strip()


def _candidate_lookup(candidates: list[SettingCandidate]) -> dict[tuple[int, str], list[str]]:
    lookup: dict[tuple[int, str], list[str]] = defaultdict(list)
    for candidate in candidates:
        lookup[(candidate.page_number, candidate.extracted_name)].append(
            _clean_description(candidate.extracted_description, candidate.page_number)
        )
    return lookup


def _description_for(spec: MissedSpec, lookup: dict[tuple[int, str], list[str]]) -> str:
    if spec.description is not None:
        return spec.description
    values = lookup.get((spec.page, spec.source_name), [])
    if len(values) < spec.occurrence:
        raise RuntimeError(
            f"Parser {PARSER_VERSION} did not produce missed-setting evidence for "
            f"page {spec.page}: {spec.source_name!r} occurrence {spec.occurrence}."
        )
    return values[spec.occurrence - 1]


def _load_pinned(session):
    manual = session.get(Manual, MANUAL_ID)
    if manual is None or manual.sha256 != EXPECTED_SHA256:
        raise RuntimeError("Manual 3 is not the pinned ASUS Intel 800 guide.")
    run = session.scalar(select(ExtractionRun).where(ExtractionRun.manual_id == manual.id))
    if run is None or run.parser_version != EXPECTED_RUN_PARSER or run.status != "succeeded":
        raise RuntimeError("The pinned ASUS extraction run is missing or has changed.")
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
            f"Expected {EXPECTED_DRAFTS} untouched ASUS drafts; found "
            f"{len(settings)} settings and {len(drafts)} drafts."
        )
    if not set(REJECTION_REASON_BY_ID).issubset({setting.id for setting in settings}):
        raise RuntimeError("The ASUS rejection manifest references unknown setting IDs.")
    return manual, settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Commit the pinned manifest")
    args = parser.parse_args()

    with SessionLocal() as session:
        manual, settings = _load_pinned(session)
        pdf_path = files.manual_file_path(manual.stored_filename)
        parsed = parse_settings(read_pdf_text(Path(pdf_path)))
        lookup = _candidate_lookup(parsed)
        for spec in MISSED:
            _description_for(spec, lookup)
        approved = [setting for setting in settings if setting.id not in REJECTION_REASON_BY_ID]
        rejected = [setting for setting in settings if setting.id in REJECTION_REASON_BY_ID]
        corrected_descriptions = sum(
            bool(lookup[(setting.page_number, setting.extracted_name)])
            and lookup[(setting.page_number, setting.extracted_name)][0]
            != _clean_description(setting.extracted_description, setting.page_number)
            for setting in approved
        )
        print(
            f"ASUS acceptance manifest: {len(approved)} approve, {len(rejected)} reject, "
            f"{len(NAME_EDITS)} name edits, {corrected_descriptions} description boundary edits, "
            f"{len(MISSED)} missed settings."
        )
        if not args.apply:
            print("Dry run only; pass --apply after verifying the database backup.")
            return

        occurrence_seen: dict[tuple[int, str], int] = defaultdict(int)
        for setting in settings:
            if setting.id in REJECTION_REASON_BY_ID:
                review.reject_setting(session, setting, REJECTION_REASON_BY_ID[setting.id])
                continue
            key = (setting.page_number, setting.extracted_name)
            occurrence_seen[key] += 1
            candidates = lookup.get(key, [])
            improved_description = (
                candidates[occurrence_seen[key] - 1]
                if len(candidates) >= occurrence_seen[key]
                else _clean_description(setting.extracted_description, setting.page_number)
            )
            name = NAME_EDITS.get(setting.id, setting.extracted_name)
            current_description = _clean_description(
                setting.extracted_description, setting.page_number
            )
            if name != setting.extracted_name or improved_description != current_description:
                review.edit_draft_setting(
                    session, setting, name=name, description=improved_description
                )
            review.approve_setting(
                session,
                setting,
                note="Configurable or executable ASUS BIOS item verified against the source page.",
            )
            acceptance.review_page_reference(
                session, manual, setting.id, page_number=setting.page_number
            )

        for spec in MISSED:
            acceptance.add_missed_setting(
                session,
                manual,
                name=spec.name or spec.source_name,
                page_number=spec.page,
                description=_description_for(spec, lookup),
                note=(
                    "Confirmed omission caused by parser 1.1 not recognizing the ASUS "
                    "TTNormsPro-Medium setting label or its wrapped form."
                ),
            )

        acceptance.certify_ground_truth(
            session,
            manual,
            notes=(
                "Full parser-1.1 candidate review and ASUS medium-weight heading audit. "
                "Parser 1.2 was used only to locate description boundaries and omissions; "
                "the immutable extraction run remains parser 1.1."
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
