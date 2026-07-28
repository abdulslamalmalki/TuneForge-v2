# ruff: noqa: E501
"""Apply TuneForge's pinned v1.16 Intel platform-controls Wave 11.

Dry-run is the default. The batch contains eight canonical identities and 33
approved Source Settings visually verified in official ASUS, GIGABYTE, and MSI
manuals. Writes preserve citation-first, append-only, and reversible behavior.
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
BACKUP_PATH = ROOT / "data" / "backups" / "tuneforge-v1.16-pre-wave11.zip"
BACKUP_SHA256 = "9eccfe72fe87c10f7c5326f0e5b4fc688b9dcd4cd8db379082d825ab6a0e01f8"
REPORT_JSON = ROOT / "data" / "reports" / "v1.16-intel-platform-controls-wave11.json"
REPORT_MARKDOWN = ROOT / "docs" / "v1.16-intel-platform-controls-wave11.md"
ACTOR = "v1.16-intel-platform-controls-official-evidence-reviewer"
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
    expected_name: str
    menu_path: str
    evidence_excerpt: str


@dataclass(frozen=True)
class IdentitySeed:
    canonical_id: str
    canonical_name: str
    description: str
    rationale: str
    uncertainty: str
    category_id: str
    risk_level: str
    risk_rationale: str
    sources: tuple[SourcePin, ...]


MANUALS = (
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
        7,
        "gigabyte",
        "b46c52231334bfe914491cc3d6aa9265631914ba9c1de6bb6aa11841b46a2fe5",
        "https://www.gigabyte.com/Motherboard/Z890-AORUS-ELITE-WIFI7/support#support-manual",
        30,
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


def source(
    setting_id: int,
    manual_id: int,
    page_number: int,
    expected_name: str,
    menu_path: str,
    evidence_excerpt: str,
) -> SourcePin:
    return SourcePin(setting_id, manual_id, page_number, expected_name, menu_path, evidence_excerpt)


IDENTITIES = (
    IdentitySeed(
        "aggressive-lpm-support",
        "Aggressive LPM Support",
        "Enables or disables aggressive link power management for chipset SATA links.",
        "ASUS and GIGABYTE use the exact normalized name in PCH/SATA storage configuration; both official descriptions control aggressive link power-state entry for chipset SATA controllers.",
        "The manuals do not document identical controller support or power-state policy on every board.",
        "power-management",
        "moderate",
        "Changing SATA link power management can affect device power use, resume behavior, or compatibility.",
        (
            source(
                1104,
                3,
                61,
                "Aggressive LPM support",
                "Advanced > PCH Storage Configuration > Aggressive LPM support",
                "The ASUS manual allows the PCH to enter an aggressive link power state.",
            ),
            source(
                5649,
                7,
                22,
                "Aggressive LPM Support",
                "Settings > SATA Configuration > Aggressive LPM Support",
                "The GIGABYTE manual controls ALPM for chipset SATA controllers.",
            ),
            source(
                7014,
                10,
                21,
                "Aggressive LPM Support",
                "Settings > SATA Configuration > Aggressive LPM Support",
                "The GIGABYTE manual controls ALPM for chipset SATA controllers.",
            ),
            source(
                7548,
                11,
                57,
                "Aggressive LPM support",
                "Advanced > PCH Storage Configuration > Aggressive LPM support",
                "The ASUS manual allows the PCH to enter an aggressive link power state.",
            ),
            source(
                8198,
                12,
                60,
                "Aggressive LPM support",
                "Advanced > PCH Storage Configuration > Aggressive LPM support",
                "The ASUS manual allows the PCH to enter an aggressive link power state.",
            ),
        ),
    ),
    IdentitySeed(
        "control-iommu-pre-boot-behavior",
        "Control IOMMU Pre-boot Behavior",
        "Enables or disables IOMMU operation in the pre-boot firmware environment.",
        "ASUS and MSI use the exact normalized name and consistently describe the IOMMU control in the pre-boot environment; both document Intel VT-d as its enabling context.",
        "Firmware wording differs on the required DXE/PEI interfaces and option labels.",
        "virtualization",
        "moderate",
        "Changing pre-boot IOMMU behavior can affect firmware-stage device access and DMA-remapping-dependent boot flows.",
        (
            source(
                1093,
                3,
                59,
                "Control Iommu Pre-boot Behavior",
                "Advanced > System Agent (SA) Configuration > Control Iommu Pre-boot Behavior",
                "The ASUS manual enables IOMMU in the pre-boot environment when the documented DMAR and VT-d interfaces are present.",
            ),
            source(
                5057,
                5,
                64,
                "Control IOMMU Pre-boot Behavior",
                "OC > CPU Features > Control IOMMU Pre-boot Behavior",
                "The MSI manual enables or disables IOMMU in the pre-boot environment when Intel VT-d is enabled.",
            ),
            source(
                7525,
                11,
                54,
                "Control Iommu Pre-boot Behavior",
                "Advanced > System Agent (SA) Configuration > Control Iommu Pre-boot Behavior",
                "The ASUS manual enables IOMMU during boot or disables it in the pre-boot environment.",
            ),
            source(
                8182,
                12,
                57,
                "Control Iommu Pre-boot Behavior",
                "Advanced > System Agent (SA) Configuration > Control Iommu Pre-boot Behavior",
                "The ASUS manual enables IOMMU during boot or disables it in the pre-boot environment.",
            ),
        ),
    ),
    IdentitySeed(
        "dmi-aspm",
        "DMI ASPM",
        "Configures Active State Power Management for the CPU-to-chipset DMI link.",
        "ASUS and GIGABYTE use the exact normalized name and describe ASPM support or mode selection for the DMI link; GIGABYTE explicitly identifies both CPU and chipset sides.",
        "Available ASPM modes and the Platform Power Management dependency vary by firmware.",
        "power-management",
        "moderate",
        "Changing DMI link power states can affect platform power use, latency, or link compatibility.",
        (
            source(
                1071,
                3,
                57,
                "DMI ASPM",
                "Advanced > SA - PCI Express > DMI ASPM",
                "The ASUS manual configures DMI ASPM support with Disabled, Auto, or ASPM L1.",
            ),
            source(
                5604,
                7,
                18,
                "DMI ASPM",
                "Settings > Platform Power > DMI ASPM",
                "The GIGABYTE manual configures ASPM for both CPU and chipset sides of the DMI link.",
            ),
            source(
                6976,
                10,
                17,
                "DMI ASPM",
                "Settings > Platform Power > DMI ASPM",
                "The GIGABYTE manual configures ASPM for both CPU and chipset sides of the DMI link.",
            ),
            source(
                8137,
                12,
                53,
                "DMI ASPM",
                "Advanced > SA - PCI Express > DMI ASPM",
                "The ASUS manual configures DMI ASPM support and exposes L0s/L1 modes.",
            ),
        ),
    ),
    IdentitySeed(
        "dvmt-pre-allocated",
        "DVMT Pre-Allocated",
        "Sets the fixed system-memory allocation reserved for the integrated graphics device.",
        "ASUS and GIGABYTE use the exact normalized name in graphics configuration; ASUS identifies DVMT fixed graphics memory and GIGABYTE describes onboard graphics memory size.",
        "Available allocation sizes vary by board and firmware.",
        "peripheral-io",
        "moderate",
        "Changing pre-allocated graphics memory changes the system memory reserved for integrated graphics.",
        (
            source(
                1098,
                3,
                59,
                "DVMT Pre-Allocated",
                "Advanced > System Agent (SA) Configuration > Graphics Configuration > DVMT Pre-Allocated",
                "The ASUS manual selects fixed DVMT 5.0 memory used by the internal graphics device.",
            ),
            source(
                6987,
                10,
                19,
                "DVMT Pre-Allocated",
                "Settings > IO Ports > DVMT Pre-Allocated",
                "The GIGABYTE manual sets onboard graphics memory size.",
            ),
            source(
                7531,
                11,
                55,
                "DVMT Pre-Allocated",
                "Advanced > System Agent (SA) Configuration > Graphics Configuration > DVMT Pre-Allocated",
                "The ASUS manual selects fixed DVMT 5.0 memory used by the internal graphics device.",
            ),
            source(
                8188,
                12,
                58,
                "DVMT Pre-Allocated",
                "Advanced > System Agent (SA) Configuration > Graphics Configuration > DVMT Pre-Allocated",
                "The ASUS manual selects fixed DVMT 5.0 memory used by the internal graphics device.",
            ),
        ),
    ),
    IdentitySeed(
        "intel-speed-shift-technology",
        "Intel(R) Speed Shift Technology",
        "Enables or disables Intel Speed Shift hardware-controlled processor performance states.",
        "ASUS and GIGABYTE use the exact normalized name; ASUS documents hardware-controlled P-states and GIGABYTE documents faster processor frequency ramping and responsiveness.",
        "User-visible performance and power effects depend on processor and operating-system support.",
        "cpu-performance",
        "moderate",
        "Changing hardware-controlled performance-state behavior can alter responsiveness, power use, and thermals.",
        (
            source(
                1085,
                3,
                58,
                "Intel(R) Speed Shift Technology",
                "Advanced > CPU Configuration > CPU - Power Management Control > Intel(R) Speed Shift Technology",
                "The ASUS manual enables hardware-controlled P-states through the documented CPPC v2 interface.",
            ),
            source(
                5531,
                7,
                10,
                "Intel(R) Speed Shift Technology",
                "Tweaker > Advanced CPU Settings > Intel(R) Speed Shift Technology",
                "The GIGABYTE manual enables Intel Speed Shift and describes faster operating-frequency ramping.",
            ),
            source(
                7502,
                11,
                52,
                "Intel(R) Speed Shift Technology",
                "Advanced > CPU Configuration > CPU - Power Management Control > Intel(R) Speed Shift Technology",
                "The ASUS manual enables hardware-controlled P-states through the documented CPPC v2 interface.",
            ),
            source(
                8161,
                12,
                55,
                "Intel(R) Speed Shift Technology",
                "Advanced > CPU Configuration > CPU - Power Management Control > Intel(R) Speed Shift Technology",
                "The ASUS manual enables hardware-controlled P-states through the documented CPPC v2 interface.",
            ),
        ),
    ),
    IdentitySeed(
        "peg-aspm",
        "PEG ASPM",
        "Configures Active State Power Management for a device connected to the processor PCI Express Graphics bus.",
        "ASUS and GIGABYTE names normalize exactly after punctuation handling and both official descriptions control ASPM for the CPU PEG connection.",
        "Available modes, PEG instance numbering, and Platform Power Management dependencies vary by firmware.",
        "power-management",
        "moderate",
        "Changing PEG link power states can affect device power use, latency, or PCIe compatibility.",
        (
            source(
                5602,
                7,
                18,
                "PEG ASPM",
                "Settings > Platform Power > PEG ASPM",
                "The GIGABYTE manual configures ASPM for a device connected to the CPU PEG bus.",
            ),
            source(
                6974,
                10,
                17,
                "PEG ASPM",
                "Settings > Platform Power > PEG ASPM",
                "The GIGABYTE manual configures ASPM for a device connected to the CPU PEG bus.",
            ),
            source(
                7484,
                11,
                50,
                "PEG - ASPM",
                "Advanced > System Agent (SA) Configuration > SA - PCI Express > PEG - ASPM",
                "The ASUS manual controls ASPM support for PEG 0 and documents the available link states.",
            ),
            source(
                8139,
                12,
                53,
                "PEG - ASPM",
                "Advanced > System Agent (SA) Configuration > SA - PCI Express > PEG - ASPM",
                "The ASUS manual controls ASPM support for PEG 0 and documents the available link states.",
            ),
        ),
    ),
    IdentitySeed(
        "rc6-render-standby",
        "RC6(Render Standby)",
        "Enables or disables the integrated graphics render-standby power state.",
        "ASUS and GIGABYTE use the exact normalized name in graphics or platform-power configuration; both official descriptions control onboard/integrated graphics render standby for power reduction.",
        "The manuals do not document identical RC6 residency behavior for every processor generation.",
        "power-management",
        "moderate",
        "Changing integrated-graphics standby behavior can affect idle power use or graphics resume behavior.",
        (
            source(
                1099,
                3,
                60,
                "RC6(Render Standby)",
                "Advanced > System Agent (SA) Configuration > Graphics Configuration > RC6(Render Standby)",
                "The ASUS manual enables or disables render standby support.",
            ),
            source(
                5612,
                7,
                19,
                "RC6(Render Standby)",
                "Settings > Platform Power > RC6(Render Standby)",
                "The GIGABYTE manual allows onboard graphics to enter standby to decrease power consumption.",
            ),
            source(
                7533,
                11,
                55,
                "RC6(Render Standby)",
                "Advanced > System Agent (SA) Configuration > Graphics Configuration > RC6(Render Standby)",
                "The ASUS manual enables or disables render standby support.",
            ),
            source(
                8190,
                12,
                58,
                "RC6(Render Standby)",
                "Advanced > System Agent (SA) Configuration > Graphics Configuration > RC6(Render Standby)",
                "The ASUS manual enables or disables render standby support.",
            ),
        ),
    ),
    IdentitySeed(
        "tvb-voltage-optimizations",
        "TVB Voltage Optimizations",
        "Controls thermal-based voltage optimizations for processors that support Intel Thermal Velocity Boost.",
        "ASUS and MSI use the exact normalized name and both official descriptions control thermally based voltage optimization for processors implementing TVB.",
        "Processor support, default state, and the resulting voltage behavior are platform-specific.",
        "cpu-performance",
        "high",
        "Changing processor voltage optimization can affect performance, stability, power use, and thermals.",
        (
            source(
                907,
                3,
                40,
                "TVB Voltage Optimizations",
                "Ai Tweaker > Thermal Velocity Boost > TVB Voltage Optimizations",
                "The ASUS manual controls thermal-based voltage optimization for processors implementing TVB.",
            ),
            source(
                4802,
                5,
                43,
                "TVB Voltage Optimizations",
                "OC > Advanced CPU Configuration > TVB Voltage Optimizations",
                "The MSI manual enables or disables thermally based voltage optimization when the processor supports TVB.",
            ),
            source(
                7387,
                11,
                37,
                "TVB Voltage Optimizations",
                "Ai Tweaker > Thermal Velocity Boost > TVB Voltage Optimizations",
                "The ASUS manual controls thermal-based voltage optimization for processors implementing TVB.",
            ),
            source(
                8038,
                12,
                39,
                "TVB Voltage Optimizations",
                "Ai Tweaker > Thermal Velocity Boost > TVB Voltage Optimizations",
                "The ASUS manual controls thermal-based voltage optimization for processors implementing TVB.",
            ),
        ),
    ),
)

KNOWN_AMBIGUOUS_IDS = {572, 6111, 6690, 7392, 7395, 7657, 8320}
KNOWN_REJECTED_DUPLICATE_IDS = {4901}

BASELINE_COUNTS = {
    CanonicalSetting: 166,
    CanonicalSettingAlias: 202,
    SettingCanonicalMapping: 756,
    CanonicalMappingEvent: 756,
    CanonicalIdentityEvent: 181,
    CanonicalSettingClassification: 166,
    CanonicalClassificationEvent: 293,
    SettingMenuContext: 553,
    SettingMenuContextEvent: 553,
    SettingMenuContextCitation: 627,
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


def _all_sources() -> tuple[SourcePin, ...]:
    return tuple(item for seed in IDENTITIES for item in seed.sources)


def _verify_preflight(session) -> None:
    if not BACKUP_PATH.is_file() or _sha256(BACKUP_PATH) != BACKUP_SHA256:
        raise RuntimeError("The verified pre-v1.16 backup is missing or changed.")
    if inspect_backup(BACKUP_PATH).database_revision != config.EXPECTED_DATABASE_REVISION:
        raise RuntimeError("The pre-v1.16 backup has an unexpected revision.")
    revision = session.scalar(text("SELECT version_num FROM alembic_version"))
    if revision != config.EXPECTED_DATABASE_REVISION:
        raise RuntimeError(f"Live schema revision changed: {revision!r}.")
    if session.scalar(text("PRAGMA integrity_check")) != "ok":
        raise RuntimeError("SQLite integrity_check failed before Wave 11.")
    if _unmapped_count(session) != 3262:
        raise RuntimeError(f"Expected 3,262 unmapped settings, found {_unmapped_count(session)}.")
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
        expected = (pin.vendor_id, pin.sha256, pin.source_url, pin.page_count)
        if actual != expected:
            raise RuntimeError(f"Pinned manual {pin.manual_id} changed: {actual!r}.")

    sources = _all_sources()
    if len(IDENTITIES) != 8 or len(sources) != 33 or len({x.setting_id for x in sources}) != 33:
        raise RuntimeError("Wave 11 must contain eight identities and 33 unique records.")
    excluded = KNOWN_AMBIGUOUS_IDS | KNOWN_REJECTED_DUPLICATE_IDS
    if excluded.intersection({item.setting_id for item in sources}):
        raise RuntimeError("A known excluded record entered Wave 11.")

    for seed in IDENTITIES:
        if session.get(CanonicalSetting, seed.canonical_id) is not None:
            raise RuntimeError(f"Canonical identity already exists: {seed.canonical_id}.")
        vendors = {pins[item.manual_id].vendor_id for item in seed.sources}
        if len(vendors) < 2:
            raise RuntimeError(f"{seed.canonical_id} lacks cross-vendor evidence.")
        for item in seed.sources:
            setting = session.get(Setting, item.setting_id)
            manual = session.get(Manual, item.manual_id)
            actual = (
                (
                    setting.manual_id,
                    setting.edited_page_number or setting.page_number,
                    normalize_alias(effective_name(setting)),
                )
                if setting
                else None
            )
            expected = (item.manual_id, item.page_number, normalize_alias(item.expected_name))
            if actual != expected:
                raise RuntimeError(f"Pinned source {item.setting_id} changed: {actual!r}.")
            reasons = canonicalization.source_trust_reasons(setting, manual)
            if reasons:
                raise RuntimeError(
                    f"Source {item.setting_id} failed trust gates: {' '.join(reasons)}"
                )
            if session.get(SettingCanonicalMapping, item.setting_id) is not None:
                raise RuntimeError(f"Source {item.setting_id} is already mapped.")
            if session.get(SettingMenuContext, item.setting_id) is not None:
                raise RuntimeError(f"Source {item.setting_id} already has reviewed menu context.")

    for setting_id in KNOWN_AMBIGUOUS_IDS:
        if session.get(SettingCanonicalMapping, setting_id) is not None:
            raise RuntimeError(f"Known ambiguous source {setting_id} was unexpectedly mapped.")
    for setting_id in KNOWN_REJECTED_DUPLICATE_IDS:
        setting = session.get(Setting, setting_id)
        if setting is None or setting.status != "rejected":
            raise RuntimeError(f"Known rejected duplicate {setting_id} changed review state.")
        if session.get(SettingCanonicalMapping, setting_id) is not None:
            raise RuntimeError(f"Known rejected duplicate {setting_id} was unexpectedly mapped.")


def _mapping_note(session, seed: IdentitySeed, item: SourcePin) -> str:
    manual = session.get(Manual, item.manual_id)
    return f"v1.16 Wave 11 reviewer decision; confidence={CONFIDENCE_BASIS_POINTS / 100:.2f}%. {seed.rationale} Official source: {manual.vendor} manual {manual.id}, page {item.page_number}, {manual.source_url}; reviewed menu path: {item.menu_path}. Remaining uncertainty: {seed.uncertainty}"


def _apply(session) -> None:
    for seed in IDENTITIES:
        identity_row = canonical_settings.create_identity(
            session,
            name=seed.canonical_name,
            canonical_id=seed.canonical_id,
            description=seed.description,
            actor=ACTOR,
            commit=False,
        )
        canonical_settings.classify_identity(
            session,
            identity_row,
            category_id=seed.category_id,
            risk_level=seed.risk_level,
            risk_rationale=seed.risk_rationale,
            actor=ACTOR,
            commit=False,
        )
        for item in seed.sources:
            setting = session.get(Setting, item.setting_id)
            canonical_identity_review.review_menu_context(
                session,
                setting.id,
                menu_path=item.menu_path,
                source_page_number=item.page_number,
                evidence_excerpt=item.evidence_excerpt,
                note="v1.16 Wave 11 official-manual name, behavior, options, and menu hierarchy visually verified.",
                actor=ACTOR,
                commit=False,
            )
            canonical_settings.map_setting(
                session,
                setting,
                canonical_id=seed.canonical_id,
                note=_mapping_note(session, seed, item),
                actor=ACTOR,
                mapping_method=canonical_settings.MAPPING_REVIEWER,
                commit=False,
            )


def _verify_final(session, baseline: dict[type, int]) -> None:
    deltas = {
        CanonicalSetting: 8,
        CanonicalSettingAlias: 8,
        SettingCanonicalMapping: 33,
        CanonicalMappingEvent: 33,
        CanonicalIdentityEvent: 8,
        CanonicalSettingClassification: 8,
        CanonicalClassificationEvent: 16,
        SettingMenuContext: 33,
        SettingMenuContextEvent: 33,
        SettingMenuContextCitation: 33,
    }
    failures = [
        f"{model.__tablename__} append-only delta mismatch"
        for model, delta in deltas.items()
        if _count(session, model) != baseline[model] + delta
    ]
    if _unmapped_count(session) != 3229:
        failures.append(f"expected 3,229 unmapped, found {_unmapped_count(session)}")
    for seed in IDENTITIES:
        expected = {item.setting_id for item in seed.sources}
        detail = library.canonical_detail(session, seed.canonical_id)
        public = {item.setting_id for item in detail.source_settings} if detail else set()
        if not expected.issubset(public):
            failures.append(f"{seed.canonical_id} public projection mismatch")
        elif any(
            not item.menu_path for item in detail.source_settings if item.setting_id in expected
        ):
            failures.append(f"{seed.canonical_id} public menu path missing")
    excluded = KNOWN_AMBIGUOUS_IDS | KNOWN_REJECTED_DUPLICATE_IDS
    if any(session.get(SettingCanonicalMapping, setting_id) is not None for setting_id in excluded):
        failures.append("a known excluded record was mapped")
    if session.scalar(text("PRAGMA integrity_check")) != "ok":
        failures.append("SQLite integrity_check failed")
    foreign_keys = list(session.execute(text("PRAGMA foreign_key_check")))
    if foreign_keys:
        failures.append(f"foreign-key violations: {foreign_keys!r}")
    if failures:
        raise RuntimeError("Wave 11 verification failed:\n- " + "\n- ".join(failures))


def _build_payload(session) -> dict[str, object]:
    decisions = []
    for seed in IDENTITIES:
        sources = []
        for item in seed.sources:
            setting = session.get(Setting, item.setting_id)
            manual = session.get(Manual, item.manual_id)
            sources.append(
                {
                    "setting_id": setting.id,
                    "vendor_id": manual.vendor_id,
                    "manual_id": manual.id,
                    "source_name": effective_name(setting),
                    "source_description": effective_description(setting),
                    "page_number": item.page_number,
                    "menu_path": item.menu_path,
                    "official_url": manual.source_url,
                    "confidence_percent": CONFIDENCE_BASIS_POINTS / 100,
                }
            )
        decisions.append(
            {
                "canonical_id": seed.canonical_id,
                "canonical_name": seed.canonical_name,
                "description": seed.description,
                "rationale": seed.rationale,
                "remaining_uncertainty": seed.uncertainty,
                "sources": sources,
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
            "reason": "Not approved in Wave 11; requires a later one-by-one evidence disposition.",
        }
        for setting, manual in remaining_rows
    ]
    audit = build_public_audit(session)
    if audit["status"] != "pass":
        raise RuntimeError("Public BIOS knowledge audit failed after Wave 11.")
    return {
        "milestone": "v1.16 Intel platform-controls canonicalization Wave 11",
        "generated_at": utc_now_iso(),
        "actor": ACTOR,
        "backup": {"path": str(BACKUP_PATH), "sha256": BACKUP_SHA256},
        "database_revision": config.EXPECTED_DATABASE_REVISION,
        "summary": {
            "new_canonical_settings": 8,
            "total_mappings_created": 33,
            "reviewed_menu_contexts": 33,
            "remaining_manual_review": len(remaining),
        },
        "official_manuals": [asdict(pin) for pin in MANUALS],
        "approved_decisions": decisions,
        "intentional_exclusions": [
            {
                "setting_ids": sorted(KNOWN_AMBIGUOUS_IDS),
                "reason": "Previously reviewed official conflicts and insufficient-evidence records remain unmapped.",
            },
            {
                "setting_ids": sorted(KNOWN_REJECTED_DUPLICATE_IDS),
                "reason": "The duplicate tXPDLL extraction remains rejected and unpublished.",
            },
        ],
        "remaining_manual_review": remaining,
        "public_knowledge_audit": audit,
    }


def _markdown(payload: dict[str, object]) -> str:
    summary = payload["summary"]
    lines = [
        "# TuneForge v1.16 Intel Platform Controls Wave 11",
        "",
        "## Outcome",
        "",
        f"- New canonical settings: {summary['new_canonical_settings']}",
        f"- New verified mappings: {summary['total_mappings_created']}",
        f"- New reviewed menu contexts: {summary['reviewed_menu_contexts']}",
        f"- Remaining approved unmapped settings: {summary['remaining_manual_review']}",
        "",
        "Every decision uses visually reviewed official ASUS, GIGABYTE, and MSI manual pages. Normalized names, documented behavior, options, and menu context agree; no community source was required.",
        "",
        "## Approved identities",
        "",
    ]
    for decision in payload["approved_decisions"]:
        vendors = sorted({item["vendor_id"] for item in decision["sources"]})
        lines.append(
            f"- **{decision['canonical_name']}** - {len(decision['sources'])} mappings across {len(vendors)} vendors: {', '.join(vendors)}. {decision['rationale']}"
        )
    lines.extend(
        [
            "",
            "## Intentional exclusions",
            "",
            "Previously documented ambiguous records and all candidates lacking equivalent official evidence remain unmapped. The complete machine-readable decision report and pending backlog are stored in `data/reports/v1.16-intel-platform-controls-wave11.json`.",
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
    parser.add_argument(
        "--apply", action="store_true", help="Commit the pinned v1.16 Wave 11 batch"
    )
    args = parser.parse_args()
    with SessionLocal() as session:
        _verify_preflight(session)
        print(
            "Preflight passed: backup, revision, counts, six manuals, 33 source pins, exclusions, and trust gates match."
        )
        print("Planned outcome: eight new identities and 33 mappings with reviewed menu contexts.")
        if not args.apply:
            print("Dry run only; pass --apply to commit the pinned reviewed batch.")
            return 0
        baseline = {model: _count(session, model) for model in BASELINE_COUNTS}
        json_temp = markdown_temp = None
        try:
            _apply(session)
            session.flush()
            _verify_final(session, baseline)
            payload = _build_payload(session)
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
        print("Applied: new_identities=8, mappings=33, menu_contexts=33.")
        print(f"Remaining approved unmapped: {payload['summary']['remaining_manual_review']}.")
        print(f"JSON report: {REPORT_JSON}")
        print(f"Summary report: {REPORT_MARKDOWN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
