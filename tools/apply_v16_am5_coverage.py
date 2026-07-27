# ruff: noqa: E501
"""Apply TuneForge's pinned v1.6 AM5 cross-vendor coverage batch.

Dry-run is the default. The batch is pinned to the verified pre-v1.6 backup,
revision-0019 database counts, four official manuals, and 43 visually reviewed
source records. It creates 12 reviewer-approved identities, adds 42 mappings,
records 43 cited menu paths, and intentionally leaves the conflicting ASUS PSS
Support record unmapped.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from sqlalchemy import func, select, text

from app import config
from app.db.models import (
    CanonicalClassificationEvent,
    CanonicalIdentityEvent,
    CanonicalMappingEvent,
    CanonicalSetting,
    CanonicalSettingAlias,
    CanonicalSettingClassification,
    Manual,
    Setting,
    SettingCanonicalMapping,
    SettingMenuContext,
    SettingMenuContextCitation,
    SettingMenuContextEvent,
)
from app.db.session import SessionLocal
from app.domain import canonical_identity_review, canonical_settings, canonicalization, library
from app.domain.extraction_runs import utc_now_iso
from app.domain.review import effective_description, effective_name
from app.normalization.canonical_settings import normalize_alias
from app.operations.backup import inspect_backup
from tools.audit_public_bios_knowledge import build_report as build_public_audit

ROOT = Path(__file__).resolve().parents[1]
BACKUP_PATH = ROOT / "data" / "backups" / "tuneforge-v1.6-pre-am5-coverage.zip"
BACKUP_SHA256 = "ef1fae5b7105f30bf90c78c2ebc4531a38ee5c1bce7c12a8524df52a550d161b"
REPORT_JSON = ROOT / "data" / "reports" / "v1.6-am5-coverage.json"
REPORT_MARKDOWN = ROOT / "docs" / "v1.6-am5-coverage.md"
ACTOR = "v1.6-am5-official-manual-reviewer"


@dataclass(frozen=True)
class ManualPin:
    manual_id: int
    vendor_id: str
    sha256: str
    source_url: str
    page_count: int


@dataclass(frozen=True)
class ContextSeed:
    setting_id: int
    manual_id: int
    vendor_id: str
    page_number: int
    setting_name: str
    menu_path: str


@dataclass(frozen=True)
class IdentitySeed:
    canonical_id: str
    canonical_name: str
    description: str
    category_id: str
    risk_level: str
    risk_rationale: str
    setting_ids: tuple[int, ...]
    rationale: str
    uncertainty: str


MANUALS = (
    ManualPin(
        1,
        "gigabyte",
        "389d5b7ab9330ef95523bccad85afae578fbf91bd97572eff9b438511d14e154",
        "https://www.gigabyte.com/Motherboard/B650M-AORUS-ELITE-AX-rev-14/support#support-manual",
        25,
    ),
    ManualPin(
        2,
        "asrock",
        "ec2ca097b878859f5de664449f5ca8d0d51eb41509290588da0fc2dacf711315",
        "https://www.asrock.com/mb/AMD/X870E%20Taichi%20White/index.asp#Manual",
        85,
    ),
    ManualPin(
        6,
        "msi",
        "5ad148e9ec850442778a3cc090df281660712919299a1c4127d012f780ae622c",
        "https://www.msi.com/Motherboard/MPG-X870E-CARBON-WIFI/support",
        49,
    ),
    ManualPin(
        8,
        "asus",
        "2e7a4b2d504e9f9286b584fcfbe9cba85e0ccd4a061ad545025160408b308213",
        "https://www.asus.com/us/supportonly/tuf%20gaming%20x870-plus%20wifi/helpdesk_manual/",
        116,
    ),
)


def _context(
    setting_id: int,
    manual_id: int,
    vendor_id: str,
    page: int,
    name: str,
    path: str,
) -> ContextSeed:
    return ContextSeed(setting_id, manual_id, vendor_id, page, name, path)


CONTEXTS = (
    _context(
        48,
        1,
        "gigabyte",
        10,
        "Power Supply Idle Control",
        "Tweaker > Advanced CPU Settings > Power Supply Idle Control",
    ),
    _context(
        55,
        1,
        "gigabyte",
        11,
        "Infinity Fabric Frequency and Dividers",
        "Tweaker > Infinity Fabric Frequency and Dividers",
    ),
    _context(56, 1, "gigabyte", 11, "UCLK DIV1 MODE", "Tweaker > UCLK DIV1 MODE"),
    _context(
        61,
        1,
        "gigabyte",
        12,
        "Power Down Enable",
        "Tweaker > Advanced Memory Settings > Power Down Enable",
    ),
    _context(
        80, 1, "gigabyte", 15, "HD Audio Controller", "Settings > IO Ports > HD Audio Controller"
    ),
    _context(97, 1, "gigabyte", 16, "NVMe RAID mode", "Settings > IO Ports > NVMe RAID mode"),
    _context(120, 1, "gigabyte", 17, "TSME", "Settings > Miscellaneous > TSME"),
    _context(130, 1, "gigabyte", 21, "System Language", "System Info > System Language"),
    _context(152, 1, "gigabyte", 23, "Administrator Password", "Boot > Administrator Password"),
    _context(153, 1, "gigabyte", 24, "User Password", "Boot > User Password"),
    _context(
        281,
        2,
        "asrock",
        38,
        "UCLK DIV1 MODE",
        "OC Tweaker > DRAM Profile Configuration > UCLK DIV1 MODE",
    ),
    _context(304, 2, "asrock", 43, "PSS Support", "Advanced > CPU Configuration > PSS Support"),
    _context(305, 2, "asrock", 43, "NX Mode", "Advanced > CPU Configuration > NX Mode"),
    _context(
        325,
        2,
        "asrock",
        49,
        "USB Power delivery in Soft Off state (S5)",
        "Advanced > ACPI Configuration > USB Power delivery in Soft Off state (S5)",
    ),
    _context(
        423,
        2,
        "asrock",
        59,
        "TSME",
        "Advanced > AMD CBS > UMC Common Options > DDR Security > TSME",
    ),
    _context(
        496,
        2,
        "asrock",
        67,
        "Infinity Fabric Frequency and Dividers",
        "Advanced > AMD Overclocking > Infinity Fabric Frequency and Dividers",
    ),
    _context(532, 2, "asrock", 70, "NVMe RAID mode", "Advanced > NVMe RAID mode"),
    _context(568, 2, "asrock", 79, "User Password", "Security > User Password"),
    _context(
        5134,
        6,
        "msi",
        13,
        "HD Audio Controller",
        "Settings > Advanced > Integrated Peripherals > HD Audio Controller",
    ),
    _context(
        5178, 6, "msi", 19, "Administrator Password", "Settings > Security > Administrator Password"
    ),
    _context(5179, 6, "msi", 19, "User Password", "Settings > Security > User Password"),
    _context(
        5302,
        6,
        "msi",
        30,
        "Infinity Fabric Frequency and Dividers",
        "OC > Advanced CPU Configuration > Infinity Fabric Frequency and Dividers",
    ),
    _context(5307, 6, "msi", 30, "NX Mode", "OC > Advanced CPU Configuration > NX Mode"),
    _context(5327, 6, "msi", 32, "UCLK DIV1 MODE", "OC > DRAM Setting > UCLK DIV1 MODE"),
    _context(
        5401,
        6,
        "msi",
        37,
        "Power Down Enable",
        "OC > Advanced DRAM Configuration > Power Down Enable",
    ),
    _context(5403, 6, "msi", 37, "TSME", "OC > Advanced DRAM Configuration > TSME"),
    _context(5770, 8, "asus", 15, "System Language", "Main > System Language"),
    _context(5774, 8, "asus", 16, "Administrator Password", "Main > Administrator Password"),
    _context(5775, 8, "asus", 16, "User Password", "Main > User Password"),
    _context(
        5933,
        8,
        "asus",
        28,
        "Power Down Enable",
        "Ai Tweaker > DRAM Timing Control > Power Down Enable",
    ),
    _context(
        5935, 8, "asus", 28, "UCLK DIV1 MODE", "Ai Tweaker > DRAM Timing Control > UCLK DIV1 MODE"
    ),
    _context(6111, 8, "asus", 50, "PSS Support", "Advanced > CPU Configuration > PSS Support"),
    _context(6112, 8, "asus", 50, "NX Mode", "Advanced > CPU Configuration > NX Mode"),
    _context(
        6125, 8, "asus", 53, "NVMe RAID Mode", "Advanced > SATA Configuration > NVMe RAID Mode"
    ),
    _context(
        6139,
        8,
        "asus",
        54,
        "HD Audio Controller",
        "Advanced > Onboard Devices Configuration > HD Audio Controller",
    ),
    _context(
        6148,
        8,
        "asus",
        55,
        "USB power delivery in Soft Off state (S5)",
        "Advanced > Onboard Devices Configuration > USB power delivery in Soft Off state (S5)",
    ),
    _context(
        6182,
        8,
        "asus",
        58,
        "Power Supply Idle Control",
        "Advanced > AMD CBS > CPU Common Options > Power Supply Idle Control",
    ),
    _context(
        6304,
        8,
        "asus",
        69,
        "Power Down Enable",
        "Advanced > AMD CBS > UMC Common Options > DDR Controller Configuration > DDR Power Options > Power Down Enable",
    ),
    _context(
        6309, 8, "asus", 69, "TSME", "Advanced > AMD CBS > UMC Common Options > DDR Security > TSME"
    ),
    _context(
        6515,
        8,
        "asus",
        85,
        "Power Down Enable",
        "Advanced > AMD CBS > UMC Common Options > DDR Controller Configuration > DDR Power Options > Power Down Enable",
    ),
    _context(
        6520,
        8,
        "asus",
        85,
        "RX2D Delay Step Size (2^n)",
        "Advanced > AMD CBS > UMC Common Options > Additional Memory Tweaks > RX2D_TrainOpt > RX2D Delay Step Size (2^n)",
    ),
    _context(
        6524,
        8,
        "asus",
        86,
        "TX2D Delay Step Size (2^n)",
        "Advanced > AMD CBS > UMC Common Options > Additional Memory Tweaks > TX2D_TrainOpt > TX2D Delay Step Size (2^n)",
    ),
    _context(
        6539,
        8,
        "asus",
        87,
        "UCLK DIV1 MODE",
        "Advanced > AMD Overclocking > DDR and Infinity Fabric Frequency/Timings > UCLK DIV1 MODE",
    ),
)


IDENTITIES = (
    IdentitySeed(
        "uclk-div1-mode",
        "UCLK DIV1 MODE",
        "Selects whether the internal memory-controller clock (UCLK) runs equal to the memory clock (MEMCLK) or at half MEMCLK.",
        "memory",
        "high",
        "Changing the UCLK-to-MEMCLK ratio can affect memory performance and stability.",
        (56, 281, 5327, 5935, 6539),
        "Four vendors document the same UCLK divider control and matching clock-ratio choices.",
        "Availability and stable ratios remain dependent on the processor, memory, and firmware.",
    ),
    IdentitySeed(
        "tsme",
        "TSME",
        "Controls AMD Transparent Secure Memory Encryption support.",
        "security",
        "moderate",
        "Changing memory-encryption support can affect security behavior and platform compatibility.",
        (120, 423, 5403, 6309),
        "Four vendors identify the same AMD TSME control with matching enable, disable, or automatic behavior.",
        "The manuals do not establish availability on every AM5 processor or firmware version.",
    ),
    IdentitySeed(
        "power-down-enable",
        "Power Down Enable",
        "Controls DDR memory power-down mode.",
        "memory",
        "moderate",
        "Changing DDR power-down behavior can affect idle power, latency, and memory stability.",
        (61, 5401, 5933, 6304, 6515),
        "GIGABYTE, MSI, and ASUS describe the same DDR power-down function; both ASUS menu instances are retained as distinct source observations.",
        "Exact power-state behavior can vary by memory controller and firmware.",
    ),
    IdentitySeed(
        "user-password",
        "User Password",
        "Configures the firmware user password, which provides more limited BIOS setup privileges than the administrator password where the vendor documents separate roles.",
        "security",
        "moderate",
        "A lost firmware password can restrict setup access and may require a vendor-specific recovery procedure.",
        (153, 568, 5179, 5775),
        "All four vendors document the user-level firmware password and distinguish it from administrator privileges.",
        "Startup prompts, permitted fields, and recovery procedures vary by vendor.",
    ),
    IdentitySeed(
        "administrator-password",
        "Administrator Password",
        "Configures the firmware administrator password used for full BIOS setup privileges.",
        "security",
        "high",
        "A lost administrator password can block firmware configuration and may require a vendor-specific recovery procedure.",
        (152, 5178, 5774),
        "GIGABYTE, MSI, and ASUS document the same administrator-level firmware credential and full setup role.",
        "Startup prompting and recovery procedures vary by vendor.",
    ),
    IdentitySeed(
        "hd-audio-controller",
        "HD Audio Controller",
        "Enables or disables the motherboard's onboard high-definition audio controller.",
        "peripheral-io",
        "low",
        "Disabling the controller removes onboard audio until it is re-enabled.",
        (80, 5134, 6139),
        "GIGABYTE, MSI, and ASUS explicitly describe the same onboard HD Audio/Azalia controller toggle.",
        "Codec implementation and exposed endpoints remain board-specific.",
    ),
    IdentitySeed(
        "infinity-fabric-frequency-and-dividers",
        "Infinity Fabric Frequency and Dividers",
        "Controls the AMD Infinity Fabric clock frequency (FCLK) and its divider selection.",
        "memory",
        "high",
        "Changing fabric frequency can affect memory performance, stability, voltage, power, and temperature.",
        (55, 496, 5302),
        "GIGABYTE, ASRock, and MSI use the exact setting name and document FCLK/fabric frequency control.",
        "Valid frequencies and stable limits are hardware- and firmware-specific.",
    ),
    IdentitySeed(
        "nvme-raid-mode",
        "NVMe RAID Mode",
        "Enables or disables use of supported NVMe storage devices in a RAID configuration.",
        "pcie-resource",
        "high",
        "Changing storage mode can affect device visibility and whether an installed operating system can boot.",
        (97, 532, 6125),
        "GIGABYTE, ASRock, and ASUS document the same NVMe RAID enablement function.",
        "Lane configuration, driver, and boot requirements remain platform-specific.",
    ),
    IdentitySeed(
        "nx-mode",
        "NX Mode",
        "Enables or disables no-execute page protection.",
        "security",
        "high",
        "Disabling no-execute protection weakens a hardware-backed memory-execution security control.",
        (305, 5307, 6112),
        "ASRock, MSI, and ASUS explicitly identify the same AMD NX/no-execute protection control.",
        "Operating-system policy and processor support can affect observed behavior.",
    ),
    IdentitySeed(
        "usb-power-delivery-in-soft-off-state-s5",
        "USB Power Delivery in Soft Off State (S5)",
        "Controls whether supported USB ports receive power while the system is in the S5 soft-off state.",
        "power-management",
        "low",
        "Enabling S5 USB power can increase off-state power use; disabling it can prevent USB charging or wake-related behavior.",
        (325, 6148),
        "ASRock and ASUS document the same USB power control in the S5 soft-off state.",
        "Which ports are powered remains board-specific.",
    ),
    IdentitySeed(
        "power-supply-idle-control",
        "Power Supply Idle Control",
        "Selects the processor package's low-current or typical-current idle behavior.",
        "power-management",
        "moderate",
        "Changing package idle behavior can alter idle power and compatibility with some power supplies or workloads.",
        (48, 6182),
        "GIGABYTE and ASUS expose the same named AMD CPU idle policy with matching low-current, typical-current, and automatic choices.",
        "The ASUS source lists choices without a behavioral description; equivalence is supported by the exact AMD CBS setting and matching choices.",
    ),
    IdentitySeed(
        "system-language",
        "System Language",
        "Selects the default language displayed by the BIOS setup interface.",
        "uncategorized",
        "low",
        "This changes interface language and does not itself change hardware tuning behavior.",
        (130, 5770),
        "GIGABYTE and ASUS document the same firmware interface-language selector.",
        "Available languages vary by firmware.",
    ),
)

EXISTING_MAPPINGS = {
    304: "pss-support",
    6520: "rx2d-delay-step-size-2-n",
    6524: "tx2d-delay-step-size-2-n",
}
AMBIGUOUS_SETTING_ID = 6111
AMBIGUITY_REASON = (
    "ASUS record 6111 uses the exact name PSS Support, but its official description lists "
    "ACPI _PPC and _PCT generation and omits _PSS. ASRock and the existing GIGABYTE/MSI "
    "records explicitly include _PSS. Exact naming alone is insufficient to publish the mapping."
)

BASELINE_COUNTS = {
    CanonicalSetting: 56,
    CanonicalSettingAlias: 92,
    SettingCanonicalMapping: 243,
    CanonicalMappingEvent: 243,
    CanonicalIdentityEvent: 71,
    CanonicalSettingClassification: 56,
    CanonicalClassificationEvent: 73,
    SettingMenuContext: 39,
    SettingMenuContextEvent: 39,
    SettingMenuContextCitation: 113,
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _count(session, model) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


def _unmapped_count(session) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(Setting)
            .outerjoin(SettingCanonicalMapping, SettingCanonicalMapping.setting_id == Setting.id)
            .where(Setting.status == "approved", SettingCanonicalMapping.setting_id.is_(None))
        )
        or 0
    )


def _verify_backup() -> None:
    if not BACKUP_PATH.is_file() or _sha256(BACKUP_PATH) != BACKUP_SHA256:
        raise RuntimeError("The verified pre-v1.6 backup is missing or its checksum changed.")
    inspection = inspect_backup(BACKUP_PATH)
    if inspection.database_revision != config.EXPECTED_DATABASE_REVISION:
        raise RuntimeError("The pre-v1.6 backup has an unexpected database revision.")


def _verify_manifest() -> None:
    context_ids = tuple(seed.setting_id for seed in CONTEXTS)
    mapping_ids = tuple(
        setting_id for seed in IDENTITIES for setting_id in seed.setting_ids
    ) + tuple(EXISTING_MAPPINGS)
    if len(context_ids) != 43 or len(set(context_ids)) != 43:
        raise RuntimeError("The v1.6 context manifest must contain 43 unique settings.")
    if len(mapping_ids) != 42 or len(set(mapping_ids)) != 42:
        raise RuntimeError("The v1.6 mapping manifest must contain 42 unique settings.")
    if set(context_ids) != set(mapping_ids) | {AMBIGUOUS_SETTING_ID}:
        raise RuntimeError("The context, mapping, and ambiguity manifests disagree.")
    if len({seed.canonical_id for seed in IDENTITIES}) != 12:
        raise RuntimeError("The v1.6 identity manifest must contain 12 unique identities.")


def _verify_baseline(session) -> None:
    revision = session.scalar(text("SELECT version_num FROM alembic_version"))
    if revision != config.EXPECTED_DATABASE_REVISION:
        raise RuntimeError(
            f"Expected revision {config.EXPECTED_DATABASE_REVISION}, found {revision}."
        )
    if session.scalar(text("PRAGMA integrity_check")) != "ok":
        raise RuntimeError("SQLite integrity check failed before v1.6.")
    if _unmapped_count(session) != 3775:
        raise RuntimeError(
            f"Expected 3,775 approved unmapped settings, found {_unmapped_count(session)}."
        )
    for model, expected in BASELINE_COUNTS.items():
        found = _count(session, model)
        if found != expected:
            raise RuntimeError(f"{model.__tablename__}: expected {expected}, found {found}.")
    for pin in MANUALS:
        manual = session.get(Manual, pin.manual_id)
        actual = (
            manual.vendor_id if manual else None,
            manual.sha256 if manual else None,
            manual.source_url if manual else None,
            manual.page_count if manual else None,
        )
        expected = (pin.vendor_id, pin.sha256, pin.source_url, pin.page_count)
        if actual != expected:
            raise RuntimeError(f"Pinned manual {pin.manual_id} changed: {actual!r}.")
    contexts = {seed.setting_id: seed for seed in CONTEXTS}
    rows = list(
        session.execute(
            select(Setting, Manual)
            .join(Manual, Manual.id == Setting.manual_id)
            .where(Setting.id.in_(contexts))
        )
    )
    if len(rows) != len(contexts):
        raise RuntimeError("One or more pinned source settings are missing.")
    for setting, manual in rows:
        seed = contexts[setting.id]
        actual = (
            setting.manual_id,
            manual.vendor_id,
            setting.edited_page_number or setting.page_number,
            normalize_alias(effective_name(setting)),
        )
        expected = (
            seed.manual_id,
            seed.vendor_id,
            seed.page_number,
            normalize_alias(seed.setting_name),
        )
        if actual != expected:
            raise RuntimeError(
                f"Pinned source {setting.id} changed: expected {expected}, found {actual}."
            )
        reasons = canonicalization.source_trust_reasons(setting, manual)
        if reasons:
            raise RuntimeError(
                f"Pinned source {setting.id} failed trust gates: {' '.join(reasons)}"
            )
        if session.get(SettingCanonicalMapping, setting.id) is not None:
            raise RuntimeError(f"Pinned source {setting.id} is already mapped.")
        if session.get(SettingMenuContext, setting.id) is not None:
            raise RuntimeError(f"Pinned source {setting.id} already has a menu context.")
    for seed in IDENTITIES:
        if session.get(CanonicalSetting, seed.canonical_id) is not None:
            raise RuntimeError(f"Pinned identity already exists: {seed.canonical_id}.")
    for canonical_id in EXISTING_MAPPINGS.values():
        identity = session.get(CanonicalSetting, canonical_id)
        if identity is None or identity.status != canonical_settings.IDENTITY_ACTIVE:
            raise RuntimeError(f"Existing target identity is unavailable: {canonical_id}.")
    evaluation = canonicalization.evaluate_backlog(session)
    if any(entry.automatic_eligible for entry in evaluation.entries):
        raise RuntimeError("An unexpected automatic mapping is eligible before the batch.")


def _record_contexts(session) -> None:
    for seed in CONTEXTS:
        canonical_identity_review.review_menu_context(
            session,
            seed.setting_id,
            menu_path=seed.menu_path,
            source_page_number=seed.page_number,
            evidence_excerpt=(
                f"Official {seed.vendor_id.upper()} BIOS hierarchy lists "
                f"{seed.setting_name} at this reviewed path."
            ),
            note=(
                "v1.6 AM5 official-manual hierarchy review; page and ordered menu path "
                "were visually verified against the stored PDF."
            ),
            actor=ACTOR,
            commit=False,
        )


def _mapping_note(
    session, setting_id: int, canonical_id: str, rationale: str, uncertainty: str
) -> str:
    setting = session.get(Setting, setting_id)
    manual = session.get(Manual, setting.manual_id)
    context = session.get(SettingMenuContext, setting_id)
    return (
        "v1.6 AM5 official-manual reviewer decision; confidence=100.00%. "
        f"{rationale} Source: {manual.vendor} manual {manual.id}, p."
        f"{setting.edited_page_number or setting.page_number}, {manual.source_url}; "
        f"reviewed menu path: {context.menu_path}. Target: {canonical_id}. "
        f"Remaining uncertainty: {uncertainty}"
    )


def _apply_decisions(session) -> None:
    for seed in IDENTITIES:
        identity = canonical_settings.create_identity(
            session,
            name=seed.canonical_name,
            canonical_id=seed.canonical_id,
            description=seed.description,
            actor=ACTOR,
            commit=False,
        )
        canonical_settings.classify_identity(
            session,
            identity,
            category_id=seed.category_id,
            risk_level=seed.risk_level,
            risk_rationale=seed.risk_rationale,
            actor=ACTOR,
            commit=False,
        )
        for setting_id in seed.setting_ids:
            setting = session.get(Setting, setting_id)
            canonical_settings.map_setting(
                session,
                setting,
                canonical_id=seed.canonical_id,
                note=_mapping_note(
                    session,
                    setting_id,
                    seed.canonical_id,
                    seed.rationale,
                    seed.uncertainty,
                ),
                actor=ACTOR,
                mapping_method=canonical_settings.MAPPING_REVIEWER,
                commit=False,
            )
    existing_rationales = {
        304: (
            "The exact normalized name, ACPI _PPC/_PSS/_PCT behavior, and CPU Configuration context agree with the existing cited GIGABYTE and MSI identity.",
            "Firmware may expose this control only on supported processors.",
        ),
        6520: (
            "The exact normalized name, LCDL delay-step choices, DDR training function, and RX2D_TrainOpt hierarchy agree with the existing ASRock and ASUS identity.",
            "Training implementation remains firmware- and memory-controller-specific.",
        ),
        6524: (
            "The exact normalized name, LCDL delay-step choices, DDR training function, and TX2D_TrainOpt hierarchy agree with the existing ASRock and ASUS identity.",
            "Training implementation remains firmware- and memory-controller-specific.",
        ),
    }
    for setting_id, canonical_id in EXISTING_MAPPINGS.items():
        rationale, uncertainty = existing_rationales[setting_id]
        canonical_settings.map_setting(
            session,
            session.get(Setting, setting_id),
            canonical_id=canonical_id,
            note=_mapping_note(session, setting_id, canonical_id, rationale, uncertainty),
            actor=ACTOR,
            mapping_method=canonical_settings.MAPPING_REVIEWER,
            commit=False,
        )


def _verify_final(session, baseline: dict[type, int]) -> None:
    deltas = {
        CanonicalSetting: 12,
        CanonicalSettingAlias: 12,
        SettingCanonicalMapping: 42,
        CanonicalMappingEvent: 42,
        CanonicalIdentityEvent: 12,
        CanonicalSettingClassification: 12,
        CanonicalClassificationEvent: 24,
        SettingMenuContext: 43,
        SettingMenuContextEvent: 43,
        SettingMenuContextCitation: 43,
    }
    failures: list[str] = []
    for model, delta in deltas.items():
        found = _count(session, model)
        expected = baseline[model] + delta
        if found != expected:
            failures.append(f"{model.__tablename__}: expected {expected}, found {found}")
    if _unmapped_count(session) != 3733:
        failures.append(f"approved unmapped: expected 3733, found {_unmapped_count(session)}")
    for seed in IDENTITIES:
        mapped = tuple(
            session.scalars(
                select(SettingCanonicalMapping.setting_id)
                .where(SettingCanonicalMapping.canonical_id == seed.canonical_id)
                .order_by(SettingCanonicalMapping.setting_id)
            )
        )
        if mapped != tuple(sorted(seed.setting_ids)):
            failures.append(f"{seed.canonical_id}: expected {seed.setting_ids}, found {mapped}")
        detail = library.canonical_detail(session, seed.canonical_id)
        if detail is None or {item.setting_id for item in detail.source_settings} != set(
            seed.setting_ids
        ):
            failures.append(f"{seed.canonical_id}: public source projection mismatch")
        elif any(not item.menu_path for item in detail.source_settings):
            failures.append(f"{seed.canonical_id}: public reviewed menu path missing")
    for setting_id, canonical_id in EXISTING_MAPPINGS.items():
        mapping = session.get(SettingCanonicalMapping, setting_id)
        if mapping is None or mapping.canonical_id != canonical_id:
            failures.append(f"setting {setting_id}: existing-identity mapping missing")
    if session.get(SettingCanonicalMapping, AMBIGUOUS_SETTING_ID) is not None:
        failures.append("ambiguous ASUS PSS Support record was mapped")
    if session.get(SettingMenuContext, AMBIGUOUS_SETTING_ID) is None:
        failures.append("ambiguous ASUS PSS Support menu context was not preserved")
    if session.scalar(text("PRAGMA integrity_check")) != "ok":
        failures.append("SQLite integrity_check failed")
    foreign_keys = list(session.execute(text("PRAGMA foreign_key_check")))
    if foreign_keys:
        failures.append(f"foreign-key violations: {foreign_keys!r}")
    if failures:
        raise RuntimeError("v1.6 verification failed:\n- " + "\n- ".join(failures))


def _source_payload(session, setting_id: int) -> dict[str, object]:
    setting = session.get(Setting, setting_id)
    manual = session.get(Manual, setting.manual_id)
    context = session.get(SettingMenuContext, setting_id)
    mapping = session.get(SettingCanonicalMapping, setting_id)
    return {
        "setting_id": setting.id,
        "canonical_id": mapping.canonical_id if mapping else None,
        "source_name": effective_name(setting),
        "source_description": effective_description(setting),
        "vendor_id": manual.vendor_id,
        "manual_id": manual.id,
        "manual_title": manual.document_title,
        "official_url": manual.source_url,
        "page_number": setting.edited_page_number or setting.page_number,
        "menu_path": context.menu_path if context else None,
        "confidence_percent": 100.0 if mapping else None,
    }


def _remaining_payload(session) -> list[dict[str, object]]:
    rows = session.execute(
        select(Setting, Manual)
        .join(Manual, Manual.id == Setting.manual_id)
        .outerjoin(SettingCanonicalMapping, SettingCanonicalMapping.setting_id == Setting.id)
        .where(Setting.status == "approved", SettingCanonicalMapping.setting_id.is_(None))
        .order_by(Setting.id)
    )
    return [
        {
            "setting_id": setting.id,
            "vendor_id": manual.vendor_id,
            "manual_id": manual.id,
            "source_name": effective_name(setting),
            "page_number": setting.edited_page_number or setting.page_number,
            "reason": (
                AMBIGUITY_REASON
                if setting.id == AMBIGUOUS_SETTING_ID
                else "No v1.6 multi-manual official-evidence decision was approved; human review remains required."
            ),
        }
        for setting, manual in rows
    ]


def _build_payload(session) -> dict[str, object]:
    new_decisions = []
    for seed in IDENTITIES:
        new_decisions.append(
            {
                **asdict(seed),
                "confidence_percent": 100.0,
                "sources": [
                    _source_payload(session, setting_id) for setting_id in seed.setting_ids
                ],
            }
        )
    existing = [_source_payload(session, setting_id) for setting_id in sorted(EXISTING_MAPPINGS)]
    remaining = _remaining_payload(session)
    audit = build_public_audit(session)
    if audit["status"] != "pass":
        raise RuntimeError("The public BIOS knowledge audit failed after the v1.6 batch.")
    return {
        "milestone": "v1.6 AM5 cross-vendor coverage expansion",
        "generated_at": utc_now_iso(),
        "actor": ACTOR,
        "backup": {"path": str(BACKUP_PATH), "sha256": BACKUP_SHA256},
        "database_revision": config.EXPECTED_DATABASE_REVISION,
        "summary": {
            "new_canonical_settings": len(IDENTITIES),
            "new_mappings": sum(len(seed.setting_ids) for seed in IDENTITIES),
            "existing_identity_mappings": len(EXISTING_MAPPINGS),
            "total_mappings_created": 42,
            "reviewed_menu_contexts": len(CONTEXTS),
            "remaining_manual_review": len(remaining),
            "intentionally_unmapped": 1,
        },
        "official_sources": [asdict(pin) for pin in MANUALS],
        "new_identity_decisions": new_decisions,
        "existing_identity_decisions": existing,
        "ambiguous_or_conflicting": [
            {**_source_payload(session, AMBIGUOUS_SETTING_ID), "reason": AMBIGUITY_REASON}
        ],
        "remaining_manual_review": remaining,
        "public_knowledge_audit": audit,
    }


def _markdown(payload: dict[str, object]) -> str:
    summary = payload["summary"]
    lines = [
        "# TuneForge v1.6 AM5 Coverage Report",
        "",
        "## Outcome",
        "",
        f"- New canonical settings: {summary['new_canonical_settings']}",
        f"- New mappings to new identities: {summary['new_mappings']}",
        f"- New mappings to existing identities: {summary['existing_identity_mappings']}",
        f"- Total mappings created: {summary['total_mappings_created']}",
        f"- Reviewed menu contexts: {summary['reviewed_menu_contexts']}",
        f"- Remaining approved unmapped settings: {summary['remaining_manual_review']}",
        "",
        "All approvals are reviewer decisions backed by the four pinned official manuals. "
        "No model-generated or community evidence was used.",
        "",
        "## New cross-vendor identities",
        "",
    ]
    for decision in payload["new_identity_decisions"]:
        vendors = sorted({source["vendor_id"] for source in decision["sources"]})
        lines.append(
            f"- **{decision['canonical_name']}** — {len(decision['sources'])} source records, "
            f"{len(vendors)} vendors ({', '.join(vendors)}). {decision['rationale']}"
        )
    lines.extend(
        [
            "",
            "## Existing identities expanded",
            "",
            "- PSS Support: ASRock record 304.",
            "- RX2D Delay Step Size (2^n): ASUS record 6520.",
            "- TX2D Delay Step Size (2^n): ASUS record 6524.",
            "",
            "## Intentionally unmapped",
            "",
            f"- ASUS record 6111, PSS Support: {AMBIGUITY_REASON}",
            "",
            "The complete source-by-source decisions and the full remaining backlog are in "
            "`data/reports/v1.6-am5-coverage.json`.",
            "",
        ]
    )
    return "\n".join(lines)


def _write_temp(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(content, encoding="utf-8")
    return temporary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Commit the pinned v1.6 batch")
    args = parser.parse_args()

    _verify_manifest()
    _verify_backup()
    with SessionLocal() as session:
        _verify_baseline(session)
        print(
            "Preflight passed: backup, revision, counts, manuals, records, and trust gates match."
        )
        print(
            "Planned outcome: 12 new identities, 42 mappings, 43 menu contexts, 1 conflict left unmapped."
        )
        if not args.apply:
            print("Dry run only; pass --apply to commit the pinned reviewed batch.")
            return 0

        baseline = {model: _count(session, model) for model in BASELINE_COUNTS}
        json_temp: Path | None = None
        markdown_temp: Path | None = None
        try:
            _record_contexts(session)
            _apply_decisions(session)
            # SessionLocal deliberately disables autoflush. Persist every
            # pending row inside the still-uncommitted transaction so the
            # verification queries inspect the complete proposed outcome.
            session.flush()
            _verify_final(session, baseline)
            payload = _build_payload(session)
            json_temp = _write_temp(
                REPORT_JSON,
                json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True) + "\n",
            )
            markdown_temp = _write_temp(REPORT_MARKDOWN, _markdown(payload))
            session.commit()
            os.replace(json_temp, REPORT_JSON)
            os.replace(markdown_temp, REPORT_MARKDOWN)
        except Exception:
            session.rollback()
            for temporary in (json_temp, markdown_temp):
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            raise

        print("Applied: identities=12, mappings=42, reviewed_menu_contexts=43.")
        print(f"Remaining approved unmapped: {payload['summary']['remaining_manual_review']}.")
        print(f"JSON report: {REPORT_JSON}")
        print(f"Summary report: {REPORT_MARKDOWN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
