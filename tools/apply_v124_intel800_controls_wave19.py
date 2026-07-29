# ruff: noqa: E501
"""Apply TuneForge's pinned v1.24 Intel 800 controls Wave 19.

Dry-run is the default. Nine identities and 18 approved Source Settings were
visually verified in official ASUS and GIGABYTE Intel 800-series manuals.
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
BACKUP_PATH = ROOT / "data" / "backups" / "tuneforge-v1.24-pre-wave19.zip"
BACKUP_SHA256 = "32fd4dcf29ec9de9239e336b37546cceee0fc03663418ca7961f7722e9315466"
REPORT_JSON = ROOT / "data" / "reports" / "v1.24-intel800-controls-wave19.json"
REPORT_MARKDOWN = ROOT / "docs" / "v1.24-intel800-controls-wave19.md"
ACTOR = "v1.24-intel800-controls-official-evidence-reviewer"
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
        7,
        "gigabyte",
        "b46c52231334bfe914491cc3d6aa9265631914ba9c1de6bb6aa11841b46a2fe5",
        "https://www.gigabyte.com/Motherboard/Z890-AORUS-ELITE-WIFI7/support#support-manual",
        30,
    ),
)


def source(
    setting_id: int,
    manual_id: int,
    page: int,
    name: str,
    menu_path: str,
    evidence: str,
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
        "The reviewed official ASUS and GIGABYTE Intel 800-series manuals use the exact normalized name in consistent BIOS context; their descriptions agree that "
        + equivalence
        + ".",
        uncertainty,
        category_id,
        risk_level,
        risk_rationale,
        sources,
    )


CPU_RISK = "Changing low-level CPU ratios, references, or voltage boot modes can cause instability, boot failure, excess voltage or temperature, or require firmware recovery."
THERMAL_RISK = "Changing the platform thermal-management framework can alter system thermal and power behavior or OS policy integration."
IO_RISK = "Changing integrated Thunderbolt or USB4 host-router behavior can prevent attached devices or operating-system drivers from initializing correctly."
ASUS_PARADISE = "Ai Tweaker > Tweaker's Paradise"
GIGABYTE_CPU = "Tweaker > Advanced CPU Settings"
ASUS_TBT = "Advanced > Thunderbolt(TM) Configuration"
GIGABYTE_TBT = "Settings > IO Ports > Thunderbolt(TM) Configuration"


IDENTITIES = (
    identity(
        "core-minimum-ratio",
        "Core Minimum Ratio",
        "Sets the minimum processor core ratio request, subject to processor limits.",
        "the setting requests the minimum CPU core ratio",
        "ASUS documents 100 MHz ratio units and clamping behavior; GIGABYTE provides only a concise functional description.",
        "cpu-performance",
        "high",
        CPU_RISK,
        (
            source(
                955,
                3,
                45,
                "Core Minimum Ratio",
                f"{ASUS_PARADISE} > CPU OC Features > Core Minimum Ratio",
                "ASUS requests the core minimum ratio, clamps out-of-range requests, and uses 0 for no setting.",
            ),
            source(
                5554,
                7,
                12,
                "Core Minimum Ratio",
                f"{GIGABYTE_CPU} > Core Minimum Ratio",
                "GIGABYTE identifies this setting as the core minimum-ratio control.",
            ),
        ),
    ),
    identity(
        "cpu-bgref-mode",
        "CPU BGREF Mode",
        "Selects the CPU bandgap-reference operating mode.",
        "the setting controls CPU Bandgap Reference mode",
        "ASUS lists Auto, Normal, and Bandgap Bypassed; GIGABYTE does not enumerate values on the reviewed page.",
        "cpu-performance",
        "high",
        CPU_RISK,
        (
            source(
                954,
                3,
                45,
                "CPU BGREF Mode",
                f"{ASUS_PARADISE} > CPU OC Features > CPU BGREF Mode",
                "ASUS selects Normal or Bandgap Bypassed CPU Bandgap Reference mode, with Auto available.",
            ),
            source(
                5549,
                7,
                11,
                "CPU BGREF Mode",
                f"{GIGABYTE_CPU} > CPU BGREF Mode",
                "GIGABYTE identifies this setting as CPU Bandgap Reference Mode.",
            ),
        ),
    ),
    identity(
        "cpu-d2d-ratio",
        "CPU D2D Ratio",
        "Sets the processor die-to-die interconnect ratio.",
        "the setting controls the CPU die-to-die bus ratio",
        "ASUS documents a 21x default; GIGABYTE does not state a default on the reviewed page.",
        "cpu-performance",
        "high",
        CPU_RISK,
        (
            source(
                1000,
                3,
                49,
                "CPU D2D Ratio",
                "Ai Tweaker > CPU D2D Ratio",
                "ASUS defines D2D as the bus ratio connecting processor tiles and states a 21x default.",
            ),
            source(
                5553,
                7,
                11,
                "CPU D2D Ratio",
                f"{GIGABYTE_CPU} > CPU D2D Ratio",
                "GIGABYTE identifies this setting as the CPU D2D ratio control.",
            ),
        ),
    ),
    identity(
        "intel-innovation-platform-framework",
        "Intel(R) Innovation Platform Framework",
        "Enables or disables Intel Innovation Platform Framework thermal-management support.",
        "the setting enables or disables Intel Innovation Platform Framework",
        "ASUS calls out the thermal framework explicitly; GIGABYTE uses the Intel IPF product abbreviation.",
        "power-management",
        "moderate",
        THERMAL_RISK,
        (
            source(
                1112,
                3,
                62,
                "Intel(R) Innovation Platform Framework",
                "Advanced > Thermal Configuration > Intel(R) Innovation Platform Framework Configuration > Intel(R) Innovation Platform Framework",
                "ASUS enables or disables Intel Innovation Platform Thermal Framework.",
            ),
            source(
                5552,
                7,
                11,
                "Intel(R) Innovation Platform Framework",
                f"{GIGABYTE_CPU} > Intel(R) Innovation Platform Framework",
                "GIGABYTE enables or disables Intel Innovation Platform Framework (Intel IPF).",
            ),
        ),
    ),
    identity(
        "ngu-ratio",
        "NGU Ratio",
        "Sets the operating ratio for the processor NGU interconnect within the SoC-memory-controller domain.",
        "the setting controls the NGU interconnect operating ratio",
        "ASUS documents voltage-frequency-curve behavior and a 26x default; GIGABYTE provides only a concise functional description.",
        "cpu-performance",
        "high",
        CPU_RISK,
        (
            source(
                999,
                3,
                49,
                "NGU Ratio",
                "Ai Tweaker > NGU Ratio",
                "ASUS sets the NGU ratio used by the SoC-memory-controller interconnect and states a 26x default.",
            ),
            source(
                5523,
                7,
                10,
                "NGU Ratio",
                f"{GIGABYTE_CPU} > NGU Ratio",
                "GIGABYTE sets the NGU clock operating ratio.",
            ),
        ),
    ),
    identity(
        "integrated-thunderbolt-enable",
        "Integrated Thunderbolt(TM) Enable",
        "Enables or disables the processor-integrated Thunderbolt controller.",
        "the setting enables or disables the integrated Thunderbolt controller",
        "Availability remains motherboard-dependent as stated by the official manuals.",
        "peripheral-io",
        "moderate",
        IO_RISK,
        (
            source(
                1135,
                3,
                65,
                "Integrated Thunderbolt(TM) Enable",
                f"{ASUS_TBT} > Integrated Thunderbolt(TM) Enable",
                "ASUS enables or disables Integrated Thunderbolt and reveals its configuration submenu when enabled.",
            ),
            source(
                5658,
                7,
                23,
                "Integrated Thunderbolt(TM) Enable",
                f"{GIGABYTE_TBT} > Integrated Thunderbolt(TM) Enable",
                "GIGABYTE enables or disables the integrated Thunderbolt controller.",
            ),
        ),
    ),
    identity(
        "usb4-host-router-class-code",
        "USB4 Host Router Class Code",
        "Selects the class code used by the USB4 host router to load the applicable operating-system driver.",
        "the setting chooses the USB4 host-router class code used for driver loading",
        "The manuals name their OS-inbox-driver option differently (USB4 Standard versus PCIe 3 Slot); this value-label difference does not change the setting identity.",
        "peripheral-io",
        "moderate",
        IO_RISK,
        (
            source(
                1149,
                3,
                66,
                "USB4 Host Router Class Code",
                f"{ASUS_TBT} > USB4 Host Router Class Code",
                "ASUS applies a host-router class code for Auto/OSPM, Intel USB4 Ver2, or the OS inbox driver.",
            ),
            source(
                5660,
                7,
                23,
                "USB4 Host Router Class Code",
                f"{GIGABYTE_TBT} > USB4 Host Router Class Code",
                "GIGABYTE applies a host-router class code for Auto/OSPM, Intel USB4 Ver2, or the OS inbox driver.",
            ),
        ),
    ),
    identity(
        "vccia-boot-voltage",
        "VCCIA Boot Voltage",
        "Selects the processor VCCIA boot-voltage mode.",
        "the setting controls VCCIA boot voltage",
        "ASUS documents Nominal and High Voltage modes and limits; GIGABYTE does not enumerate values on the reviewed page.",
        "cpu-performance",
        "high",
        CPU_RISK,
        (
            source(
                925,
                3,
                42,
                "VCCIA Boot Voltage",
                "Ai Tweaker > Max Voltage Limits > VCCIA Boot Voltage",
                "ASUS selects Nominal or High VCCIA boot voltage, with Auto available.",
            ),
            source(
                5547,
                7,
                11,
                "VCCIA Boot Voltage",
                f"{GIGABYTE_CPU} > VCCIA Boot Voltage",
                "GIGABYTE identifies this setting as the VCCIA boot-voltage control.",
            ),
        ),
    ),
    identity(
        "vccsa-boot-voltage",
        "VCCSA Boot Voltage",
        "Selects the processor system-agent VCCSA boot-voltage mode.",
        "the setting controls VCCSA boot voltage",
        "ASUS documents Nominal and High Voltage modes and limits; GIGABYTE does not enumerate values on the reviewed page.",
        "cpu-performance",
        "high",
        CPU_RISK,
        (
            source(
                926,
                3,
                43,
                "VCCSA Boot Voltage",
                "Ai Tweaker > Max Voltage Limits > VCCSA Boot Voltage",
                "ASUS selects Nominal or High VCCSA boot voltage, with Auto available.",
            ),
            source(
                5548,
                7,
                11,
                "VCCSA Boot Voltage",
                f"{GIGABYTE_CPU} > VCCSA Boot Voltage",
                "GIGABYTE identifies this setting as the VCCSA boot-voltage control.",
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
    CanonicalSetting: 284,
    CanonicalSettingAlias: 320,
    SettingCanonicalMapping: 1041,
    CanonicalMappingEvent: 1041,
    CanonicalIdentityEvent: 299,
    CanonicalSettingClassification: 284,
    CanonicalClassificationEvent: 529,
    SettingMenuContext: 838,
    SettingMenuContextEvent: 838,
    SettingMenuContextCitation: 912,
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
        raise RuntimeError("The verified pre-v1.24 backup is missing or changed.")
    if inspect_backup(BACKUP_PATH).database_revision != config.EXPECTED_DATABASE_REVISION:
        raise RuntimeError("The pre-v1.24 backup has an unexpected revision.")
    if (
        session.scalar(text("SELECT version_num FROM alembic_version"))
        != config.EXPECTED_DATABASE_REVISION
    ):
        raise RuntimeError("The live schema revision changed.")
    if session.scalar(text("PRAGMA integrity_check")) != "ok":
        raise RuntimeError("SQLite integrity_check failed before Wave 19.")
    if _unmapped_count(session) != 2977:
        raise RuntimeError(f"Expected 2,977 unmapped settings, found {_unmapped_count(session)}.")
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
    if len(IDENTITIES) != 9 or len(sources) != 18 or len({x.setting_id for x in sources}) != 18:
        raise RuntimeError("Wave 19 must contain nine identities and 18 unique records.")
    if _excluded().intersection({item.setting_id for item in sources}):
        raise RuntimeError("A known excluded record entered Wave 19.")
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
    return f"v1.24 Wave 19 reviewer decision; confidence={CONFIDENCE_BASIS_POINTS / 100:.2f}%. {seed.rationale} Official source: {manual.vendor} manual {manual.id}, page {item.page_number}, {manual.source_url}; reviewed menu path: {item.menu_path}. Remaining uncertainty: {seed.uncertainty}"


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
                note="v1.24 Wave 19 official-manual name, function, options, dependencies, platform, and menu hierarchy visually verified.",
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
        CanonicalSetting: 9,
        CanonicalSettingAlias: 9,
        SettingCanonicalMapping: 18,
        CanonicalMappingEvent: 18,
        CanonicalIdentityEvent: 9,
        CanonicalSettingClassification: 9,
        CanonicalClassificationEvent: 18,
        SettingMenuContext: 18,
        SettingMenuContextEvent: 18,
        SettingMenuContextCitation: 18,
    }
    failures = [
        f"{model.__tablename__} append-only delta mismatch"
        for model, delta in deltas.items()
        if _count(session, model) != baseline[model] + delta
    ]
    if _unmapped_count(session) != 2959:
        failures.append(f"expected 2,959 unmapped, found {_unmapped_count(session)}")
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
        raise RuntimeError("Wave 19 verification failed:\n- " + "\n- ".join(failures))


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
            "reason": "Not approved in Wave 19; requires a later evidence disposition.",
        }
        for setting, manual in rows
    ]
    audit = build_public_audit(session)
    if audit["status"] != "pass":
        raise RuntimeError("Public BIOS knowledge audit failed after Wave 19.")
    return {
        "milestone": "v1.24 Intel 800 controls canonicalization Wave 19",
        "generated_at": utc_now_iso(),
        "actor": ACTOR,
        "backup": {"path": str(BACKUP_PATH), "sha256": BACKUP_SHA256},
        "database_revision": config.EXPECTED_DATABASE_REVISION,
        "summary": {
            "new_canonical_settings": 9,
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
                "reason": "USB4 CM Mode is documented as mode selection by ASUS but enable/disable by GIGABYTE; the pair remains unmapped.",
            },
        ],
        "remaining_manual_review": remaining,
        "public_knowledge_audit": audit,
    }


def _markdown(payload: dict[str, object]) -> str:
    summary = payload["summary"]
    lines = [
        "# TuneForge v1.24 Intel 800 Controls Wave 19",
        "",
        "## Outcome",
        "",
        f"- New canonical settings: {summary['new_canonical_settings']}",
        f"- New verified mappings: {summary['total_mappings_created']}",
        f"- New reviewed menu contexts: {summary['reviewed_menu_contexts']}",
        f"- Remaining approved unmapped settings: {summary['remaining_manual_review']}",
        "",
        "Every decision uses visually reviewed official ASUS and GIGABYTE Intel 800-series pages. Name, function, platform generation, dependencies, and menu context agree; no community source was required.",
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
            "Known collisions and evidence gaps remain unmapped. `USB4 CM Mode` was reviewed but excluded because the two official manuals describe materially different control behavior. The complete decision report and pending backlog are stored in `data/reports/v1.24-intel800-controls-wave19.json`.",
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
        "--apply", action="store_true", help="Commit the pinned v1.24 Wave 19 batch"
    )
    args = parser.parse_args()
    with SessionLocal() as session:
        _verify_preflight(session)
        print(
            "Preflight passed: backup, revision, counts, two manuals, 18 source pins, exclusions, and trust gates match."
        )
        print("Planned outcome: nine new identities and 18 mappings with reviewed menu contexts.")
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
        print("Applied: new_identities=9, mappings=18, menu_contexts=18.")
        print(f"Remaining approved unmapped: {payload['summary']['remaining_manual_review']}.")
        print(f"JSON report: {REPORT_JSON}")
        print(f"Summary report: {REPORT_MARKDOWN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
