# ruff: noqa: E501
"""Apply TuneForge's pinned v1.20 AMD platform-controls Wave 15.

Dry-run is the default. Twenty-three identities and 46 approved Source Settings
were visually verified in official ASRock and ASUS manuals.
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
BACKUP_PATH = ROOT / "data" / "backups" / "tuneforge-v1.20-pre-wave15.zip"
BACKUP_SHA256 = "76a3a4669f5ccb6ef44bbf073104c558c91d030c4921275413da3b297e0ca74a"
REPORT_JSON = ROOT / "data" / "reports" / "v1.20-amd-platform-controls-wave15.json"
REPORT_MARKDOWN = ROOT / "docs" / "v1.20-amd-platform-controls-wave15.md"
ACTOR = "v1.20-amd-platform-controls-official-evidence-reviewer"
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
        8,
        "asus",
        "2e7a4b2d504e9f9286b584fcfbe9cba85e0ccd4a061ad545025160408b308213",
        "https://www.asus.com/us/supportonly/tuf%20gaming%20x870-plus%20wifi/helpdesk_manual/",
        116,
    ),
)


def cross_sources(
    name: str,
    asrock_id: int,
    asrock_page: int,
    asus_id: int,
    asus_page: int,
    section: str,
    evidence: str,
) -> tuple[SourcePin, SourcePin]:
    return (
        SourcePin(
            asrock_id,
            2,
            asrock_page,
            name,
            f"Advanced > AMD CBS > {section} > {name}",
            evidence,
        ),
        SourcePin(
            asus_id,
            8,
            asus_page,
            name,
            f"Advanced > AMD CBS > {section} > {name}",
            evidence,
        ),
    )


def identity(
    canonical_id: str,
    name: str,
    description: str,
    equivalence: str,
    uncertainty: str,
    category_id: str,
    risk_level: str,
    risk_rationale: str,
    asrock_id: int,
    asrock_page: int,
    asus_id: int,
    asus_page: int,
    section: str,
) -> IdentitySeed:
    rationale = (
        "ASRock and ASUS use the exact normalized name in the same AMD CBS "
        f"control family; official descriptions and options agree that {equivalence}."
    )
    evidence = f"The official ASRock and ASUS manuals both document that {equivalence}."
    return IdentitySeed(
        canonical_id,
        name,
        description,
        rationale,
        uncertainty,
        category_id,
        risk_level,
        risk_rationale,
        cross_sources(
            name,
            asrock_id,
            asrock_page,
            asus_id,
            asus_page,
            section,
            evidence,
        ),
    )


MEMORY_RISK = "Changing low-level memory addressing, signaling, training, or test controls can cause instability, failed training, data errors, or require firmware recovery."
IDENTITIES = (
    identity(
        "core-watchdog-timer-enable",
        "Core Watchdog Timer Enable",
        "Enables or disables the processor core watchdog timer.",
        "the option enables or disables the CPU watchdog timer",
        "The timer's platform-specific reset and reporting behavior is not stated.",
        "cpu-performance",
        "moderate",
        "Changing the CPU watchdog can alter how processor hangs are detected and recovered.",
        353,
        54,
        6177,
        58,
        "CPU Common Options",
    ),
    identity(
        "core-watchdog-timer-interval",
        "Core Watchdog Timer Interval",
        "Selects the processor core watchdog-timer interval when the watchdog is enabled.",
        "the option selects the CPU watchdog interval and depends on the watchdog being enabled",
        "The ASRock page does not enumerate the interval values.",
        "cpu-performance",
        "moderate",
        "Changing the watchdog interval can change hang-detection and recovery timing.",
        354,
        54,
        6178,
        58,
        "CPU Common Options",
    ),
    identity(
        "core-watchdog-timer-severity",
        "Core Watchdog Timer Severity",
        "Selects the error severity reported by the processor core watchdog timer.",
        "the option selects CPU watchdog error severity when the watchdog is enabled",
        "The reviewed option lists differ by one platform-specific severity value.",
        "cpu-performance",
        "high",
        "Changing watchdog severity can alter machine-check reporting and failure handling.",
        355,
        54,
        6179,
        58,
        "CPU Common Options",
    ),
    identity(
        "memory-interleaving",
        "Memory interleaving",
        "Enables or disables memory-channel interleaving.",
        "the option controls memory-channel interleaving",
        "Exact address distribution depends on the populated memory topology.",
        "memory",
        "high",
        MEMORY_RISK,
        359,
        54,
        6210,
        60,
        "DF Common Options > Memory Addressing",
    ),
    identity(
        "memory-interleaving-size",
        "Memory interleaving size",
        "Selects the address granularity used for memory interleaving.",
        "the option selects the memory-interleaving size and starting address bit",
        "ASRock omits 512 bytes in prose while ASUS lists it as a supported option.",
        "memory",
        "high",
        MEMORY_RISK,
        360,
        54,
        6211,
        60,
        "DF Common Options > Memory Addressing",
    ),
    identity(
        "dram-map-inversion",
        "DRAM map inversion",
        "Inverts the DRAM map so higher memory channels receive lower system addresses.",
        "enabled map inversion assigns the highest memory channels the lowest addresses",
        "The effect depends on installed channels and system topology.",
        "memory",
        "high",
        MEMORY_RISK,
        361,
        55,
        6212,
        61,
        "DF Common Options > Memory Addressing",
    ),
    identity(
        "acpi-srat-l3-cache-as-numa-domain",
        "ACPI SRAT L3 Cache as NUMA Domain",
        "Controls whether each processor CCX is declared as a separate ACPI NUMA domain.",
        "enabled declares each CCX as a separate NUMA domain and disabled declares nodes per socket",
        "Operating-system scheduling effects are outside the reviewed manual text.",
        "memory",
        "moderate",
        "Changing NUMA exposure can affect operating-system memory placement and workload performance.",
        364,
        55,
        6215,
        61,
        "DF Common Options > ACPI",
    ),
    identity(
        "disable-df-sync-flood-propagation",
        "Disable DF sync flood propagation",
        "Controls propagation of Data Fabric sync-flood errors to other components and ports.",
        "the option disables or enables Data Fabric sync-flood propagation",
        "Downstream error-handling effects depend on the platform.",
        "memory",
        "high",
        "Changing error propagation can alter whether hardware faults trigger downstream recovery or reset behavior.",
        365,
        55,
        6217,
        61,
        "DF Common Options",
    ),
    identity(
        "psp-error-injection-support",
        "PSP error injection support",
        "Enables or disables Platform Security Processor error-injection support.",
        "the true or false option controls PSP error injection",
        "The injected error types and consumers are not documented on these pages.",
        "security",
        "high",
        "Error injection is a diagnostic function that can intentionally exercise platform fault paths.",
        368,
        55,
        6220,
        61,
        "DF Common Options",
    ),
    identity(
        "processor-ck-drive-strengths",
        "Processor CK drive strengths",
        "Selects processor memory-clock signal drive strength.",
        "the option selects Processor CK drive strength in ohms",
        "One ASRock extraction token is malformed; the common impedance choices agree.",
        "memory",
        "high",
        MEMORY_RISK,
        373,
        56,
        6279,
        67,
        "UMC Common Options > DDR Bus Configuration",
    ),
    identity(
        "bankswapmode",
        "BankSwapMode",
        "Selects the DRAM bank-swap mode.",
        "the option configures DRAM bank swapping with Auto, Disabled, and APU-swap choices",
        "ASUS additionally exposes a CPU-swap choice.",
        "memory",
        "high",
        MEMORY_RISK,
        430,
        60,
        6316,
        69,
        "UMC Common Options > DDR Options > DDR Addressing Options",
    ),
    identity(
        "dram-pda-enumerate-id-programming-mode",
        "DRAM PDA Enumerate ID Programming Mode",
        "Selects sequential or legacy DRAM per-device-addressability enumeration.",
        "the option selects sequential or legacy PDA enumeration mode",
        "The manuals do not explain device-specific implementation differences.",
        "memory",
        "high",
        MEMORY_RISK,
        433,
        60,
        6319,
        69,
        "UMC Common Options > DDR Options > DDR Training Options",
    ),
    identity(
        "rx-burst-length",
        "RX Burst Length",
        "Selects the extended burst length used for memory read training.",
        "the option selects 1x, 2x, 4x, or 8x extended read-training sequences",
        "Automatic selection remains firmware-dependent.",
        "memory",
        "high",
        MEMORY_RISK,
        437,
        61,
        6323,
        70,
        "UMC Common Options > DDR Options > DDR Training Options",
    ),
    identity(
        "mbist-enable",
        "MBIST Enable",
        "Enables or disables the DRAM memory built-in self-test.",
        "the option enables or disables Memory MBIST",
        "Test coverage and runtime are platform-dependent.",
        "memory",
        "high",
        MEMORY_RISK,
        450,
        62,
        6337,
        71,
        "UMC Common Options > DDR Options > DDR Memory MBIST",
    ),
    identity(
        "mbist-per-bit-slave-die-reporting",
        "MBIST Per Bit Slave Die Reporting",
        "Reports per-bit two-dimensional data-eye results in the ABL log for each DQ, chip select, and channel.",
        "enabled reports 2D data-eye results per DQ, chip select, and channel",
        "The manuals do not define the ABL log retrieval workflow.",
        "memory",
        "high",
        MEMORY_RISK,
        453,
        62,
        6340,
        71,
        "UMC Common Options > DDR Options > DDR Memory MBIST",
    ),
    identity(
        "pattern-select",
        "Pattern Select",
        "Selects PRBS, SSO, or both patterns for the memory test.",
        "the option selects PRBS, SSO, or both memory-test patterns",
        "ASUS additionally exposes Auto.",
        "memory",
        "high",
        MEMORY_RISK,
        455,
        63,
        6342,
        71,
        "UMC Common Options > DDR Options > DDR Memory MBIST",
    ),
    identity(
        "aggressor-channel",
        "Aggressor Channel",
        "Selects how many aggressor channels are read during the memory test.",
        "the option selects one, three, or seven aggressor channels",
        "ASUS additionally exposes Auto.",
        "memory",
        "high",
        MEMORY_RISK,
        457,
        63,
        6345,
        72,
        "UMC Common Options > DDR Options > DDR Memory MBIST",
    ),
    identity(
        "read-drift-adjustment",
        "Read Drift Adjustment",
        "Selects the memory read-drift adjustment relative to zero.",
        "Auto corresponds to read-drift adjustment zero with positive and negative offsets available",
        "The electrical unit represented by each step is not stated.",
        "memory",
        "high",
        MEMORY_RISK,
        462,
        63,
        6350,
        72,
        "UMC Common Options > DDR Options > DDR Memory Features",
    ),
    identity(
        "write-drift-adjustment",
        "Write Drift Adjustment",
        "Selects the memory write-drift adjustment relative to zero.",
        "Auto corresponds to write-drift adjustment zero with positive and negative offsets available",
        "The electrical unit represented by each step is not stated.",
        "memory",
        "high",
        MEMORY_RISK,
        464,
        63,
        6352,
        72,
        "UMC Common Options > DDR Options > DDR Memory Features",
    ),
    identity(
        "uma-version",
        "UMA Version",
        "Selects the UMA implementation version, including legacy, non-legacy, or hybrid-secure behavior.",
        "the option selects the UMA implementation version",
        "Availability depends on processor graphics support.",
        "peripheral-io",
        "moderate",
        "Changing UMA implementation can affect integrated-graphics memory initialization and compatibility.",
        474,
        64,
        6360,
        73,
        "NBIO Common Options > GFX Configuration",
    ),
    identity(
        "nb-azalia",
        "NB Azalia",
        "Enables or disables the integrated HD Audio controller.",
        "the option enables or disables the integrated HD Audio controller",
        "Audio routing depends on motherboard implementation.",
        "peripheral-io",
        "moderate",
        "Disabling integrated audio removes firmware and operating-system access to that controller.",
        477,
        64,
        6366,
        73,
        "NBIO Common Options > Audio Configuration",
    ),
    identity(
        "audio-ios",
        "Audio IOs",
        "Selects the integrated audio I/O configuration.",
        "the option configures the platform's Audio I/O arrangement",
        "ASRock does not enumerate the ASUS HDA, SoundWire, and PDM combinations.",
        "peripheral-io",
        "moderate",
        "Changing audio I/O routing can disable or reroute onboard audio interfaces.",
        478,
        64,
        6367,
        73,
        "NBIO Common Options > Audio Configuration",
    ),
    identity(
        "persistence-mode-for-legacy-endpoints",
        "Persistence mode for legacy endpoints",
        "Enables persistent handling for legacy PCIe endpoints that might otherwise not be detected.",
        "the option enables persistence when some legacy PCIe devices are not detected",
        "The affected endpoint classes are not enumerated.",
        "peripheral-io",
        "moderate",
        "Changing endpoint persistence can affect PCIe device discovery and initialization.",
        480,
        64,
        6370,
        73,
        "NBIO Common Options",
    ),
)

KNOWN_AMBIGUOUS_IDS = {572, 6111, 6690, 7392, 7395, 7657, 8320}
KNOWN_COLLISION_IDS = {47, 77, 435, 491, 5137, 5284, 5292, 6158, 6161, 6321, 6378}
KNOWN_REJECTED_DUPLICATE_IDS = {4901}
WAVE15_CONFLICT_IDS = {419, 6306}
BASELINE_COUNTS = {
    CanonicalSetting: 217,
    CanonicalSettingAlias: 253,
    SettingCanonicalMapping: 896,
    CanonicalMappingEvent: 896,
    CanonicalIdentityEvent: 232,
    CanonicalSettingClassification: 217,
    CanonicalClassificationEvent: 395,
    SettingMenuContext: 693,
    SettingMenuContextEvent: 693,
    SettingMenuContextCitation: 767,
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
        raise RuntimeError("The verified pre-v1.20 backup is missing or changed.")
    if inspect_backup(BACKUP_PATH).database_revision != config.EXPECTED_DATABASE_REVISION:
        raise RuntimeError("The pre-v1.20 backup has an unexpected revision.")
    if (
        session.scalar(text("SELECT version_num FROM alembic_version"))
        != config.EXPECTED_DATABASE_REVISION
    ):
        raise RuntimeError("The live schema revision changed.")
    if session.scalar(text("PRAGMA integrity_check")) != "ok":
        raise RuntimeError("SQLite integrity_check failed before Wave 15.")
    if _unmapped_count(session) != 3122:
        raise RuntimeError(f"Expected 3,122 unmapped settings, found {_unmapped_count(session)}.")
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
    if len(IDENTITIES) != 23 or len(sources) != 46 or len({x.setting_id for x in sources}) != 46:
        raise RuntimeError("Wave 15 must contain 23 identities and 46 unique records.")
    excluded = (
        KNOWN_AMBIGUOUS_IDS
        | KNOWN_COLLISION_IDS
        | KNOWN_REJECTED_DUPLICATE_IDS
        | WAVE15_CONFLICT_IDS
    )
    if excluded.intersection({item.setting_id for item in sources}):
        raise RuntimeError("A known excluded record entered Wave 15.")
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
    for setting_id in KNOWN_AMBIGUOUS_IDS | KNOWN_COLLISION_IDS | WAVE15_CONFLICT_IDS:
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
    return f"v1.20 Wave 15 reviewer decision; confidence={CONFIDENCE_BASIS_POINTS / 100:.2f}%. {seed.rationale} Official source: {manual.vendor} manual {manual.id}, page {item.page_number}, {manual.source_url}; reviewed menu path: {item.menu_path}. Remaining uncertainty: {seed.uncertainty}"


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
                note="v1.20 Wave 15 official-manual label, behavior, options, dependencies, and menu hierarchy visually verified.",
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
        CanonicalSetting: 23,
        CanonicalSettingAlias: 23,
        SettingCanonicalMapping: 46,
        CanonicalMappingEvent: 46,
        CanonicalIdentityEvent: 23,
        CanonicalSettingClassification: 23,
        CanonicalClassificationEvent: 46,
        SettingMenuContext: 46,
        SettingMenuContextEvent: 46,
        SettingMenuContextCitation: 46,
    }
    failures = [
        f"{model.__tablename__} append-only delta mismatch"
        for model, delta in deltas.items()
        if _count(session, model) != baseline[model] + delta
    ]
    if _unmapped_count(session) != 3076:
        failures.append(f"expected 3,076 unmapped, found {_unmapped_count(session)}")
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
    excluded = (
        KNOWN_AMBIGUOUS_IDS
        | KNOWN_COLLISION_IDS
        | KNOWN_REJECTED_DUPLICATE_IDS
        | WAVE15_CONFLICT_IDS
    )
    if any(session.get(SettingCanonicalMapping, setting_id) is not None for setting_id in excluded):
        failures.append("a known excluded record was mapped")
    if session.scalar(text("PRAGMA integrity_check")) != "ok":
        failures.append("SQLite integrity_check failed")
    foreign_keys = list(session.execute(text("PRAGMA foreign_key_check")))
    if foreign_keys:
        failures.append(f"foreign-key violations: {foreign_keys!r}")
    if failures:
        raise RuntimeError("Wave 15 verification failed:\n- " + "\n- ".join(failures))


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
            "reason": "Not approved in Wave 15; requires a later evidence disposition.",
        }
        for setting, manual in rows
    ]
    audit = build_public_audit(session)
    if audit["status"] != "pass":
        raise RuntimeError("Public BIOS knowledge audit failed after Wave 15.")
    return {
        "milestone": "v1.20 AMD platform-controls canonicalization Wave 15",
        "generated_at": utc_now_iso(),
        "actor": ACTOR,
        "backup": {"path": str(BACKUP_PATH), "sha256": BACKUP_SHA256},
        "database_revision": config.EXPECTED_DATABASE_REVISION,
        "summary": {
            "new_canonical_settings": 23,
            "total_mappings_created": 46,
            "reviewed_menu_contexts": 46,
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
            {
                "setting_ids": sorted(WAVE15_CONFLICT_IDS),
                "reason": "Disable Memory Error Injection has contradictory official label and behavior wording and remains unmapped.",
            },
        ],
        "remaining_manual_review": remaining,
        "public_knowledge_audit": audit,
    }


def _markdown(payload: dict[str, object]) -> str:
    summary = payload["summary"]
    lines = [
        "# TuneForge v1.20 AMD Platform Controls Wave 15",
        "",
        "## Outcome",
        "",
        f"- New canonical settings: {summary['new_canonical_settings']}",
        f"- New verified mappings: {summary['total_mappings_created']}",
        f"- New reviewed menu contexts: {summary['reviewed_menu_contexts']}",
        f"- Remaining approved unmapped settings: {summary['remaining_manual_review']}",
        "",
        "Every decision uses visually reviewed official ASRock and ASUS pages. Name, function, options, dependencies, and menu context agree; no community source was required.",
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
            "Known semantic collisions and records lacking equivalent official evidence remain unmapped. `Disable Memory Error Injection` is intentionally excluded because its official label and behavior wording conflict. The complete decision report and pending backlog are stored in `data/reports/v1.20-amd-platform-controls-wave15.json`.",
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
        "--apply", action="store_true", help="Commit the pinned v1.20 Wave 15 batch"
    )
    args = parser.parse_args()
    with SessionLocal() as session:
        _verify_preflight(session)
        print(
            "Preflight passed: backup, revision, counts, two manuals, 46 source pins, exclusions, and trust gates match."
        )
        print("Planned outcome: 23 new identities and 46 mappings with reviewed menu contexts.")
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
        print("Applied: new_identities=23, mappings=46, menu_contexts=46.")
        print(f"Remaining approved unmapped: {payload['summary']['remaining_manual_review']}.")
        print(f"JSON report: {REPORT_JSON}")
        print(f"Summary report: {REPORT_MARKDOWN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
