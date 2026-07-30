# ruff: noqa: E501
"""Apply TuneForge's pinned v1.29 platform, power, and memory Wave 24.

Dry-run is the default. Ten new identities and two extensions to existing
identities were visually verified in eight official vendor manuals.
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
BACKUP_PATH = ROOT / "data" / "backups" / "tuneforge-v1.29-pre-wave24.zip"
BACKUP_SHA256 = "47db66807e86651a6fabcfbfa4ae0b30c1de122c1704bae5035976ad905e96d1"
REPORT_JSON = ROOT / "data" / "reports" / "v1.29-platform-power-wave24.json"
REPORT_MARKDOWN = ROOT / "docs" / "v1.29-platform-power-wave24.md"
ACTOR = "v1.29-platform-power-official-evidence-reviewer"
RELEASE_VERSION = "1.29.0"
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
    create_identity: bool = True


def source(
    setting_id: int,
    manual_id: int,
    page_number: int,
    expected_name: str,
    menu_path: str,
    evidence_excerpt: str,
) -> SourcePin:
    return SourcePin(
        setting_id,
        manual_id,
        page_number,
        expected_name,
        menu_path,
        evidence_excerpt,
    )


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

MEMORY_SWITCHING_RISK = "Changing DRAM PMIC switching frequency can affect regulator behavior, memory stability, power, thermals, training, and boot reliability."

IDENTITIES = (
    IdentitySeed(
        "df-cstates",
        "DF Cstates",
        "Controls whether firmware enables Data Fabric C-state power management.",
        "Official ASRock and ASUS AMD manuals describe the same DF C-state firmware programming, Auto synchronization with Global C-state, and enable/disable choices.",
        "The ASRock heading visibly says 'DR Cstates', while its body repeatedly says DF Cstate; the source label is preserved as a documented vendor-manual typo.",
        "power-management",
        "moderate",
        "Changing Data Fabric C-state behavior can affect idle power, wake latency, and platform stability.",
        (
            source(
                367,
                2,
                55,
                "DR Cstates",
                "Advanced > AMD CBS > DF Common Options > DR Cstates",
                "The rendered ASRock page visibly labels DR Cstates but defines DF Cstate firmware programming and Global C State synchronization.",
            ),
            source(
                6219,
                8,
                61,
                "DF Cstates",
                "Advanced > AMD CBS > DF Common Options > DF Cstates",
                "The rendered ASUS page defines the same DF Cstate firmware programming, Global C State synchronization, and option set.",
            ),
        ),
    ),
    IdentitySeed(
        "l1-burst-prefetch-mode",
        "L1 Burst Prefetch Mode",
        "Enables, disables, or automatically configures the processor L1 burst prefetch mode.",
        "Official ASRock and ASUS AMD manuals use L1 Burst Prefetcher or L1 Burst Prefetch Mode and independently describe the same L1 burst-prefetch control with matching Auto, Disabled, and Enabled choices.",
        "Only the presentation noun differs between the two manuals.",
        "cpu-performance",
        "moderate",
        "Changing L1 burst prefetch behavior can improve or reduce workload performance and memory traffic.",
        (
            source(
                350,
                2,
                54,
                "L1 Burst Prefetcher",
                "Advanced > AMD CBS > CPU Common Options > Prefetcher Settings > L1 Burst Prefetcher",
                "ASRock states that this item enables or disables L1 Burst Prefetch Mode and lists Auto, Disabled, and Enabled.",
            ),
            source(
                6174,
                8,
                57,
                "L1 Burst Prefetch Mode",
                "Advanced > AMD CBS > CPU Common Options > Prefetcher settings > L1 Burst Prefetch Mode",
                "ASUS states that this item enables or disables L1 Burst Prefetch Mode and lists Disable, Enable, and Auto.",
            ),
        ),
    ),
    IdentitySeed(
        "restore-ac-power-loss",
        "Restore AC Power Loss",
        "Selects the system power state after AC power is restored following a power failure.",
        "Official ASRock, ASUS, and MSI manuals independently document the same post-power-loss state control; wording varies between Restore on, Restore after, and Restore AC Power Loss.",
        "ASRock's cited AM5 guide exposes Power Off and Power On, while the ASUS and MSI guides also expose Last State; the core function is identical but available choices are board-dependent.",
        "power-management",
        "moderate",
        "Changing post-loss power restoration can cause unattended startup or leave a system unavailable after an outage.",
        (
            source(
                322,
                2,
                48,
                "Restore on AC/Power Loss",
                "Advanced > ACPI Configuration > Restore on AC/Power Loss",
                "ASRock selects whether the system remains off or boots when AC power recovers.",
            ),
            source(
                1128,
                3,
                64,
                "Restore AC Power Loss",
                "Advanced > APM Configuration > Restore AC Power Loss",
                "ASUS selects Power Off, Power On, or Last State when power is re-applied.",
            ),
            source(
                5152,
                6,
                15,
                "Restore after AC Power Loss",
                "Settings > Advanced > Power Management Setup > Restore after AC Power Loss",
                "MSI defines Power Off, Power On, and Last State behavior after AC restoration.",
            ),
            source(
                6128,
                8,
                53,
                "Restore AC Power Loss",
                "Advanced > APM Configuration > Restore AC Power Loss",
                "ASUS selects the AC power state after a power failure with Power Off, Power On, and Last State.",
            ),
            source(
                7578,
                11,
                63,
                "Restore AC Power Loss",
                "Advanced > APM Configuration > Restore AC Power Loss",
                "ASUS selects ON, OFF, or the previous state after AC power loss.",
            ),
            source(
                8235,
                12,
                68,
                "Restore AC Power Loss",
                "Advanced > APM Configuration > Restore AC Power Loss",
                "ASUS selects Power Off, Power On, or Last State after power is re-applied.",
            ),
        ),
    ),
    IdentitySeed(
        "chipset-link-speed",
        "Chipset Link Speed",
        "Selects the PCIe generation used by the link between the CPU and chipset.",
        "Official ASRock and ASUS AMD manuals independently expose Auto and Gen1 through Gen4 for the CPU-to-chipset link; ASUS calls the control Chipset Link Mode.",
        "The manuals do not promise every generation on every motherboard; platform support remains board-dependent.",
        "pcie-resource",
        "moderate",
        "Forcing an unsupported chipset-link generation can reduce performance, cause link instability, or prevent reliable boot and device enumeration.",
        (
            source(
                531,
                2,
                70,
                "Chipset Link Speed",
                "Advanced > AMD PBS > AMD Common Platform Module > Chipset Link Speed",
                "ASRock configures Chipset Link Speed with Auto and Gen1 through Gen4.",
            ),
            source(
                6155,
                8,
                56,
                "Chipset Link Mode",
                "Advanced > Onboard Devices Configuration > PCIE Link Speed > Chipset Link Mode",
                "ASUS sets the link speed between CPU and chipset with Auto and Gen1 through Gen4.",
            ),
        ),
    ),
    IdentitySeed(
        "dram-vdd-switching-frequency",
        "DRAM VDD Switching Frequency",
        "Configures the switching frequency of the DRAM VDD regulator.",
        "Official ASUS and MSI Intel manuals independently identify the DRAM VDD PMIC regulator switching-frequency control.",
        "ASUS exposes synchronized and per-PMIC variants; MSI does not document a range on the cited page.",
        "memory",
        "high",
        MEMORY_SWITCHING_RISK,
        (
            source(
                1051,
                3,
                54,
                "DRAM VDD Switching Frequency",
                "Ai Tweaker > DRAM VDD Switching Frequency",
                "ASUS defines the switching frequency of the DRAM VDD regulator in MHz for synchronized PMICs.",
            ),
            source(
                1060,
                3,
                54,
                "PMIC0-1 DRAM VDD Switching Frequency",
                "Ai Tweaker > PMIC0-1 DRAM VDD Switching Frequency",
                "ASUS defines the same DRAM VDD regulator switching frequency for per-PMIC control.",
            ),
            source(
                4849,
                5,
                47,
                "VDD Switching Frequency",
                "OC > Advanced DRAM Configuration > DRAM PMIC Features > VDD Switching Frequency",
                "MSI defines VDD switching frequency under DRAM PMIC Features.",
            ),
        ),
    ),
    IdentitySeed(
        "dram-vddq-switching-frequency",
        "DRAM VDDQ Switching Frequency",
        "Configures the switching frequency of the DRAM VDDQ regulator.",
        "Official ASUS and MSI Intel manuals independently identify the DRAM VDDQ PMIC regulator switching-frequency control.",
        "ASUS exposes synchronized and per-PMIC variants; MSI does not document a range on the cited page.",
        "memory",
        "high",
        MEMORY_SWITCHING_RISK,
        (
            source(
                1052,
                3,
                54,
                "DRAM VDDQ Switching Frequency",
                "Ai Tweaker > DRAM VDDQ Switching Frequency",
                "ASUS defines the switching frequency of the DRAM VDDQ regulator in MHz for synchronized PMICs.",
            ),
            source(
                1061,
                3,
                55,
                "PMIC0-1 DRAM VDDQ Switching Frequency",
                "Ai Tweaker > PMIC0-1 DRAM VDDQ Switching Frequency",
                "ASUS defines the same DRAM VDDQ regulator switching frequency for per-PMIC control.",
            ),
            source(
                4852,
                5,
                47,
                "VDDQ Switching Frequency",
                "OC > Advanced DRAM Configuration > DRAM PMIC Features > VDDQ Switching Frequency",
                "MSI defines VDDQ switching frequency under DRAM PMIC Features.",
            ),
        ),
    ),
    IdentitySeed(
        "dram-vpp-switching-frequency",
        "DRAM VPP Switching Frequency",
        "Configures the switching frequency of the DRAM VPP regulator.",
        "Official ASUS and MSI Intel manuals independently identify the DRAM VPP PMIC regulator switching-frequency control.",
        "ASUS exposes synchronized and per-PMIC variants; MSI does not document a range on the cited page.",
        "memory",
        "high",
        MEMORY_SWITCHING_RISK,
        (
            source(
                1053,
                3,
                54,
                "DRAM VPP Switching Frequency",
                "Ai Tweaker > DRAM VPP Switching Frequency",
                "ASUS defines the switching frequency of the DRAM VPP regulator in MHz for synchronized PMICs.",
            ),
            source(
                1062,
                3,
                55,
                "PMIC0-1 DRAM VPP Switching Frequency",
                "Ai Tweaker > PMIC0-1 DRAM VPP Switching Frequency",
                "ASUS defines the same DRAM VPP regulator switching frequency for per-PMIC control.",
            ),
            source(
                4855,
                5,
                47,
                "VPP Switching Frequency",
                "OC > Advanced DRAM Configuration > DRAM PMIC Features > VPP Switching Frequency",
                "MSI defines VPP switching frequency under DRAM PMIC Features.",
            ),
        ),
    ),
    IdentitySeed(
        "wake-from-thunderbolt-devices",
        "Wake From Thunderbolt(TM) Devices",
        "Enables or disables waking the system from a Thunderbolt device.",
        "Official MSI and ASUS Intel manuals independently document the same system-wake control under Thunderbolt configuration.",
        "The option depends on discrete Thunderbolt support and motherboard hardware availability.",
        "power-management",
        "moderate",
        "Enabling external-device wake can increase standby activity and permit attached Thunderbolt hardware to wake the system.",
        (
            source(
                4566,
                5,
                20,
                "Wake From Thunderbolt(TM) Device",
                "Settings > Advanced > Intel Thunderbolt > Wake From Thunderbolt(TM) Device",
                "MSI enables or disables system wake by a Thunderbolt device.",
            ),
            source(
                7555,
                11,
                59,
                "Wake From Thunderbolt(TM) Devices",
                "Advanced > Thunderbolt(TM) Configuration > Wake From Thunderbolt(TM) Devices",
                "ASUS enables or disables system wake from Thunderbolt devices when discrete support is enabled.",
            ),
            source(
                8206,
                12,
                62,
                "Wake From Thunderbolt(TM) Devices",
                "Advanced > Thunderbolt(TM) Configuration > Wake From Thunderbolt(TM) Devices",
                "ASUS enables or disables system wake from Thunderbolt devices when discrete support is enabled.",
            ),
        ),
    ),
    IdentitySeed(
        "intel-turbo-boost-max-technology-3-0",
        "Intel(R) Turbo Boost Max Technology 3.0",
        "Enables or disables Intel Turbo Boost Max Technology 3.0 support.",
        "Official MSI and ASUS Intel manuals independently expose the same Intel Turbo Boost Max Technology 3.0 enable/disable control and processor-support dependency.",
        "ASUS additionally documents the disabled-state _CPC ratio behavior.",
        "cpu-performance",
        "moderate",
        "Disabling Turbo Boost Max Technology 3.0 can reduce favored-core performance and change operating-system performance-state reporting.",
        (
            source(
                4784,
                5,
                42,
                "Intel Turbo Boost Max Technology 3.0",
                "OC > Advanced CPU Configuration > Intel Turbo Boost Max Technology 3.0",
                "MSI enables or disables Intel Turbo Boost Max 3.0 when a supporting CPU is installed.",
            ),
            source(
                7504,
                11,
                52,
                "Intel(R) Turbo Boost Max Technology 3.0",
                "Advanced > CPU Configuration > CPU - Power Management Control > Intel(R) Turbo Boost Max Technology 3.0",
                "ASUS enables or disables Intel Turbo Boost Max Technology 3.0 and documents disabled-state _CPC behavior.",
            ),
            source(
                8162,
                12,
                55,
                "Intel(R) Turbo Boost Max Technology 3.0",
                "Advanced > CPU Configuration > CPU - Power Management Control > Intel(R) Turbo Boost Max Technology 3.0",
                "ASUS enables or disables Intel Turbo Boost Max Technology 3.0 and documents disabled-state _CPC behavior.",
            ),
        ),
    ),
    IdentitySeed(
        "lclk-frequency-control",
        "LCLK Frequency Control",
        "Selects automatic or manual control of the AMD LCLK I/O frequency.",
        "Official MSI and ASUS AMD manuals independently define Auto/default and Manual LCLK frequency control and gate the same Maximum Frequency child setting.",
        "Supported frequency range is documented on the child Maximum Frequency setting and remains platform-dependent.",
        "peripheral-io",
        "high",
        "Manual LCLK control can affect I/O stability, power, performance, and boot reliability.",
        (
            source(
                5280,
                6,
                28,
                "LCLK Frequency Control (I/O Clock)",
                "OC > LCLK Frequency Control (I/O Clock)",
                "MSI selects Auto or Manual LCLK control and exposes Maximum Frequency only in Manual mode.",
            ),
            source(
                6582,
                8,
                92,
                "LCLK Frequency Control",
                "Advanced > AMD Overclocking > LCLK Frequency Control",
                "ASUS selects default or Manual LCLK control and exposes Maximum Frequency only in Manual mode.",
            ),
        ),
    ),
    IdentitySeed(
        "intel-speed-shift-technology",
        "Intel(R) Speed Shift Technology",
        "",
        "The GIGABYTE Intel 600 source has the same label and verbatim function text as an already mapped GIGABYTE source, while the existing identity is independently supported by ASUS official manuals.",
        "The '(Note)' suffix only marks chipset/CPU availability and is not part of the BIOS function.",
        "",
        "",
        "",
        (
            source(
                6920,
                10,
                10,
                "Intel(R) Speed Shift Technology (Note)",
                "Tweaker > Advanced CPU Settings > Intel(R) Speed Shift Technology (Note)",
                "GIGABYTE enables or disables Intel Speed Shift to improve frequency ramp and responsiveness; the text matches existing official GIGABYTE evidence.",
            ),
        ),
        create_identity=False,
    ),
    IdentitySeed(
        "hd-audio-controller",
        "HD Audio Controller",
        "",
        "The GIGABYTE Intel 600 Audio Controller record has the same onboard-audio enable/disable function and verbatim explanatory text as already mapped GIGABYTE HD Audio Controller records, with independent MSI and ASUS support.",
        "GIGABYTE omits 'HD' from this manual's visible label; the documented onboard-audio function is unchanged.",
        "",
        "",
        "",
        (
            source(
                6992,
                10,
                19,
                "Audio Controller",
                "Settings > IO Ports > Audio Controller",
                "GIGABYTE enables or disables onboard audio and gives the same add-in-card explanation as its mapped HD Audio Controller records.",
            ),
        ),
        create_identity=False,
    ),
)

PRIOR_EXCLUDED_IDS = {
    36,
    47,
    77,
    283,
    419,
    435,
    447,
    491,
    572,
    1033,
    1134,
    4741,
    4795,
    4901,
    5137,
    5284,
    5292,
    5323,
    5342,
    5528,
    5657,
    5779,
    6111,
    6158,
    6161,
    6288,
    6306,
    6321,
    6333,
    6378,
    6442,
    6580,
    6690,
    7392,
    7395,
    7455,
    7657,
    8107,
    8320,
}
WAVE24_UNRESOLVED_SIGNAL_IDS = {374, 6501}
REJECTED_DUPLICATE_IDS = {4901}
BASELINE_COUNTS = {
    CanonicalSetting: 320,
    CanonicalSettingAlias: 356,
    SettingCanonicalMapping: 1129,
    CanonicalMappingEvent: 1129,
    CanonicalIdentityEvent: 335,
    CanonicalSettingClassification: 320,
    CanonicalClassificationEvent: 601,
    SettingMenuContext: 926,
    SettingMenuContextEvent: 926,
    SettingMenuContextCitation: 1000,
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _count(session, model: type) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


def _sources() -> tuple[SourcePin, ...]:
    return tuple(item for seed in IDENTITIES for item in seed.sources)


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


def _excluded() -> set[int]:
    return PRIOR_EXCLUDED_IDS | WAVE24_UNRESOLVED_SIGNAL_IDS


def _verify_preflight(session) -> None:
    if config.APP_VERSION != "1.28.0":
        raise RuntimeError(f"Expected v1.28 baseline, found {config.APP_VERSION}.")
    if not BACKUP_PATH.is_file() or _sha256(BACKUP_PATH) != BACKUP_SHA256:
        raise RuntimeError("The verified pre-v1.29 backup is missing or changed.")
    if inspect_backup(BACKUP_PATH).database_revision != config.EXPECTED_DATABASE_REVISION:
        raise RuntimeError("The pre-v1.29 backup has an unexpected revision.")
    if (
        session.scalar(text("SELECT version_num FROM alembic_version"))
        != config.EXPECTED_DATABASE_REVISION
    ):
        raise RuntimeError("The live schema revision changed.")
    if session.scalar(text("PRAGMA integrity_check")) != "ok":
        raise RuntimeError("SQLite integrity_check failed before Wave 24.")
    if list(session.execute(text("PRAGMA foreign_key_check"))):
        raise RuntimeError("Foreign-key violations exist before Wave 24.")
    if _unmapped_count(session) != 2889:
        raise RuntimeError(f"Expected 2,889 unmapped settings, found {_unmapped_count(session)}.")
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
    sources = _sources()
    if len(IDENTITIES) != 12 or sum(seed.create_identity for seed in IDENTITIES) != 10:
        raise RuntimeError("Wave 24 must contain ten new and two existing identities.")
    if len(sources) != 31 or len({item.setting_id for item in sources}) != 31:
        raise RuntimeError("Wave 24 must contain 31 unique source records.")
    if _excluded().intersection(item.setting_id for item in sources):
        raise RuntimeError("A known excluded record entered Wave 24.")
    for seed in IDENTITIES:
        existing = session.get(CanonicalSetting, seed.canonical_id)
        if seed.create_identity and existing is not None:
            raise RuntimeError(f"Canonical identity already exists: {seed.canonical_id}.")
        if not seed.create_identity:
            detail = library.canonical_detail(session, seed.canonical_id)
            if detail is None or len({item.vendor_id for item in detail.source_settings}) < 2:
                raise RuntimeError(
                    f"Existing identity lacks cross-vendor support: {seed.canonical_id}."
                )
        elif len({pins[item.manual_id].vendor_id for item in seed.sources}) < 2:
            raise RuntimeError(f"New identity lacks cross-vendor evidence: {seed.canonical_id}.")
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
    for setting_id in _excluded() - REJECTED_DUPLICATE_IDS:
        if session.get(SettingCanonicalMapping, setting_id) is not None:
            raise RuntimeError(f"Known excluded source {setting_id} was unexpectedly mapped.")
    for setting_id in REJECTED_DUPLICATE_IDS:
        setting = session.get(Setting, setting_id)
        if (
            setting is None
            or setting.status != "rejected"
            or session.get(SettingCanonicalMapping, setting_id) is not None
        ):
            raise RuntimeError(f"Known rejected duplicate {setting_id} changed state.")


def _mapping_note(session, seed: IdentitySeed, item: SourcePin) -> str:
    manual = session.get(Manual, item.manual_id)
    return f"v1.29 Wave 24 reviewer decision; confidence={CONFIDENCE_BASIS_POINTS / 100:.2f}%. {seed.rationale} Official source: {manual.vendor} manual {manual.id}, page {item.page_number}, {manual.source_url}; reviewed menu path: {item.menu_path}. Remaining uncertainty: {seed.uncertainty}"


def _apply(session) -> None:
    for seed in IDENTITIES:
        if seed.create_identity:
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
                note="v1.29 Wave 24 official-manual name, function, options, dependencies, platform, and menu hierarchy visually verified.",
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
        SettingCanonicalMapping: 31,
        CanonicalMappingEvent: 31,
        CanonicalIdentityEvent: 10,
        CanonicalSettingClassification: 10,
        CanonicalClassificationEvent: 20,
        SettingMenuContext: 31,
        SettingMenuContextEvent: 31,
        SettingMenuContextCitation: 31,
    }
    failures = [
        f"{model.__tablename__} append-only delta mismatch"
        for model, delta in deltas.items()
        if _count(session, model) != baseline[model] + delta
    ]
    if _unmapped_count(session) != 2858:
        failures.append(f"expected 2,858 unmapped, found {_unmapped_count(session)}")
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
    if any(
        session.get(SettingCanonicalMapping, setting_id) is not None for setting_id in _excluded()
    ):
        failures.append("a known excluded record was mapped")
    if session.scalar(text("PRAGMA integrity_check")) != "ok":
        failures.append("SQLite integrity_check failed")
    foreign_keys = list(session.execute(text("PRAGMA foreign_key_check")))
    if foreign_keys:
        failures.append(f"foreign-key violations: {foreign_keys!r}")
    if failures:
        raise RuntimeError("Wave 24 verification failed:\n- " + "\n- ".join(failures))


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
                "decision": "new_identity"
                if seed.create_identity
                else "existing_identity_extension",
                "canonical_id": seed.canonical_id,
                "canonical_name": seed.canonical_name,
                "description": seed.description or None,
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
    conflict_reasons = {
        374: "ASRock exposes separate CK, CC, and CS controls; official evidence does not prove CC is ASUS CKI.",
        6501: "ASUS exposes CKI separately from CS and CA; official evidence does not prove CKI is ASRock CC.",
    }
    remaining = [
        {
            "setting_id": setting.id,
            "vendor_id": manual.vendor_id,
            "manual_id": manual.id,
            "source_name": effective_name(setting),
            "page_number": setting.edited_page_number or setting.page_number,
            "review_status": "pending_evidence_review",
            "reason": conflict_reasons.get(
                setting.id, "Not approved in Wave 24; requires a later evidence disposition."
            ),
        }
        for setting, manual in rows
    ]
    audit = build_public_audit(session)
    if audit["status"] != "pass":
        raise RuntimeError("Public BIOS knowledge audit failed after Wave 24.")
    audit["app_version"] = RELEASE_VERSION
    return {
        "milestone": "v1.29 platform, power, and memory canonicalization Wave 24",
        "generated_at": utc_now_iso(),
        "actor": ACTOR,
        "backup": {"path": str(BACKUP_PATH), "sha256": BACKUP_SHA256},
        "database_revision": config.EXPECTED_DATABASE_REVISION,
        "summary": {
            "new_canonical_settings": 10,
            "existing_canonical_settings_extended": 2,
            "total_mappings_created": 31,
            "reviewed_menu_contexts": 31,
            "remaining_manual_review": len(remaining),
        },
        "official_manuals": [asdict(pin) for pin in MANUALS],
        "approved_decisions": decisions,
        "intentional_exclusions": [
            {
                "setting_ids": sorted(WAVE24_UNRESOLVED_SIGNAL_IDS),
                "reason": "Official signal naming remains unresolved; no CC-to-CKI equivalence was inferred.",
            },
            {
                "setting_ids": sorted(PRIOR_EXCLUDED_IDS),
                "reason": "Previously preserved ambiguity, collision, rejection, contradiction, or evidence gap.",
            },
        ],
        "remaining_manual_review": remaining,
        "public_knowledge_audit": audit,
    }


def _markdown(payload: dict[str, object]) -> str:
    summary = payload["summary"]
    lines = [
        "# TuneForge v1.29 Platform, Power, and Memory Wave 24",
        "",
        "## Outcome",
        "",
        f"- New canonical settings: {summary['new_canonical_settings']}",
        f"- Existing canonical settings extended: {summary['existing_canonical_settings_extended']}",
        f"- New verified mappings: {summary['total_mappings_created']}",
        f"- New reviewed menu contexts: {summary['reviewed_menu_contexts']}",
        f"- Remaining approved unmapped settings: {summary['remaining_manual_review']}",
        "",
        "Every decision uses visually reviewed official ASUS, ASRock, GIGABYTE, and MSI pages. Names, functions, options, dependencies, platform context, and menu hierarchy agree; no community source was required.",
        "",
        "## Approved decisions",
        "",
    ]
    for decision in payload["approved_decisions"]:
        vendors = sorted({item["vendor_id"] for item in decision["sources"]})
        kind = (
            "new identity"
            if decision["decision"] == "new_identity"
            else "existing identity extension"
        )
        mapping_label = "mapping" if len(decision["sources"]) == 1 else "mappings"
        lines.append(
            f"- **{decision['canonical_name']}** ({kind}) - {len(decision['sources'])} {mapping_label} from {', '.join(vendors)}. {decision['rationale']}"
        )
    lines.extend(
        [
            "",
            "## Intentional exclusion",
            "",
            "Processor CC drive strengths and Processor CKI drive strengths remain unmapped. The official pages expose separate CK, CC/CKI, CS, and CA controls, but do not establish that CC and CKI are equivalent. TuneForge does not infer the relationship.",
            "",
            "The complete machine-readable decision report and pending backlog are stored in `data/reports/v1.29-platform-power-wave24.json`.",
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
        "--apply", action="store_true", help="Commit the pinned v1.29 Wave 24 batch"
    )
    args = parser.parse_args()
    with SessionLocal() as session:
        _verify_preflight(session)
        print(
            "Preflight passed: backup, revision, counts, eight manuals, 31 source pins, exclusions, and trust gates match."
        )
        print(
            "Planned outcome: ten new identities, two existing identity extensions, and 31 mappings."
        )
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
        print("Applied: new_identities=10, existing_extensions=2, mappings=31, menu_contexts=31.")
        print(f"Remaining approved unmapped: {payload['summary']['remaining_manual_review']}.")
        print(f"JSON report: {REPORT_JSON}")
        print(f"Summary report: {REPORT_MARKDOWN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
