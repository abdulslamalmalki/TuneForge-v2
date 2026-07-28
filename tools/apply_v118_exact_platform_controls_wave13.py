# ruff: noqa: E501
"""Apply TuneForge's pinned v1.18 exact platform-controls Wave 13.

Dry-run is the default. Ten identities and 34 approved Source Settings were
visually verified in official ASRock, ASUS, GIGABYTE, and MSI manuals.
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
BACKUP_PATH = ROOT / "data" / "backups" / "tuneforge-v1.18-pre-wave13.zip"
BACKUP_SHA256 = "78c509634f7aef116777444ed17de151e526629581b1a1a78b9f16f87e428521"
REPORT_JSON = ROOT / "data" / "reports" / "v1.18-exact-platform-controls-wave13.json"
REPORT_MARKDOWN = ROOT / "docs" / "v1.18-exact-platform-controls-wave13.md"
ACTOR = "v1.18-exact-platform-controls-official-evidence-reviewer"
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


def src(
    setting_id: int, manual_id: int, page: int, name: str, path: str, excerpt: str
) -> SourcePin:
    return SourcePin(setting_id, manual_id, page, name, path, excerpt)


MEMORY_RISK = "Runtime or profile-based memory changes can cause failed training, instability, data corruption, or require firmware recovery."
IDENTITIES = (
    IdentitySeed(
        "realtime-memory-timing",
        "Realtime Memory Timing",
        "Allows memory timings to be changed after firmware memory initialization has completed.",
        "ASUS and GIGABYTE use the exact normalized name in advanced memory controls; ASUS identifies changes after MRC_DONE and GIGABYTE describes fine-tuning after the BIOS stage.",
        "The manuals do not guarantee which timings can be changed at runtime on every platform.",
        "memory",
        "high",
        MEMORY_RISK,
        (
            src(
                950,
                3,
                44,
                "Realtime Memory Timing",
                "Ai Tweaker > Tweaker's Paradise > Realtime Memory Timing",
                "The ASUS manual permits realtime timing changes after MRC_DONE.",
            ),
            src(
                5586,
                7,
                15,
                "Realtime Memory Timing",
                "Tweaker > Advanced Memory Settings > Realtime Memory Timing",
                "The GIGABYTE manual permits memory timing fine-tuning after the BIOS stage.",
            ),
            src(
                6959,
                10,
                14,
                "Realtime Memory Timing",
                "Tweaker > Advanced Memory Settings > Realtime Memory Timing",
                "The GIGABYTE manual permits memory timing fine-tuning after the BIOS stage.",
            ),
            src(
                7401,
                11,
                38,
                "Realtime Memory Timing",
                "Ai Tweaker > Tweaker's Paradise > Realtime Memory Timing",
                "The ASUS manual permits realtime timing changes after MRC_DONE.",
            ),
            src(
                8052,
                12,
                41,
                "Realtime Memory Timing",
                "Ai Tweaker > Tweaker's Paradise > Realtime Memory Timing",
                "The ASUS manual permits realtime timing changes after MRC_DONE.",
            ),
        ),
    ),
    IdentitySeed(
        "dynamic-memory-boost",
        "Dynamic Memory Boost",
        "Enables automatic switching between the default SPD memory frequency and a selected XMP profile frequency.",
        "ASUS and MSI use the exact normalized name in memory-frequency controls; ASUS documents automatic SPD/XMP switching and MSI identifies the same memory boost control.",
        "Exact profile prerequisites and switching policy vary by firmware.",
        "memory",
        "high",
        MEMORY_RISK,
        (
            src(
                855,
                3,
                34,
                "Dynamic Memory Boost",
                "Ai Tweaker > Configure Memory Dynamic Frequency Switching > Dynamic Memory Boost",
                "The ASUS manual documents automatic switching between SPD and selected XMP frequency.",
            ),
            src(
                4842,
                5,
                47,
                "Dynamic Memory Boost",
                "OC > Advanced DRAM Configuration > Dynamic Memory Boost",
                "The MSI manual enables or disables the memory boost feature.",
            ),
            src(
                7330,
                11,
                31,
                "Dynamic Memory Boost",
                "Ai Tweaker > Configure Memory Dynamic Frequency Switching > Dynamic Memory Boost",
                "The ASUS manual documents automatic switching between SPD and selected XMP frequency.",
            ),
            src(
                7992,
                12,
                34,
                "Dynamic Memory Boost",
                "Ai Tweaker > Configure Memory Dynamic Frequency Switching > Dynamic Memory Boost",
                "The ASUS manual documents automatic switching between SPD and selected XMP frequency.",
            ),
        ),
    ),
    IdentitySeed(
        "realtime-memory-frequency",
        "Realtime Memory Frequency",
        "Enables runtime switching between the default SPD memory frequency and an XMP profile frequency.",
        "ASUS and MSI use the exact normalized name beside Dynamic Memory Boost; ASUS documents manual runtime SPD/XMP switching and MSI identifies the same realtime frequency control.",
        "The selected XMP profile and available runtime transitions vary by firmware.",
        "memory",
        "high",
        MEMORY_RISK,
        (
            src(
                856,
                3,
                35,
                "Realtime Memory Frequency",
                "Ai Tweaker > Configure Memory Dynamic Frequency Switching > Realtime Memory Frequency",
                "The ASUS manual documents manual runtime switching between SPD and XMP frequency.",
            ),
            src(
                4843,
                5,
                47,
                "Realtime Memory Frequency",
                "OC > Advanced DRAM Configuration > Realtime Memory Frequency",
                "The MSI manual enables or disables realtime memory frequency.",
            ),
            src(
                7331,
                11,
                31,
                "Realtime Memory Frequency",
                "Ai Tweaker > Configure Memory Dynamic Frequency Switching > Realtime Memory Frequency",
                "The ASUS manual documents manual runtime switching between SPD and XMP frequency.",
            ),
            src(
                7993,
                12,
                34,
                "Realtime Memory Frequency",
                "Ai Tweaker > Configure Memory Dynamic Frequency Switching > Realtime Memory Frequency",
                "The ASUS manual documents manual runtime switching between SPD and XMP frequency.",
            ),
        ),
    ),
    IdentitySeed(
        "next-boot-after-ac-power-loss",
        "Next Boot after AC Power Loss",
        "Selects normal or fast boot behavior for the first boot after AC power is restored.",
        "ASUS and GIGABYTE use the exact normalized name under Boot and provide matching Normal Boot and Fast Boot behavior after AC power loss.",
        "Availability depends on Fast Boot state and firmware.",
        "boot",
        "moderate",
        "Changing this option can alter initialization behavior on the first boot after AC power loss.",
        (
            src(
                6713,
                8,
                107,
                "Next Boot after AC Power Loss",
                "Boot > Boot Configuration > Next Boot after AC Power Loss",
                "The ASUS manual selects Normal Boot or Fast Boot after AC power loss.",
            ),
            src(
                7054,
                10,
                26,
                "Next Boot After AC Power Loss",
                "Boot > Next Boot After AC Power Loss",
                "The GIGABYTE manual selects Normal Boot or preserves Fast Boot after AC power loss.",
            ),
            src(
                7680,
                11,
                78,
                "Next Boot after AC Power Loss",
                "Boot > Boot Configuration > Next Boot after AC Power Loss",
                "The ASUS manual selects Normal Boot or Fast Boot after AC power loss.",
            ),
            src(
                8343,
                12,
                83,
                "Next Boot after AC Power Loss",
                "Boot > Boot Configuration > Next Boot after AC Power Loss",
                "The ASUS manual selects Normal Boot or Fast Boot after AC power loss.",
            ),
        ),
    ),
    IdentitySeed(
        "cfg-lock",
        "CFG Lock",
        "Locks or unlocks the processor configuration lock bit in MSR 0xE2.",
        "MSI and GIGABYTE use the exact normalized name; MSI identifies MSR 0xE2 bit 15 and GIGABYTE identifies the MSR 0xE2 function.",
        "GIGABYTE does not state the bit position on the reviewed pages.",
        "security",
        "moderate",
        "Changing a processor configuration lock can affect whether firmware or software may modify protected processor configuration.",
        (
            src(
                5062,
                5,
                64,
                "CFG Lock",
                "OC > CPU Features > CFG Lock",
                "The MSI manual locks or unlocks MSR 0xE2 bit 15.",
            ),
            src(
                5688,
                7,
                28,
                "CFG Lock",
                "Boot > CFG Lock",
                "The GIGABYTE manual enables or disables the MSR 0xE2 function.",
            ),
            src(
                7045,
                10,
                25,
                "CFG Lock",
                "Boot > CFG Lock",
                "The GIGABYTE manual enables or disables the MSR 0xE2 function.",
            ),
        ),
    ),
    IdentitySeed(
        "dtbt-go2sx-command",
        "DTBT Go2Sx Command",
        "Controls whether discrete Thunderbolt executes its Go2Sx command while the system enters an Sx state.",
        "ASUS and MSI use the exact normalized name in discrete Thunderbolt configuration and describe the same command before/during entry to Sx states.",
        "The specific Sx states and controller behavior are platform-dependent.",
        "power-management",
        "moderate",
        "Changing controller sleep commands can affect suspend or resume behavior of discrete Thunderbolt devices.",
        (
            src(
                4571,
                5,
                21,
                "DTBT Go2Sx Command",
                "Settings > Advanced > Intel Thunderbolt > Discrete Thunderbolt(TM) Configuration > DTBT Go2Sx Command",
                "The MSI manual controls DTBT Go2Sx execution before entry to Sx.",
            ),
            src(
                7558,
                11,
                59,
                "DTBT Go2Sx Command",
                "Advanced > Thunderbolt(TM) Configuration > Discrete Thunderbolt(TM) Configuration > DTBT Go2Sx Command",
                "The ASUS manual controls the command that places DTBT into Sx as the system enters Sx.",
            ),
            src(
                8208,
                12,
                62,
                "DTBT Go2Sx Command",
                "Advanced > Thunderbolt(TM) Configuration > Discrete Thunderbolt(TM) Configuration > DTBT Go2Sx Command",
                "The ASUS manual controls the command that places DTBT into Sx as the system enters Sx.",
            ),
        ),
    ),
    IdentitySeed(
        "enhanced-tvb",
        "Enhanced TVB",
        "Enables, disables, or automatically configures Enhanced Intel Thermal Velocity Boost on supported processors.",
        "ASUS and GIGABYTE use the exact normalized name in TVB/CPU controls; GIGABYTE identifies Enhanced Thermal Velocity Boost and ASUS limits the option to supported SKUs.",
        "Exact boost behavior and supported processor SKUs vary by platform.",
        "cpu-performance",
        "high",
        "Changing thermal boost behavior can affect processor frequency, power, temperature, and stability margins.",
        (
            src(
                908,
                3,
                40,
                "Enhanced TVB",
                "Ai Tweaker > Thermal Velocity Boost > Enhanced TVB",
                "The ASUS manual exposes Enhanced TVB only on supported SKUs.",
            ),
            src(
                5539,
                7,
                11,
                "Enhanced TVB",
                "Tweaker > Advanced CPU Settings > Enhanced TVB",
                "The GIGABYTE manual enables, disables, or automatically configures Enhanced TVB.",
            ),
            src(
                8039,
                12,
                40,
                "Enhanced TVB",
                "Ai Tweaker > Thermal Velocity Boost > Enhanced TVB",
                "The ASUS manual exposes Enhanced TVB only on supported SKUs.",
            ),
        ),
    ),
    IdentitySeed(
        "internal-graphics",
        "Internal Graphics",
        "Enables or disables the processor's internal/onboard graphics function.",
        "ASUS and GIGABYTE use the exact normalized name in graphics/IO configuration and both describe enabling or disabling internal/onboard graphics.",
        "Display-routing behavior and availability depend on the installed processor and board outputs.",
        "peripheral-io",
        "moderate",
        "Disabling internal graphics can remove firmware and operating-system access to motherboard display outputs.",
        (
            src(
                1097,
                3,
                59,
                "Internal Graphics",
                "Advanced > System Agent (SA) Configuration > Graphics Configuration > Internal Graphics",
                "The ASUS manual enables or disables IGFX.",
            ),
            src(
                5616,
                7,
                20,
                "Internal Graphics",
                "Settings > IO Ports > Internal Graphics",
                "The GIGABYTE manual enables or disables onboard graphics.",
            ),
            src(
                6986,
                10,
                19,
                "Internal Graphics",
                "Settings > IO Ports > Internal Graphics",
                "The GIGABYTE manual enables or disables onboard graphics.",
            ),
        ),
    ),
    IdentitySeed(
        "discrete-thunderbolt-support",
        "Discrete Thunderbolt(TM) Support",
        "Enables or disables support for a discrete Thunderbolt controller.",
        "ASUS and MSI use the exact normalized name in Thunderbolt configuration and both describe enabling or disabling discrete Thunderbolt device support.",
        "DMA-protection prerequisites, controller generation, and downstream options vary by platform.",
        "peripheral-io",
        "high",
        "Thunderbolt support and its IOMMU dependency can affect external-device access and DMA-protection behavior.",
        (
            src(
                4565,
                5,
                20,
                "Discrete Thunderbolt(TM) Support",
                "Settings > Advanced > Intel Thunderbolt > Discrete Thunderbolt(TM) Support",
                "The MSI manual enables or disables discrete Thunderbolt device support.",
            ),
            src(
                7554,
                11,
                58,
                "Discrete Thunderbolt(TM) Support",
                "Advanced > Thunderbolt(TM) Configuration > Discrete Thunderbolt(TM) Support",
                "The ASUS manual enables or disables discrete Thunderbolt and documents the pre-boot IOMMU dependency for DMA protection.",
            ),
            src(
                8205,
                12,
                62,
                "Discrete Thunderbolt(TM) Support",
                "Advanced > Thunderbolt(TM) Configuration > Discrete Thunderbolt(TM) Support",
                "The ASUS manual enables or disables discrete Thunderbolt and documents the pre-boot IOMMU dependency for DMA protection.",
            ),
        ),
    ),
    IdentitySeed(
        "fch-spread-spectrum",
        "FCH Spread Spectrum",
        "Enables or disables spread-spectrum modulation associated with the AMD FCH/base-clock path.",
        "ASRock and MSI use the exact normalized name under AMD platform controls; both enable or disable FCH spread spectrum, and MSI documents EMI and base-clock-overclocking effects.",
        "ASRock does not document the same option labels or clock-effect detail on the reviewed page.",
        "cpu-performance",
        "high",
        "Changing clock spread spectrum can affect electromagnetic emissions, base-clock behavior, and overclocking stability.",
        (
            src(
                487,
                2,
                66,
                "FCH Spread Spectrum",
                "Advanced > AMD CBS > NBIO Common Options > FCH Common Options > FCH Spread Spectrum",
                "The ASRock manual enables or disables the FCH spread-spectrum feature.",
            ),
            src(
                5312,
                6,
                31,
                "FCH Spread Spectrum",
                "OC > Advanced CPU Configuration > AMD CBS > FCH Spread Spectrum",
                "The MSI manual controls FCH spread spectrum and documents EMI and base-clock stability effects.",
            ),
        ),
    ),
)

KNOWN_AMBIGUOUS_IDS = {572, 6111, 6690, 7392, 7395, 7657, 8320}
KNOWN_COLLISION_IDS = {47, 77, 435, 491, 5137, 5284, 5292, 6158, 6161, 6321, 6378}
KNOWN_REJECTED_DUPLICATE_IDS = {4901}
BASELINE_COUNTS = {
    CanonicalSetting: 199,
    CanonicalSettingAlias: 235,
    SettingCanonicalMapping: 839,
    CanonicalMappingEvent: 839,
    CanonicalIdentityEvent: 214,
    CanonicalSettingClassification: 199,
    CanonicalClassificationEvent: 359,
    SettingMenuContext: 636,
    SettingMenuContextEvent: 636,
    SettingMenuContextCitation: 710,
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
    return tuple(item for seed in IDENTITIES for item in seed.sources)


def _verify_preflight(session) -> None:
    if not BACKUP_PATH.is_file() or _sha256(BACKUP_PATH) != BACKUP_SHA256:
        raise RuntimeError("The verified pre-v1.18 backup is missing or changed.")
    if inspect_backup(BACKUP_PATH).database_revision != config.EXPECTED_DATABASE_REVISION:
        raise RuntimeError("The pre-v1.18 backup has an unexpected revision.")
    if (
        session.scalar(text("SELECT version_num FROM alembic_version"))
        != config.EXPECTED_DATABASE_REVISION
    ):
        raise RuntimeError("The live schema revision changed.")
    if session.scalar(text("PRAGMA integrity_check")) != "ok":
        raise RuntimeError("SQLite integrity_check failed before Wave 13.")
    if _unmapped_count(session) != 3179:
        raise RuntimeError(f"Expected 3,179 unmapped settings, found {_unmapped_count(session)}.")
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
    if len(IDENTITIES) != 10 or len(sources) != 34 or len({x.setting_id for x in sources}) != 34:
        raise RuntimeError("Wave 13 must contain ten identities and 34 unique records.")
    excluded = KNOWN_AMBIGUOUS_IDS | KNOWN_COLLISION_IDS | KNOWN_REJECTED_DUPLICATE_IDS
    if excluded.intersection({item.setting_id for item in sources}):
        raise RuntimeError("A known excluded record entered Wave 13.")
    for seed in IDENTITIES:
        if session.get(CanonicalSetting, seed.canonical_id) is not None:
            raise RuntimeError(f"Canonical identity already exists: {seed.canonical_id}.")
        if len({pins[item.manual_id].vendor_id for item in seed.sources}) < 2:
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
                raise RuntimeError(
                    f"Pinned source {item.setting_id} changed: {actual!r} != {expected!r}."
                )
            reasons = canonicalization.source_trust_reasons(setting, manual)
            if reasons:
                raise RuntimeError(
                    f"Source {item.setting_id} failed trust gates: {' '.join(reasons)}"
                )
            if (
                session.get(SettingCanonicalMapping, item.setting_id) is not None
                or session.get(SettingMenuContext, item.setting_id) is not None
            ):
                raise RuntimeError(
                    f"Source {item.setting_id} is no longer an untouched backlog record."
                )
    for setting_id in KNOWN_AMBIGUOUS_IDS | KNOWN_COLLISION_IDS:
        if session.get(SettingCanonicalMapping, setting_id) is not None:
            raise RuntimeError(f"Known excluded source {setting_id} was unexpectedly mapped.")
    for setting_id in KNOWN_REJECTED_DUPLICATE_IDS:
        setting = session.get(Setting, setting_id)
        if (
            setting is None
            or setting.status != "rejected"
            or session.get(SettingCanonicalMapping, setting_id) is not None
        ):
            raise RuntimeError(f"Known rejected duplicate {setting_id} changed state.")


def _mapping_note(session, seed: IdentitySeed, item: SourcePin) -> str:
    manual = session.get(Manual, item.manual_id)
    return f"v1.18 Wave 13 reviewer decision; confidence={CONFIDENCE_BASIS_POINTS / 100:.2f}%. {seed.rationale} Official source: {manual.vendor} manual {manual.id}, page {item.page_number}, {manual.source_url}; reviewed menu path: {item.menu_path}. Remaining uncertainty: {seed.uncertainty}"


def _apply(session) -> None:
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
        for item in seed.sources:
            setting = session.get(Setting, item.setting_id)
            canonical_identity_review.review_menu_context(
                session,
                setting.id,
                menu_path=item.menu_path,
                source_page_number=item.page_number,
                evidence_excerpt=item.evidence_excerpt,
                note="v1.18 Wave 13 official-manual label, behavior, prerequisites, options, and menu hierarchy visually verified.",
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
        CanonicalSetting: 10,
        CanonicalSettingAlias: 10,
        SettingCanonicalMapping: 34,
        CanonicalMappingEvent: 34,
        CanonicalIdentityEvent: 10,
        CanonicalSettingClassification: 10,
        CanonicalClassificationEvent: 20,
        SettingMenuContext: 34,
        SettingMenuContextEvent: 34,
        SettingMenuContextCitation: 34,
    }
    failures = [
        f"{model.__tablename__} append-only delta mismatch"
        for model, delta in deltas.items()
        if _count(session, model) != baseline[model] + delta
    ]
    if _unmapped_count(session) != 3145:
        failures.append(f"expected 3,145 unmapped, found {_unmapped_count(session)}")
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
    excluded = KNOWN_AMBIGUOUS_IDS | KNOWN_COLLISION_IDS | KNOWN_REJECTED_DUPLICATE_IDS
    if any(session.get(SettingCanonicalMapping, setting_id) is not None for setting_id in excluded):
        failures.append("a known excluded record was mapped")
    if session.scalar(text("PRAGMA integrity_check")) != "ok":
        failures.append("SQLite integrity_check failed")
    foreign_keys = list(session.execute(text("PRAGMA foreign_key_check")))
    if foreign_keys:
        failures.append(f"foreign-key violations: {foreign_keys!r}")
    if failures:
        raise RuntimeError("Wave 13 verification failed:\n- " + "\n- ".join(failures))


def _payload(session) -> dict[str, object]:
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
    rows = session.execute(
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
            "reason": "Not approved in Wave 13; requires a later evidence disposition.",
        }
        for setting, manual in rows
    ]
    audit = build_public_audit(session)
    if audit["status"] != "pass":
        raise RuntimeError("Public BIOS knowledge audit failed after Wave 13.")
    return {
        "milestone": "v1.18 exact platform-controls canonicalization Wave 13",
        "generated_at": utc_now_iso(),
        "actor": ACTOR,
        "backup": {"path": str(BACKUP_PATH), "sha256": BACKUP_SHA256},
        "database_revision": config.EXPECTED_DATABASE_REVISION,
        "summary": {
            "new_canonical_settings": 10,
            "total_mappings_created": 34,
            "reviewed_menu_contexts": 34,
            "remaining_manual_review": len(remaining),
        },
        "official_manuals": [asdict(pin) for pin in MANUALS],
        "approved_decisions": decisions,
        "intentional_exclusions": [
            {
                "setting_ids": sorted(KNOWN_AMBIGUOUS_IDS),
                "reason": "Previously reviewed conflicts and insufficient evidence.",
            },
            {
                "setting_ids": sorted(KNOWN_COLLISION_IDS),
                "reason": "Exact repeated labels with conflicting official semantics remain unmapped.",
            },
            {
                "setting_ids": sorted(KNOWN_REJECTED_DUPLICATE_IDS),
                "reason": "Rejected duplicate remains unpublished.",
            },
        ],
        "remaining_manual_review": remaining,
        "public_knowledge_audit": audit,
    }


def _markdown(payload: dict[str, object]) -> str:
    summary = payload["summary"]
    lines = [
        "# TuneForge v1.18 Exact Platform Controls Wave 13",
        "",
        "## Outcome",
        "",
        f"- New canonical settings: {summary['new_canonical_settings']}",
        f"- New verified mappings: {summary['total_mappings_created']}",
        f"- New reviewed menu contexts: {summary['reviewed_menu_contexts']}",
        f"- Remaining approved unmapped settings: {summary['remaining_manual_review']}",
        "",
        "Every decision uses visually reviewed official ASRock, ASUS, GIGABYTE, and MSI pages. Name, function, prerequisites, options, and menu context agree; no community source was required.",
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
            "Known semantic collisions and records lacking equivalent official evidence remain unmapped. The complete decision report and pending backlog are stored in `data/reports/v1.18-exact-platform-controls-wave13.json`.",
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
        "--apply", action="store_true", help="Commit the pinned v1.18 Wave 13 batch"
    )
    args = parser.parse_args()
    with SessionLocal() as session:
        _verify_preflight(session)
        print(
            "Preflight passed: backup, revision, counts, nine manuals, 34 source pins, exclusions, and trust gates match."
        )
        print("Planned outcome: ten new identities and 34 mappings with reviewed menu contexts.")
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
        print("Applied: new_identities=10, mappings=34, menu_contexts=34.")
        print(f"Remaining approved unmapped: {payload['summary']['remaining_manual_review']}.")
        print(f"JSON report: {REPORT_JSON}")
        print(f"Summary report: {REPORT_MARKDOWN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
