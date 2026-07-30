# ruff: noqa: E501
"""Apply TuneForge's pinned v1.26 AMD memory/fabric Wave 21.

Dry-run is the default. Seven identities and 15 approved Source Settings were
visually verified in official ASUS, ASRock, GIGABYTE, and MSI AMD manuals.
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
BACKUP_PATH = ROOT / "data" / "backups" / "tuneforge-v1.26-pre-wave21.zip"
BACKUP_SHA256 = "5f3a7ab1493d88a6f4772b54b4f875b70ba655a8bfb40e789e0c1d00a20bd65e"
REPORT_JSON = ROOT / "data" / "reports" / "v1.26-amd-memory-fabric-wave21.json"
REPORT_MARKDOWN = ROOT / "docs" / "v1.26-amd-memory-fabric-wave21.md"
ACTOR = "v1.26-amd-memory-fabric-official-evidence-reviewer"
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
        f"Official AMD-platform manuals from multiple vendors use the same source label in consistent BIOS context and independently document that {equivalence}.",
        uncertainty,
        category_id,
        risk_level,
        risk_rationale,
        sources,
    )


MEMORY_RISK = "Changing memory addressing or timing behavior can affect performance, stability, training, and boot reliability."
FABRIC_RISK = "Changing fabric clock frequency can affect memory performance, stability, voltage requirements, power, and temperature."
GFX_RISK = "Changing the integrated-graphics voltage/frequency curve can cause instability, display failure, excess voltage or temperature, or require firmware recovery."
LCLK_RISK = (
    "Changing LCLK frequency can affect I/O stability, power, performance, and boot reliability."
)
CPU_RISK = "Changing processor cache or P-state controls can affect performance, power management, operating-system behavior, and stability."


IDENTITIES = (
    identity(
        "bank-swap-mode",
        "Bank Swap Mode",
        "Selects the DRAM bank-swap mode, including CPU- or APU-oriented modes where supported.",
        "the setting selects the DRAM bank-swap mode",
        "MSI does not enumerate modes; ASUS documents Auto, Disabled, Swap CPU, and Swap APU.",
        "memory",
        "moderate",
        MEMORY_RISK,
        (
            source(
                5408,
                6,
                37,
                "Bank Swap Mode",
                "OC > Advanced DRAM Configuration > Bank Swap Mode",
                "MSI selects the bank swap mode within Advanced DRAM Configuration.",
            ),
            source(
                5944,
                8,
                29,
                "Bank Swap Mode",
                "Ai Tweaker > DRAM Timing Control > Bank Swap Mode",
                "ASUS exposes Auto, Disabled, Swap CPU, and Swap APU modes within DRAM Timing Control.",
            ),
        ),
    ),
    identity(
        "fclk-frequency",
        "FCLK Frequency",
        "Sets the AMD Infinity Fabric clock frequency.",
        "the setting controls FCLK frequency",
        "ASUS documents an 800-3000 MHz range; MSI warns that overclocking behavior is not guaranteed.",
        "memory",
        "high",
        FABRIC_RISK,
        (
            source(
                5326,
                6,
                32,
                "FCLK Frequency",
                "OC > FCLK Frequency",
                "MSI sets FCLK, described as the internal Data Fabric clock of DRAM.",
            ),
            source(
                5783,
                8,
                18,
                "FCLK Frequency",
                "Ai Tweaker > FCLK Frequency",
                "ASUS specifies FCLK frequency with Auto and 800-3000 MHz options.",
            ),
        ),
    ),
    identity(
        "gear-down-mode",
        "Gear Down Mode",
        "Controls DDR Gear Down mode.",
        "the setting enables, disables, or configures DDR Gear Down mode",
        "The manuals do not explain the electrical timing mechanism or enumerate all model-specific options.",
        "memory",
        "moderate",
        MEMORY_RISK,
        (
            source(
                62,
                1,
                12,
                "Gear Down Mode",
                "Tweaker > Advanced Memory Settings > Gear Down Mode",
                "GIGABYTE enables or disables Gear Down mode in Advanced Memory Settings.",
            ),
            source(
                250,
                2,
                35,
                "Gear Down Mode",
                "OC Tweaker > DRAM Timing Configuration > Gear Down Mode",
                "ASRock configures Gear Down Mode in DRAM Timing Configuration.",
            ),
        ),
    ),
    identity(
        "gfx-curve-optimizer-magnitude",
        "GFX Curve Optimizer Magnitude",
        "Sets the magnitude of the integrated-graphics voltage/frequency curve shift; direction is selected separately.",
        "the setting controls the magnitude of the GFX Curve Optimizer shift",
        "MSI says the value applies to all cores, while ASUS describes the GFX AVFS curve and SMU command; the exact label, GFX parent, and sign dependency agree.",
        "cpu-performance",
        "high",
        GFX_RISK,
        (
            source(
                5271,
                6,
                28,
                "GFX Curve Optimizer Magnitude",
                "OC > Precision Boost Overdrive > GFX Curve Optimizer > GFX Curve Optimizer Magnitude",
                "MSI sets the GFX Curve Optimizer value when its GFX parent control is enabled.",
            ),
            source(
                5975,
                8,
                32,
                "GFX Curve Optimizer Magnitude",
                "Ai Tweaker > Precision Boost Overdrive > GFX Curve Optimizer > GFX Curve Optimizer Magnitude",
                "ASUS sets the magnitude of the GFX AVFS curve shift and combines it with the separate sign for an SMU command.",
            ),
            source(
                6556,
                8,
                89,
                "GFX Curve Optimizer Magnitude",
                "Advanced > AMD Overclocking > Precision Boost Overdrive > GFX Curve Optimizer > GFX Curve Optimizer Magnitude",
                "ASUS AMD Overclocking documents the same GFX curve-shift magnitude and sign dependency.",
            ),
        ),
    ),
    identity(
        "lclk-maximum-frequency",
        "LCLK Maximum Frequency",
        "Sets the maximum LCLK frequency when manual LCLK frequency control is selected.",
        "the generic source label Maximum Frequency controls LCLK under the same explicit parent setting",
        "The leaf label is generic and is equivalent only under the reviewed LCLK Frequency Control parent; it must not be reused outside that context.",
        "peripheral-io",
        "high",
        LCLK_RISK,
        (
            source(
                5281,
                6,
                28,
                "Maximum Frequency",
                "OC > LCLK Frequency Control (I/O Clock) > Maximum Frequency",
                "MSI sets LCLK manually when LCLK Frequency Control (I/O Clock) is Manual.",
            ),
            source(
                6583,
                8,
                92,
                "Maximum Frequency",
                "Advanced > AMD Overclocking > LCLK Frequency Control > Maximum Frequency",
                "ASUS sets maximum LCLK frequency from 1029 through 2500 under manual LCLK Frequency Control.",
            ),
        ),
    ),
    identity(
        "opcache-control",
        "Opcache Control",
        "Enables, disables, or automatically configures the processor Opcache where supported.",
        "the setting controls Opcache enablement",
        "Neither manual describes the internal cache mechanism; ASUS additionally exposes Auto.",
        "cpu-performance",
        "moderate",
        CPU_RISK,
        (
            source(
                5285,
                6,
                29,
                "Opcache Control",
                "OC > AMD CBS > Opcache Control",
                "MSI enables or disables Opcache Control within AMD CBS.",
            ),
            source(
                6183,
                8,
                58,
                "Opcache Control",
                "Advanced > AMD CBS > Opcache Control",
                "ASUS enables or disables Opcache and also exposes Auto within AMD CBS.",
            ),
        ),
    ),
    identity(
        "ppc-adjustment",
        "PPC Adjustment",
        "Selects or fixes the processor P-state mode when ACPI processor performance-state support is enabled.",
        "the setting controls the CPU P-state mode",
        "GIGABYTE says the control fixes CPU PState; MSI says it sets Pstate mode and documents the PSS Support dependency.",
        "power-management",
        "moderate",
        CPU_RISK,
        (
            source(
                46,
                1,
                10,
                "PPC Adjustment",
                "Tweaker > Advanced CPU Settings > PPC Adjustment",
                "GIGABYTE allows the reviewer to fix the CPU PState under Advanced CPU Settings.",
            ),
            source(
                5306,
                6,
                30,
                "PPC Adjustment",
                "OC > AMD CBS > PPC Adjustment",
                "MSI sets Pstate mode and exposes the setting when PSS Support is enabled.",
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
WAVE21_EXPO_SCOPE_CONFLICT_IDS = {5323, 5779, 6442}
BASELINE_COUNTS = {
    CanonicalSetting: 301,
    CanonicalSettingAlias: 337,
    SettingCanonicalMapping: 1077,
    CanonicalMappingEvent: 1077,
    CanonicalIdentityEvent: 316,
    CanonicalSettingClassification: 301,
    CanonicalClassificationEvent: 563,
    SettingMenuContext: 874,
    SettingMenuContextEvent: 874,
    SettingMenuContextCitation: 948,
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
        | WAVE21_EXPO_SCOPE_CONFLICT_IDS
    )


def _verify_preflight(session) -> None:
    if not BACKUP_PATH.is_file() or _sha256(BACKUP_PATH) != BACKUP_SHA256:
        raise RuntimeError("The verified pre-v1.26 backup is missing or changed.")
    if inspect_backup(BACKUP_PATH).database_revision != config.EXPECTED_DATABASE_REVISION:
        raise RuntimeError("The pre-v1.26 backup has an unexpected revision.")
    if (
        session.scalar(text("SELECT version_num FROM alembic_version"))
        != config.EXPECTED_DATABASE_REVISION
    ):
        raise RuntimeError("The live schema revision changed.")
    if session.scalar(text("PRAGMA integrity_check")) != "ok":
        raise RuntimeError("SQLite integrity_check failed before Wave 21.")
    if _unmapped_count(session) != 2941:
        raise RuntimeError(f"Expected 2,941 unmapped settings, found {_unmapped_count(session)}.")
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
    if len(IDENTITIES) != 7 or len(sources) != 15 or len({x.setting_id for x in sources}) != 15:
        raise RuntimeError("Wave 21 must contain seven identities and 15 unique records.")
    if _excluded().intersection({item.setting_id for item in sources}):
        raise RuntimeError("A known excluded record entered Wave 21.")
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
    return f"v1.26 Wave 21 reviewer decision; confidence={CONFIDENCE_BASIS_POINTS / 100:.2f}%. {seed.rationale} Official source: {manual.vendor} manual {manual.id}, page {item.page_number}, {manual.source_url}; reviewed menu path: {item.menu_path}. Remaining uncertainty: {seed.uncertainty}"


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
                note="v1.26 Wave 21 official-manual name, function, options, dependencies, platform, and menu hierarchy visually verified.",
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
        CanonicalSetting: 7,
        CanonicalSettingAlias: 7,
        SettingCanonicalMapping: 15,
        CanonicalMappingEvent: 15,
        CanonicalIdentityEvent: 7,
        CanonicalSettingClassification: 7,
        CanonicalClassificationEvent: 14,
        SettingMenuContext: 15,
        SettingMenuContextEvent: 15,
        SettingMenuContextCitation: 15,
    }
    failures = [
        f"{model.__tablename__} append-only delta mismatch"
        for model, delta in deltas.items()
        if _count(session, model) != baseline[model] + delta
    ]
    if _unmapped_count(session) != 2926:
        failures.append(f"expected 2,926 unmapped, found {_unmapped_count(session)}")
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
        raise RuntimeError("Wave 21 verification failed:\n- " + "\n- ".join(failures))


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
            "reason": (
                "Same label has conflicting EXPO scopes and remains unmapped."
                if setting.id in WAVE21_EXPO_SCOPE_CONFLICT_IDS
                else "Not approved in Wave 21; requires a later evidence disposition."
            ),
        }
        for setting, manual in rows
    ]
    audit = build_public_audit(session)
    if audit["status"] != "pass":
        raise RuntimeError("Public BIOS knowledge audit failed after Wave 21.")
    return {
        "milestone": "v1.26 AMD memory and fabric canonicalization Wave 21",
        "generated_at": utc_now_iso(),
        "actor": ACTOR,
        "backup": {"path": str(BACKUP_PATH), "sha256": BACKUP_SHA256},
        "database_revision": config.EXPECTED_DATABASE_REVISION,
        "summary": {
            "new_canonical_settings": 7,
            "total_mappings_created": 15,
            "reviewed_menu_contexts": 15,
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
            {
                "setting_ids": sorted(WAVE21_EXPO_SCOPE_CONFLICT_IDS),
                "reason": "EXPO labels cover profile selection/loading in Ai Tweaker and profile detection/pretraining in AMD Overclocking; they cannot safely share one identity without a scope model.",
            },
        ],
        "remaining_manual_review": remaining,
        "public_knowledge_audit": audit,
    }


def _markdown(payload: dict[str, object]) -> str:
    summary = payload["summary"]
    lines = [
        "# TuneForge v1.26 AMD Memory and Fabric Wave 21",
        "",
        "## Outcome",
        "",
        f"- New canonical settings: {summary['new_canonical_settings']}",
        f"- New verified mappings: {summary['total_mappings_created']}",
        f"- New reviewed menu contexts: {summary['reviewed_menu_contexts']}",
        f"- Remaining approved unmapped settings: {summary['remaining_manual_review']}",
        "",
        "Every decision uses visually reviewed official ASUS, ASRock, GIGABYTE, and MSI AMD-platform pages. Exact labels, functions, platform context, options, dependencies, and menu hierarchy agree; no community source was required.",
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
            "The three reviewed `EXPO` records remain unmapped because the same label covers profile selection/loading in Ai Tweaker and profile detection/pretraining in AMD Overclocking. Generic `Maximum Frequency` records are mapped only with explicit LCLK parent evidence. The complete decision report and pending backlog are stored in `data/reports/v1.26-amd-memory-fabric-wave21.json`.",
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
        "--apply", action="store_true", help="Commit the pinned v1.26 Wave 21 batch"
    )
    args = parser.parse_args()
    with SessionLocal() as session:
        _verify_preflight(session)
        print(
            "Preflight passed: backup, revision, counts, four manuals, 15 source pins, exclusions, and trust gates match."
        )
        print("Planned outcome: seven new identities and 15 mappings with reviewed menu contexts.")
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
        print("Applied: new_identities=7, mappings=15, menu_contexts=15.")
        print(f"Remaining approved unmapped: {payload['summary']['remaining_manual_review']}.")
        print(f"JSON report: {REPORT_JSON}")
        print(f"Summary report: {REPORT_MARKDOWN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
