# ruff: noqa: E501
"""Apply TuneForge's pinned v1.8 cross-vendor canonicalization Wave 3.

Dry-run is the default. The batch contains 13 new canonical identities and 51
source mappings visually reviewed in official vendor manuals. It refuses to
run if the verified backup, database baseline, manual identity, source label,
page citation, provenance trust gate, or append-only audit deltas differ.
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
BACKUP_PATH = ROOT / "data" / "backups" / "tuneforge-v1.8-pre-wave3.zip"
BACKUP_SHA256 = "37959cfebe271908138adb142dbd251e8733fbafdd88d4c2424de410f8dab737"
REPORT_JSON = ROOT / "data" / "reports" / "v1.8-cross-vendor-wave3.json"
REPORT_MARKDOWN = ROOT / "docs" / "v1.8-cross-vendor-wave3.md"
ACTOR = "v1.8-cross-vendor-official-evidence-reviewer"
CONFIDENCE_BASIS_POINTS = 9990


@dataclass(frozen=True)
class ManualPin:
    manual_id: int
    vendor_id: str
    sha256: str
    source_url: str
    page_count: int


@dataclass(frozen=True)
class SourcePin:
    setting_id: int
    manual_id: int
    page_number: int
    menu_path: str
    expected_name: str | None = None


@dataclass(frozen=True)
class IdentitySeed:
    canonical_id: str
    canonical_name: str
    description: str
    category_id: str
    risk_level: str
    risk_rationale: str
    rationale: str
    uncertainty: str
    sources: tuple[SourcePin, ...]


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
        3,
        "asus",
        "586c9e7fc4a78e22afc5234b3d5f13511b9e923131a76f4af963bd4a50293054",
        "https://www.asus.com/us/supportonly/prime%20b860m-ks/helpdesk_manual/",
        92,
    ),
    ManualPin(
        5,
        "msi",
        "c59d17516e594f187bcb8d1a60b9e746f0a1849435f76ae93e726040f731af8e",
        "https://www.msi.com/support/download-manual/MPG-Z790-EDGE-TI-MAX-WIFI",
        71,
    ),
    ManualPin(
        6,
        "msi",
        "5ad148e9ec850442778a3cc090df281660712919299a1c4127d012f780ae622c",
        "https://www.msi.com/Motherboard/MPG-X870E-CARBON-WIFI/support",
        49,
    ),
    ManualPin(
        7,
        "gigabyte",
        "b46c52231334bfe914491cc3d6aa9265631914ba9c1de6bb6aa11841b46a2fe5",
        "https://www.gigabyte.com/Motherboard/Z890-AORUS-ELITE-WIFI7/support#support-manual",
        30,
    ),
    ManualPin(
        8,
        "asus",
        "2e7a4b2d504e9f9286b584fcfbe9cba85e0ccd4a061ad545025160408b308213",
        "https://www.asus.com/us/supportonly/tuf%20gaming%20x870-plus%20wifi/helpdesk_manual/",
        116,
    ),
    ManualPin(
        9,
        "gigabyte",
        "14814b731f11f56522bea71ea506b4182de44e3ad729169b81a075fc84f8bb90",
        "https://www.gigabyte.com/Motherboard/B550-AORUS-ELITE-AX-V2-rev-10/support#support-manual",
        19,
    ),
    ManualPin(
        10,
        "gigabyte",
        "17b5d03f17f9280f024b9ac491c9043b1ed3faa0987ea25a3cbacbf8c631b907",
        "https://www.gigabyte.com/Motherboard/Z690-AORUS-ELITE-AX-rev-14/support#support-manual",
        28,
    ),
    ManualPin(
        11,
        "asus",
        "4e3f39d81ae255a6dc8d48a52a31d0be2e496d457e3c6a17f7c689eb54cb57cc",
        "https://www.asus.com/us/motherboards-components/motherboards/prime/prime-b660-plus-d4/helpdesk_manual?model2Name=PRIME-B660-PLUS-D4",
        88,
    ),
    ManualPin(
        12,
        "asus",
        "49d55cb253e29b523cb69a78934d1df0f8160dd7feb9c68a76ceecb42927da37",
        "https://www.asus.com/motherboards-components/motherboards/tuf-gaming/tuf-gaming-b760m-plus/helpdesk_manual?model2Name=TUF-GAMING-B760M-PLUS",
        94,
    ),
)


def s(
    setting_id: int,
    manual_id: int,
    page: int,
    path: str,
    expected_name: str | None = None,
) -> SourcePin:
    return SourcePin(setting_id, manual_id, page, path, expected_name)


IDENTITIES = (
    IdentitySeed(
        "dram-vpp-voltage",
        "DRAM VPP Voltage",
        "Configures the DRAM VPP activating-power-supply voltage provided at the memory modules.",
        "memory",
        "high",
        "Changing DRAM supply voltage can cause memory instability or hardware stress.",
        "ASRock, ASUS, and MSI use the exact label and identify the same DRAM-side or PMIC VPP supply.",
        "Adjustment ranges and whether control is global, profile-specific, or per-DIMM vary by platform.",
        (
            s(279, 2, 38, "OC Tweaker > DRAM Profile Configuration > DRAM VPP Voltage"),
            s(1050, 3, 54, "Ai Tweaker > DRAM VPP Voltage"),
            s(5023, 5, 60, "OC > DRAM Setting > DRAM VPP Voltage"),
            s(5040, 5, 61, "OC > DRAM Setting > Per-DIMM Voltage > DRAM VPP Voltage"),
            s(5341, 6, 33, "OC > DRAM Setting > A-XMP User Profile > DRAM VPP Voltage"),
            s(5362, 6, 34, "OC > DRAM Setting > EXPO User Profile > DRAM VPP Voltage"),
            s(5452, 6, 41, "OC > Voltage Setting > DRAM VPP Voltage"),
            s(8115, 12, 49, "Ai Tweaker > DRAM VPP Voltage"),
        ),
    ),
    IdentitySeed(
        "native-aspm",
        "Native ASPM",
        "Selects whether PCI Express Active State Power Management is controlled by the operating system or firmware.",
        "power-management",
        "moderate",
        "ASPM policy can affect PCIe power use, latency, and device compatibility.",
        "ASUS, MSI, and GIGABYTE use the exact label and distinguish OS-controlled ASPM from BIOS-controlled ASPM.",
        "Auto behavior and prerequisite controls differ by firmware.",
        (
            s(1065, 3, 56, "Advanced > Platform Misc Configuration > Native ASPM"),
            s(4513, 5, 14, "Settings > Advanced > PCI Subsystem Settings > Native ASPM"),
            s(5610, 7, 19, "Settings > Platform Power > Native ASPM"),
            s(6133, 8, 54, "Advanced > Onboard Devices Configuration > Native ASPM"),
            s(7476, 11, 49, "Advanced > Platform Misc Configuration > Native ASPM"),
            s(8131, 12, 52, "Advanced > Platform Misc Configuration > Native ASPM"),
        ),
    ),
    IdentitySeed(
        "pcie-tunneling-over-usb4",
        "PCIe Tunneling over USB4",
        "Enables or disables tunneling PCI Express traffic over a USB4 or Thunderbolt connection.",
        "pcie-resource",
        "high",
        "Changing PCIe tunneling can affect external PCIe device availability and DMA exposure.",
        "ASUS, MSI, and GIGABYTE use the same label and explicitly describe enabling or disabling PCIe tunneling over USB4.",
        "Thunderbolt naming, DMA-protection prerequisites, and available ports remain platform-specific.",
        (
            s(1133, 3, 65, "Advanced > Thunderbolt(TM) Configuration > PCIE Tunneling over USB4"),
            s(4564, 5, 20, "Settings > Advanced > Intel Thunderbolt > PCIE Tunneling over USB4"),
            s(
                5656,
                7,
                22,
                "Settings > IO Ports > Thunderbolt(TM) Configuration > PCIE Tunneling over USB4",
            ),
            s(7553, 11, 58, "Advanced > Thunderbolt(TM) Configuration > PCIE Tunneling over USB4"),
            s(8204, 12, 62, "Advanced > Thunderbolt(TM) Configuration > PCIE Tunneling over USB4"),
        ),
    ),
    IdentitySeed(
        "sata-mode",
        "SATA Mode",
        "Selects the operating mode of the chipset SATA controller, such as AHCI or RAID where supported.",
        "peripheral-io",
        "high",
        "Changing SATA mode can prevent an installed operating system from booting without matching storage drivers.",
        "GIGABYTE, MSI, and ASUS use the exact label and document the same chipset SATA-controller mode selection.",
        "Available modes and RAID support vary by chipset and firmware.",
        (
            s(96, 1, 16, "Settings > IO Ports > SATA Configuration > SATA Mode"),
            s(5131, 6, 13, "Settings > Advanced > Integrated Peripherals > SATA Mode"),
            s(6124, 8, 53, "Advanced > SATA Configuration > SATA Mode"),
            s(6815, 9, 11, "Settings > IO Ports > SATA Configuration > SATA Mode"),
        ),
    ),
    IdentitySeed(
        "vddp-voltage",
        "VDDP Voltage",
        "Configures the target VDDP voltage used by the AMD memory interface and PHY.",
        "memory",
        "high",
        "Incorrect VDDP voltage can cause memory-training failures, instability, or hardware stress.",
        "ASRock, MSI, and ASUS use the exact label and describe the same AMD VDDP voltage target or control.",
        "Units, ranges, and automatic-control behavior vary by firmware path.",
        (
            s(286, 2, 39, "OC Tweaker > VDDP Voltage"),
            s(5301, 6, 30, "OC > Advanced CPU Configuration > AMD CBS > VDDP Voltage"),
            s(6082, 8, 46, "Ai Tweaker > VDDP Voltage"),
            s(6402, 8, 75, "Advanced > AMD CBS > SMU Common Options > VDDP Voltage"),
        ),
    ),
    IdentitySeed(
        "data-scramble",
        "Data Scramble",
        "Controls DRAM data scrambling in the memory controller.",
        "memory",
        "moderate",
        "Changing memory-controller scrambling can affect memory initialization or platform behavior.",
        "ASRock, MSI, and ASUS use the exact label with matching Auto, Disabled, and Enabled behavior in AMD memory-controller menus.",
        "The manuals do not expose the implementation polynomial or controller-internal details.",
        (
            s(424, 2, 60, "Advanced > AMD CBS > UMC Common Options > DDR Options > Data Scramble"),
            s(
                5404,
                6,
                37,
                "OC > Advanced CPU Configuration > AMD CBS > UMC Common Options > DDR Options > Data Scramble",
            ),
            s(
                6310,
                8,
                69,
                "Advanced > AMD CBS > UMC Common Options > DDR Controller Configuration > Data Scramble",
            ),
        ),
    ),
    IdentitySeed(
        "dram-ecc",
        "DRAM ECC",
        "Enables or disables error-correcting-code operation for supported system memory.",
        "memory",
        "moderate",
        "ECC availability and behavior depend on the CPU, memory modules, motherboard, and firmware.",
        "ASRock, MSI, and ASUS use the label ECC in equivalent AMD memory-controller contexts and describe the same DRAM ECC control.",
        "This identity does not imply that every listed motherboard or installed DIMM supports ECC operation.",
        (
            s(421, 2, 59, "Advanced > AMD CBS > UMC Common Options > ECC", "ECC"),
            s(
                5402,
                6,
                37,
                "OC > Advanced CPU Configuration > AMD CBS > UMC Common Options > ECC",
                "ECC",
            ),
            s(6163, 8, 57, "Advanced > AMD CBS > UMC Common Options > ECC", "ECC"),
        ),
    ),
    IdentitySeed(
        "legacy-game-compatibility-mode",
        "Legacy Game Compatibility Mode",
        "Enables a processor compatibility mode intended to improve operation of some older games.",
        "cpu-performance",
        "moderate",
        "Compatibility mode can alter available processor-core behavior and may reduce performance.",
        "MSI, GIGABYTE, and ASUS use the exact label and identify the same older-game compatibility function.",
        "The manuals do not enumerate affected games, processors, or the exact core-control mechanism.",
        (
            s(4776, 5, 41, "OC > Advanced CPU Configuration > Legacy Game Compatibility Mode"),
            s(5544, 7, 11, "Tweaker > Advanced CPU Settings > Legacy Game Compatibility Mode"),
            s(8156, 12, 55, "Advanced > CPU Configuration > Legacy Game Compatibility Mode"),
        ),
    ),
    IdentitySeed(
        "smt-control",
        "SMT Control",
        "Controls AMD simultaneous multithreading, which exposes multiple logical threads per supported processor core.",
        "cpu-performance",
        "moderate",
        "Disabling SMT reduces logical processor availability and can change workload performance.",
        "ASRock, MSI, and ASUS use the exact label and describe disabling or automatically enabling symmetric multithreading.",
        "Re-enabling behavior can require a full power cycle on some firmware.",
        (
            s(344, 2, 53, "Advanced > AMD CBS > CPU Common Options > SMT Control"),
            s(5278, 6, 28, "OC > Advanced CPU Configuration > SMT Control"),
            s(6164, 8, 57, "Advanced > AMD CBS > CPU Common Options > SMT Control"),
        ),
    ),
    IdentitySeed(
        "tdc-control",
        "TDC Control",
        "Selects automatic or manual control of the sustained thermal design current limit.",
        "cpu-performance",
        "high",
        "Increasing sustained current limits can increase processor power, temperature, and stability risk.",
        "ASRock, MSI, and ASUS use the exact label and consistently describe BIOS/default versus manual TDC-limit control.",
        "Units, allowed limits, and processor enforcement depend on the installed CPU and firmware.",
        (
            s(493, 2, 66, "Advanced > AMD CBS > SMU Common Options > TDC Control"),
            s(
                5296,
                6,
                30,
                "OC > Advanced CPU Configuration > AMD CBS > SMU Common Options > TDC Control",
            ),
            s(6382, 8, 74, "Advanced > AMD CBS > SMU Common Options > TDC Control"),
        ),
    ),
    IdentitySeed(
        "tdp-control",
        "TDP Control",
        "Selects automatic or manual control of the processor thermal design power limit.",
        "cpu-performance",
        "high",
        "Increasing processor power limits can increase temperature, power draw, and stability risk.",
        "ASRock, MSI, and ASUS use the exact label and consistently describe BIOS/default versus manual TDP-limit control.",
        "Available values and whether the limit is directly enforced vary by CPU and firmware.",
        (
            s(489, 2, 66, "Advanced > AMD CBS > SMU Common Options > TDP Control"),
            s(
                5290,
                6,
                29,
                "OC > Advanced CPU Configuration > AMD CBS > SMU Common Options > TDP Control",
            ),
            s(6375, 8, 73, "Advanced > AMD CBS > SMU Common Options > TDP Control"),
        ),
    ),
    IdentitySeed(
        "thermal-control",
        "Thermal Control",
        "Selects automatic or manual control of the processor Tctl maximum temperature limit.",
        "cpu-performance",
        "high",
        "Raising a thermal limit can increase operating temperature and cooling requirements.",
        "ASRock, MSI, and ASUS use the exact label and consistently describe BIOS/default versus manual Tctl maximum control.",
        "Permitted temperatures and CPU enforcement vary by processor and firmware.",
        (
            s(492, 2, 66, "Advanced > AMD CBS > SMU Common Options > Thermal Control"),
            s(
                5294,
                6,
                29,
                "OC > Advanced CPU Configuration > AMD CBS > SMU Common Options > Thermal Control",
            ),
            s(6380, 8, 74, "Advanced > AMD CBS > SMU Common Options > Thermal Control"),
        ),
    ),
    IdentitySeed(
        "uma-frame-buffer-size",
        "UMA Frame Buffer Size",
        "Selects the amount of system memory reserved for use by integrated graphics as a UMA frame buffer.",
        "memory",
        "moderate",
        "A larger reservation reduces memory available to the operating system; availability depends on integrated graphics.",
        "GIGABYTE, MSI, and ASUS use the exact label and describe the same system-memory allocation for integrated graphics.",
        "Available sizes and whether the setting appears depend on the processor and graphics mode.",
        (
            s(79, 1, 15, "Settings > IO Ports > Integrated Graphics > UMA Frame Buffer Size"),
            s(
                5138,
                6,
                14,
                "Settings > Advanced > Integrated Graphics Configuration > UMA Frame Buffer Size",
            ),
            s(6159, 8, 56, "Advanced > NB Configuration > UMA Frame Buffer Size"),
        ),
    ),
)

BASELINE_COUNTS = {
    CanonicalSetting: 91,
    CanonicalSettingAlias: 127,
    SettingCanonicalMapping: 421,
    CanonicalMappingEvent: 421,
    CanonicalIdentityEvent: 106,
    CanonicalSettingClassification: 91,
    CanonicalClassificationEvent: 143,
    SettingMenuContext: 218,
    SettingMenuContextEvent: 218,
    SettingMenuContextCitation: 292,
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _count(session, model: type) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


def _unmapped_count(session) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(Setting)
            .outerjoin(SettingCanonicalMapping)
            .where(Setting.status == "approved", SettingCanonicalMapping.setting_id.is_(None))
        )
        or 0
    )


def _sources() -> tuple[SourcePin, ...]:
    return tuple(source for decision in IDENTITIES for source in decision.sources)


def _verify_preflight(session) -> None:
    if not BACKUP_PATH.is_file() or _sha256(BACKUP_PATH) != BACKUP_SHA256:
        raise RuntimeError("The verified pre-v1.8 backup is missing or changed.")
    if inspect_backup(BACKUP_PATH).database_revision != config.EXPECTED_DATABASE_REVISION:
        raise RuntimeError("The pre-v1.8 backup has an unexpected revision.")
    if (
        session.scalar(text("SELECT version_num FROM alembic_version"))
        != config.EXPECTED_DATABASE_REVISION
    ):
        raise RuntimeError("The live schema revision changed.")
    if session.scalar(text("PRAGMA integrity_check")) != "ok":
        raise RuntimeError("SQLite integrity_check failed before Wave 3.")
    if _unmapped_count(session) != 3597:
        raise RuntimeError(f"Expected 3,597 unmapped settings, found {_unmapped_count(session)}.")
    for model, expected in BASELINE_COUNTS.items():
        if _count(session, model) != expected:
            raise RuntimeError(f"{model.__tablename__} baseline changed.")
    pins = {pin.manual_id: pin for pin in MANUALS}
    for pin in MANUALS:
        manual = session.get(Manual, pin.manual_id)
        actual = (
            (manual.vendor_id, manual.sha256, manual.source_url, manual.page_count)
            if manual
            else None
        )
        if actual != (pin.vendor_id, pin.sha256, pin.source_url, pin.page_count):
            raise RuntimeError(f"Pinned manual {pin.manual_id} changed: {actual!r}.")
    sources = _sources()
    if len(IDENTITIES) != 13 or len(sources) != 51 or len({x.setting_id for x in sources}) != 51:
        raise RuntimeError("Wave 3 manifest must contain 13 identities and 51 unique sources.")
    for decision in IDENTITIES:
        if session.get(CanonicalSetting, decision.canonical_id) is not None:
            raise RuntimeError(f"Canonical identity already exists: {decision.canonical_id}.")
        if len({pins[source.manual_id].vendor_id for source in decision.sources}) < 3:
            raise RuntimeError(f"{decision.canonical_id} lacks three-vendor evidence.")
        for source in decision.sources:
            setting = session.get(Setting, source.setting_id)
            manual = session.get(Manual, source.manual_id)
            actual = (
                (
                    setting.manual_id,
                    setting.edited_page_number or setting.page_number,
                    normalize_alias(effective_name(setting)),
                )
                if setting
                else None
            )
            expected = (
                source.manual_id,
                source.page_number,
                normalize_alias(source.expected_name or decision.canonical_name),
            )
            if actual != expected:
                raise RuntimeError(
                    f"Pinned source {source.setting_id} changed: {actual!r} != {expected!r}."
                )
            reasons = canonicalization.source_trust_reasons(setting, manual)
            if reasons:
                raise RuntimeError(
                    f"Source {source.setting_id} failed trust gates: {' '.join(reasons)}"
                )
            if session.get(SettingCanonicalMapping, source.setting_id) is not None:
                raise RuntimeError(f"Source {source.setting_id} is already mapped.")
            if session.get(SettingMenuContext, source.setting_id) is not None:
                raise RuntimeError(f"Source {source.setting_id} already has reviewed menu context.")


def _note(session, decision: IdentitySeed, source: SourcePin) -> str:
    manual = session.get(Manual, source.manual_id)
    return (
        f"v1.8 Wave 3 reviewer decision; confidence={CONFIDENCE_BASIS_POINTS / 100:.2f}%. "
        f"{decision.rationale} Official source: {manual.vendor} manual {manual.id}, page {source.page_number}, "
        f"{manual.source_url}; reviewed menu path: {source.menu_path}. Remaining uncertainty: {decision.uncertainty}"
    )


def _apply(session) -> None:
    for decision in IDENTITIES:
        identity = canonical_settings.create_identity(
            session,
            name=decision.canonical_name,
            canonical_id=decision.canonical_id,
            description=decision.description,
            actor=ACTOR,
            commit=False,
        )
        canonical_settings.classify_identity(
            session,
            identity,
            category_id=decision.category_id,
            risk_level=decision.risk_level,
            risk_rationale=decision.risk_rationale,
            actor=ACTOR,
            commit=False,
        )
        for source in decision.sources:
            setting = session.get(Setting, source.setting_id)
            canonical_identity_review.review_menu_context(
                session,
                setting.id,
                menu_path=source.menu_path,
                source_page_number=source.page_number,
                evidence_excerpt=f"The official manual page lists {effective_name(setting)} in the visually reviewed hierarchy represented by this path.",
                note="v1.8 Wave 3 official-manual page and ordered menu hierarchy visually verified.",
                actor=ACTOR,
                commit=False,
            )
            canonical_settings.map_setting(
                session,
                setting,
                canonical_id=decision.canonical_id,
                note=_note(session, decision, source),
                actor=ACTOR,
                mapping_method=canonical_settings.MAPPING_REVIEWER,
                commit=False,
            )


def _verify_final(session, baseline: dict[type, int]) -> None:
    deltas = {
        CanonicalSetting: 13,
        CanonicalSettingAlias: 13,
        SettingCanonicalMapping: 51,
        CanonicalMappingEvent: 51,
        CanonicalIdentityEvent: 13,
        CanonicalSettingClassification: 13,
        CanonicalClassificationEvent: 26,
        SettingMenuContext: 51,
        SettingMenuContextEvent: 51,
        SettingMenuContextCitation: 51,
    }
    failures = []
    for model, delta in deltas.items():
        if _count(session, model) != baseline[model] + delta:
            failures.append(f"{model.__tablename__} append-only delta mismatch")
    if _unmapped_count(session) != 3546:
        failures.append(f"expected 3,546 unmapped, found {_unmapped_count(session)}")
    for decision in IDENTITIES:
        expected = {source.setting_id for source in decision.sources}
        detail = library.canonical_detail(session, decision.canonical_id)
        public = {item.setting_id for item in detail.source_settings} if detail else set()
        if not expected.issubset(public):
            failures.append(f"{decision.canonical_id} public projection mismatch")
        elif any(
            not item.menu_path for item in detail.source_settings if item.setting_id in expected
        ):
            failures.append(f"{decision.canonical_id} public menu path missing")
    if session.scalar(text("PRAGMA integrity_check")) != "ok":
        failures.append("SQLite integrity_check failed")
    foreign_keys = list(session.execute(text("PRAGMA foreign_key_check")))
    if foreign_keys:
        failures.append(f"foreign-key violations: {foreign_keys!r}")
    if failures:
        raise RuntimeError("Wave 3 verification failed:\n- " + "\n- ".join(failures))


def _payload(session) -> dict[str, object]:
    decisions = []
    for decision in IDENTITIES:
        rows = []
        for source in decision.sources:
            setting = session.get(Setting, source.setting_id)
            manual = session.get(Manual, source.manual_id)
            rows.append(
                {
                    "setting_id": setting.id,
                    "vendor_id": manual.vendor_id,
                    "manual_id": manual.id,
                    "source_name": effective_name(setting),
                    "source_description": effective_description(setting),
                    "page_number": source.page_number,
                    "menu_path": source.menu_path,
                    "official_url": manual.source_url,
                    "confidence_percent": CONFIDENCE_BASIS_POINTS / 100,
                }
            )
        decisions.append(
            {
                "canonical_id": decision.canonical_id,
                "canonical_name": decision.canonical_name,
                "rationale": decision.rationale,
                "remaining_uncertainty": decision.uncertainty,
                "sources": rows,
            }
        )
    remaining_rows = session.execute(
        select(Setting, Manual)
        .join(Manual)
        .outerjoin(SettingCanonicalMapping)
        .where(Setting.status == "approved", SettingCanonicalMapping.setting_id.is_(None))
        .order_by(Setting.id)
    )
    remaining = [
        {
            "setting_id": setting.id,
            "vendor_id": manual.vendor_id,
            "manual_id": manual.id,
            "source_name": effective_name(setting),
            "page_number": setting.edited_page_number or setting.page_number,
            "review_status": "pending_evidence_review",
            "reason": "Not approved in Wave 3; requires a later one-by-one evidence disposition.",
        }
        for setting, manual in remaining_rows
    ]
    audit = build_public_audit(session)
    if audit["status"] != "pass":
        raise RuntimeError("Public BIOS knowledge audit failed after Wave 3.")
    return {
        "milestone": "v1.8 cross-vendor canonicalization Wave 3",
        "generated_at": utc_now_iso(),
        "actor": ACTOR,
        "backup": {"path": str(BACKUP_PATH), "sha256": BACKUP_SHA256},
        "database_revision": config.EXPECTED_DATABASE_REVISION,
        "summary": {
            "new_canonical_settings": 13,
            "total_mappings_created": 51,
            "reviewed_menu_contexts": 51,
            "remaining_manual_review": len(remaining),
        },
        "official_manuals": [asdict(pin) for pin in MANUALS],
        "approved_decisions": decisions,
        "remaining_manual_review": remaining,
        "public_knowledge_audit": audit,
    }


def _markdown(payload: dict[str, object]) -> str:
    summary = payload["summary"]
    lines = [
        "# TuneForge v1.8 Cross-Vendor Canonicalization Wave 3",
        "",
        "## Outcome",
        "",
        f"- New canonical settings: {summary['new_canonical_settings']}",
        f"- New verified mappings: {summary['total_mappings_created']}",
        f"- New reviewed menu contexts: {summary['reviewed_menu_contexts']}",
        f"- Remaining approved unmapped settings: {summary['remaining_manual_review']}",
        "",
        "Every decision is based on visually reviewed official motherboard manuals from at least three vendors. No community source was required for this batch.",
        "",
        "## Approved identities",
        "",
    ]
    for decision in payload["approved_decisions"]:
        vendors = sorted({source["vendor_id"] for source in decision["sources"]})
        lines.append(
            f"- **{decision['canonical_name']}** - {len(decision['sources'])} mappings across {len(vendors)} vendors: {', '.join(vendors)}. {decision['rationale']}"
        )
    lines.extend(
        [
            "",
            "The machine-readable report contains every approval and the complete pending backlog: `data/reports/v1.8-cross-vendor-wave3.json`.",
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
    parser.add_argument("--apply", action="store_true", help="Commit the pinned v1.8 Wave 3 batch")
    args = parser.parse_args()
    with SessionLocal() as session:
        _verify_preflight(session)
        print(
            "Preflight passed: backup, revision, counts, 11 manuals, 51 source pins, and trust gates match."
        )
        print("Planned outcome: 13 new identities and 51 mappings with reviewed menu contexts.")
        if not args.apply:
            print("Dry run only; pass --apply to commit the pinned reviewed batch.")
            return 0
        baseline = {model: _count(session, model) for model in BASELINE_COUNTS}
        json_temp = markdown_temp = None
        try:
            _apply(session)
            session.flush()
            _verify_final(session, baseline)
            payload = _payload(session)
            json_temp = _write_temp(
                REPORT_JSON, json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True) + "\n"
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
        print("Applied: new_identities=13, mappings=51, menu_contexts=51.")
        print(f"Remaining approved unmapped: {payload['summary']['remaining_manual_review']}.")
        print(f"JSON report: {REPORT_JSON}")
        print(f"Summary report: {REPORT_MARKDOWN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
