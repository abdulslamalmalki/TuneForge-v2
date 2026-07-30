# ruff: noqa: E501
"""Apply TuneForge's pinned v1.25 Intel tuning-controls Wave 20.

Dry-run is the default. Eight identities and 18 approved Source Settings were
visually verified in official ASUS, GIGABYTE, and MSI Intel manuals.
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
BACKUP_PATH = ROOT / "data" / "backups" / "tuneforge-v1.25-pre-wave20.zip"
BACKUP_SHA256 = "3e3a7a377e2332fa4f434d6e5275c973105e7a69375d705d545351fb7efaebe6"
REPORT_JSON = ROOT / "data" / "reports" / "v1.25-intel-tuning-controls-wave20.json"
REPORT_MARKDOWN = ROOT / "docs" / "v1.25-intel-tuning-controls-wave20.md"
ACTOR = "v1.25-intel-tuning-controls-official-evidence-reviewer"
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
    setting_id: int, manual_id: int, page: int, name: str, menu_path: str, evidence: str
) -> SourcePin:
    return SourcePin(setting_id, manual_id, page, name, menu_path, evidence)


def identity(
    canonical_id: str,
    name: str,
    description: str,
    equivalence: str,
    uncertainty: str,
    category_id: str,
    risk_level: str,
    risk_rationale: str,
    sources: tuple[SourcePin, ...],
) -> IdentitySeed:
    return IdentitySeed(
        canonical_id,
        name,
        description,
        f"Official Intel-platform manuals from multiple vendors use the exact normalized name in consistent BIOS context and independently document that {equivalence}.",
        uncertainty,
        category_id,
        risk_level,
        risk_rationale,
        sources,
    )


CPU_RISK = "Changing CPU ratios, PLL behavior, core availability, or voltage offsets can cause instability, boot failure, excess voltage or temperature, or require firmware recovery."
MEMORY_RISK = "Changing System Agent Geyserville can alter memory-frequency switching, training time, performance, and stability."
THERMAL_RISK = "Changing the TCC activation offset changes the processor thermal-control threshold and can materially affect temperature and throttling behavior."
MSI_CPU = "OC > Advanced CPU Configuration"
ASUS_PARADISE = "Ai Tweaker > Tweaker's Paradise"


IDENTITIES = (
    identity(
        "cpu-core-voltage-offset",
        "CPU Core Voltage Offset",
        "Sets the magnitude of the voltage offset applied to CPU core voltage when offset mode is selected.",
        "the setting adjusts CPU core voltage by an offset value",
        "Offset sign is selected separately; ASUS documents the magnitude range while MSI provides a concise description.",
        "cpu-performance",
        "high",
        CPU_RISK,
        (
            source(
                4981,
                5,
                56,
                "CPU Core Voltage Offset",
                "OC > Voltage Setting > CPU Core Voltage Mode > CPU Core Voltage Offset",
                "MSI sets the offset value for CPU core voltage under CPU Core Voltage Mode.",
            ),
            source(
                7436,
                11,
                42,
                "CPU Core Voltage Offset",
                "Ai Tweaker > CPU Core/Cache Voltage > CPU Core Voltage Offset",
                "ASUS exposes the CPU core offset magnitude after Offset Mode Sign and documents 5 mV steps.",
            ),
        ),
    ),
    identity(
        "fll-oc-mode",
        "FLL OC Mode",
        "Selects the processor frequency-locked-loop overclocking mode.",
        "the setting selects FLL overclocking mode",
        "ASUS enumerates Auto, Disabled, Normal, Elevated, and Extreme Elevated; GIGABYTE provides a concise FLL-mode description.",
        "cpu-performance",
        "high",
        CPU_RISK,
        (
            source(
                5542,
                7,
                11,
                "FLL OC Mode",
                "Tweaker > Advanced CPU Settings > FLL OC Mode",
                "GIGABYTE selects the processor FLL mode.",
            ),
            source(
                7407,
                11,
                39,
                "FLL OC mode",
                f"{ASUS_PARADISE} > FLL OC mode",
                "ASUS Intel 600-series firmware exposes five FLL OC mode choices.",
            ),
            source(
                8058,
                12,
                41,
                "FLL OC mode",
                f"{ASUS_PARADISE} > FLL OC mode",
                "ASUS Intel 700-series firmware exposes the same five FLL OC mode choices.",
            ),
        ),
    ),
    identity(
        "per-e-core-control",
        "Per E-Core Control",
        "Enables individual enable/disable control for processor efficiency cores.",
        "the setting enables control of individual E-cores",
        "The ASUS manual's dependency note mistakenly references Per P-Core Control, but its label, enabled/disabled options, and E-Core 0-7 children agree with MSI.",
        "cpu-performance",
        "moderate",
        CPU_RISK,
        (
            source(
                4765,
                5,
                40,
                "Per E-Core Control",
                f"{MSI_CPU} > Per E-Core Control",
                "MSI enables or disables individual E-core control and lists per-core children.",
            ),
            source(
                8148,
                12,
                54,
                "Per E-Core Control",
                "Advanced > CPU Configuration > Per E-Core Control",
                "ASUS exposes Enabled/Disabled control followed by E-Core 0-7 children.",
            ),
        ),
    ),
    identity(
        "per-p-core-control",
        "Per P-Core Control",
        "Enables individual enable/disable control for processor performance cores.",
        "the setting enables control of individual P-cores",
        "ASUS lists grouped P-Core 0-5 children while MSI lists the available per-core entries; installed CPU topology determines the count.",
        "cpu-performance",
        "moderate",
        CPU_RISK,
        (
            source(
                4756,
                5,
                40,
                "Per P-Core Control",
                f"{MSI_CPU} > Per P-Core Control",
                "MSI enables or disables individual P-core control and lists per-core children.",
            ),
            source(
                8146,
                12,
                54,
                "Per P-Core Control",
                "Advanced > CPU Configuration > Per P-Core Control",
                "ASUS exposes Enabled/Disabled control followed by P-Core entries.",
            ),
        ),
    ),
    identity(
        "pvd-ratio-threshold",
        "PVD Ratio Threshold",
        "Sets the core-domain PLL ratio threshold used to switch to a lower post divider during BCLK overclocking.",
        "the setting controls the PVD ratio threshold for BCLK overclocking",
        "ASUS documents the default, range, and DCO rationale; MSI provides a concise functional description.",
        "cpu-performance",
        "high",
        CPU_RISK,
        (
            source(
                4806,
                5,
                44,
                "PVD Ratio Threshold",
                f"{MSI_CPU} > PVD Ratio Threshold",
                "MSI sets the PVD ratio threshold for BCLK overclocking.",
            ),
            source(
                8054,
                12,
                41,
                "PVD Ratio Threshold",
                f"{ASUS_PARADISE} > PVD Ratio Threshold",
                "ASUS sets the Core Domain PLL post-divider threshold used for high BCLK.",
            ),
        ),
    ),
    identity(
        "ring-ratio",
        "Ring Ratio",
        "Sets the processor ring or uncore operating ratio, subject to CPU support and limits.",
        "the setting controls the CPU ring/uncore ratio",
        "Both manuals state that the adjustable range depends on the installed CPU.",
        "cpu-performance",
        "high",
        CPU_RISK,
        (
            source(
                4816,
                5,
                44,
                "Ring Ratio",
                f"{MSI_CPU} > Ring Ratio",
                "MSI sets the ring ratio and states that its valid range depends on the installed CPU.",
            ),
            source(
                6914,
                10,
                9,
                "Ring Ratio",
                "Tweaker > Ring Ratio",
                "GIGABYTE sets the CPU Uncore ratio and states that its range depends on CPU support.",
            ),
        ),
    ),
    identity(
        "sa-gv",
        "SA GV",
        "Controls System Agent Geyserville memory-frequency switching or fixes operation to a selected frequency point.",
        "the setting controls System Agent Geyserville memory-frequency switching",
        "ASUS exposes fixed frequency points in addition to Disabled and Enabled; MSI documents the dynamic-frequency behavior concisely.",
        "memory",
        "high",
        MEMORY_RISK,
        (
            source(
                4841,
                5,
                47,
                "SA GV",
                "OC > Advanced DRAM Configuration > SA GV",
                "MSI enables or disables System Agent Geyserville dynamic memory-frequency tuning.",
            ),
            source(
                7332,
                11,
                31,
                "SA GV",
                "Ai Tweaker > Configure Memory Dynamic Frequency Switching > SA GV",
                "ASUS Intel 600-series firmware disables, enables, or fixes System Agent Geyserville to one of four points.",
            ),
            source(
                7994,
                12,
                34,
                "SA GV",
                "Ai Tweaker > Configure Memory Dynamic Frequency Switching > SA GV",
                "ASUS Intel 700-series firmware exposes the same System Agent Geyserville modes.",
            ),
        ),
    ),
    identity(
        "tcc-activation-offset",
        "TCC Activation Offset",
        "Sets the offset below the factory TCC activation temperature at which processor thermal control begins.",
        "the setting adjusts the Thermal Control Circuit activation offset",
        "ASUS documents a 1-63 range and activation formula; GIGABYTE provides the protective-threshold behavior.",
        "power-management",
        "high",
        THERMAL_RISK,
        (
            source(
                5529,
                7,
                10,
                "Tcc Activation Offset",
                "Tweaker > Advanced CPU Settings > Tcc Activation Offset",
                "GIGABYTE sets the TCC activation offset that determines when processor temperature regulation begins.",
            ),
            source(
                7368,
                11,
                35,
                "TCC Activation Offset",
                "Ai Tweaker > Internal CPU Power Management > TCC Activation Offset",
                "ASUS subtracts the offset from the factory TCC activation temperature and documents a 1-63 range.",
            ),
        ),
    ),
)

KNOWN_AMBIGUOUS_IDS = {572, 6111, 6690, 7392, 7395, 7657, 8320}
KNOWN_COLLISION_IDS = {47, 77, 435, 491, 5137, 5284, 5292, 6158, 6161, 6321, 6378}
KNOWN_REJECTED_DUPLICATE_IDS = {4901}
KNOWN_SEMANTIC_CONFLICT_IDS = {419, 6306}
WAVE18_EVIDENCE_GAP_IDS = {283, 6580}
WAVE19_SEMANTIC_GAP_IDS = {1134, 5657}
BASELINE_COUNTS = {
    CanonicalSetting: 293,
    CanonicalSettingAlias: 329,
    SettingCanonicalMapping: 1059,
    CanonicalMappingEvent: 1059,
    CanonicalIdentityEvent: 308,
    CanonicalSettingClassification: 293,
    CanonicalClassificationEvent: 547,
    SettingMenuContext: 856,
    SettingMenuContextEvent: 856,
    SettingMenuContextCitation: 930,
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


def _excluded() -> set[int]:
    return (
        KNOWN_AMBIGUOUS_IDS
        | KNOWN_COLLISION_IDS
        | KNOWN_REJECTED_DUPLICATE_IDS
        | KNOWN_SEMANTIC_CONFLICT_IDS
        | WAVE18_EVIDENCE_GAP_IDS
        | WAVE19_SEMANTIC_GAP_IDS
    )


def _verify_preflight(session) -> None:
    if not BACKUP_PATH.is_file() or _sha256(BACKUP_PATH) != BACKUP_SHA256:
        raise RuntimeError("The verified pre-v1.25 backup is missing or changed.")
    if inspect_backup(BACKUP_PATH).database_revision != config.EXPECTED_DATABASE_REVISION:
        raise RuntimeError("The pre-v1.25 backup has an unexpected revision.")
    if (
        session.scalar(text("SELECT version_num FROM alembic_version"))
        != config.EXPECTED_DATABASE_REVISION
    ):
        raise RuntimeError("The live schema revision changed.")
    if session.scalar(text("PRAGMA integrity_check")) != "ok":
        raise RuntimeError("SQLite integrity_check failed before Wave 20.")
    if _unmapped_count(session) != 2959:
        raise RuntimeError(f"Expected 2,959 unmapped settings, found {_unmapped_count(session)}.")
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
    if len(IDENTITIES) != 8 or len(sources) != 18 or len({x.setting_id for x in sources}) != 18:
        raise RuntimeError("Wave 20 must contain eight identities and 18 unique records.")
    if _excluded().intersection({item.setting_id for item in sources}):
        raise RuntimeError("A known excluded record entered Wave 20.")
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
    for setting_id in _excluded() - KNOWN_REJECTED_DUPLICATE_IDS:
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
    return f"v1.25 Wave 20 reviewer decision; confidence={CONFIDENCE_BASIS_POINTS / 100:.2f}%. {seed.rationale} Official source: {manual.vendor} manual {manual.id}, page {item.page_number}, {manual.source_url}; reviewed menu path: {item.menu_path}. Remaining uncertainty: {seed.uncertainty}"


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
                note="v1.25 Wave 20 official-manual name, function, options, dependencies, platform, and menu hierarchy visually verified.",
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
        SettingCanonicalMapping: 18,
        CanonicalMappingEvent: 18,
        CanonicalIdentityEvent: 8,
        CanonicalSettingClassification: 8,
        CanonicalClassificationEvent: 16,
        SettingMenuContext: 18,
        SettingMenuContextEvent: 18,
        SettingMenuContextCitation: 18,
    }
    failures = [
        f"{model.__tablename__} append-only delta mismatch"
        for model, delta in deltas.items()
        if _count(session, model) != baseline[model] + delta
    ]
    if _unmapped_count(session) != 2941:
        failures.append(f"expected 2,941 unmapped, found {_unmapped_count(session)}")
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
        raise RuntimeError("Wave 20 verification failed:\n- " + "\n- ".join(failures))


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
            "reason": "Not approved in Wave 20; requires a later evidence disposition.",
        }
        for setting, manual in rows
    ]
    audit = build_public_audit(session)
    if audit["status"] != "pass":
        raise RuntimeError("Public BIOS knowledge audit failed after Wave 20.")
    return {
        "milestone": "v1.25 Intel tuning controls canonicalization Wave 20",
        "generated_at": utc_now_iso(),
        "actor": ACTOR,
        "backup": {"path": str(BACKUP_PATH), "sha256": BACKUP_SHA256},
        "database_revision": config.EXPECTED_DATABASE_REVISION,
        "summary": {
            "new_canonical_settings": 8,
            "total_mappings_created": 18,
            "reviewed_menu_contexts": 18,
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
                "setting_ids": sorted(KNOWN_SEMANTIC_CONFLICT_IDS),
                "reason": "Disable Memory Error Injection has contradictory official label and behavior wording.",
            },
            {
                "setting_ids": sorted(WAVE18_EVIDENCE_GAP_IDS),
                "reason": "VDD Misc Voltage lacks independently consistent official rail evidence.",
            },
            {
                "setting_ids": sorted(WAVE19_SEMANTIC_GAP_IDS),
                "reason": "USB4 CM Mode has conflicting official behavior descriptions.",
            },
        ],
        "remaining_manual_review": remaining,
        "public_knowledge_audit": audit,
    }


def _markdown(payload: dict[str, object]) -> str:
    summary = payload["summary"]
    lines = [
        "# TuneForge v1.25 Intel Tuning Controls Wave 20",
        "",
        "## Outcome",
        "",
        f"- New canonical settings: {summary['new_canonical_settings']}",
        f"- New verified mappings: {summary['total_mappings_created']}",
        f"- New reviewed menu contexts: {summary['reviewed_menu_contexts']}",
        f"- Remaining approved unmapped settings: {summary['remaining_manual_review']}",
        "",
        "Every decision uses visually reviewed official ASUS, GIGABYTE, and MSI Intel-platform pages. Name, function, platform context, options, dependencies, and menu hierarchy agree; no community source was required.",
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
            "Known collisions and evidence gaps remain unmapped. The ASUS `Per E-Core Control` dependency-note typo is preserved as uncertainty and does not affect the independently verified control identity. The complete decision report and pending backlog are stored in `data/reports/v1.25-intel-tuning-controls-wave20.json`.",
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
        "--apply", action="store_true", help="Commit the pinned v1.25 Wave 20 batch"
    )
    args = parser.parse_args()
    with SessionLocal() as session:
        _verify_preflight(session)
        print(
            "Preflight passed: backup, revision, counts, five manuals, 18 source pins, exclusions, and trust gates match."
        )
        print("Planned outcome: eight new identities and 18 mappings with reviewed menu contexts.")
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
        print("Applied: new_identities=8, mappings=18, menu_contexts=18.")
        print(f"Remaining approved unmapped: {payload['summary']['remaining_manual_review']}.")
        print(f"JSON report: {REPORT_JSON}")
        print(f"Summary report: {REPORT_MARKDOWN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
