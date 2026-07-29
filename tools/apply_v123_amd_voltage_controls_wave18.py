# ruff: noqa: E501
"""Apply TuneForge's pinned v1.23 AMD voltage-controls Wave 18.

Dry-run is the default. Nine identities and 19 approved Source Settings were
visually verified in official ASRock, ASUS, and MSI manuals.
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
BACKUP_PATH = ROOT / "data" / "backups" / "tuneforge-v1.23-pre-wave18.zip"
BACKUP_SHA256 = "6825b7c8364b44025d0f12aff5e186332c62cf1bddda8429d881eaca579aadf9"
REPORT_JSON = ROOT / "data" / "reports" / "v1.23-amd-voltage-controls-wave18.json"
REPORT_MARKDOWN = ROOT / "docs" / "v1.23-amd-voltage-controls-wave18.md"
ACTOR = "v1.23-amd-voltage-controls-official-evidence-reviewer"
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
    setting_id: int,
    manual_id: int,
    page: int,
    name: str,
    menu_path: str,
    evidence: str,
) -> SourcePin:
    return SourcePin(setting_id, manual_id, page, name, menu_path, evidence)


VOLTAGE_RISK = "Changing processor, fabric, memory-PHY, or SoC voltage can increase temperature and power, reduce silicon lifetime, cause instability or data corruption, or require firmware recovery."


def identity(
    canonical_id: str,
    name: str,
    description: str,
    equivalence: str,
    uncertainty: str,
    sources: tuple[SourcePin, ...],
) -> IdentitySeed:
    return IdentitySeed(
        canonical_id,
        name,
        description,
        f"The reviewed official vendors use the exact normalized name for the same AMD voltage rail or control scope; descriptions, parent controls, and adjustment behavior agree that {equivalence}.",
        uncertainty,
        "memory",
        "high",
        VOLTAGE_RISK,
        sources,
    )


MSI_VOLTAGE = "OC > Voltage Setting"
ASUS_PBS = "Advanced > AMD PBS"


IDENTITIES = (
    identity(
        "vddp-voltage-control",
        "VDDP Voltage Control",
        "Selects automatic or manual control of the processor VDDP voltage.",
        "Auto uses firmware-selected VDDP voltage and Manual permits a customized value",
        "The first MSI copy contains a TDC wording error, but its exact label, parent/child placement, and second MSI copy confirm VDDP control.",
        (
            source(
                495,
                2,
                66,
                "VDDP Voltage Control",
                "Advanced > AMD CBS > SMU Common Options > VDDP Voltage Control",
                "ASRock selects default or customized VDDP voltage.",
            ),
            source(
                5300,
                6,
                30,
                "VDDP Voltage Control",
                "OC > Advanced CPU Configuration > AMD CBS > SMU Common Options > VDDP Voltage Control",
                "MSI exposes the VDDP control immediately above its manual VDDP child value.",
            ),
            source(
                5448,
                6,
                41,
                "VDDP Voltage Control",
                f"{MSI_VOLTAGE} > VDDP Voltage Control",
                "MSI documents Auto firmware control and Manual voltage adjustment.",
            ),
        ),
    ),
    identity(
        "vddg-ccd-voltage",
        "VDDG CCD Voltage",
        "Sets the CCD portion of Infinity Fabric data-path voltage.",
        "the value controls VDDG CCD for the Infinity Fabric data path",
        "ASRock does not state a numeric range; ASUS documents 650-1650 mV in 10 mV steps.",
        (
            source(
                284,
                2,
                38,
                "VDDG CCD Voltage",
                "OC Tweaker > DRAM Profile Configuration > VDDG CCD Voltage",
                "ASRock identifies VDDG as the fabric data-path supply derived from VDDCR_MISC.",
            ),
            source(
                6086,
                8,
                46,
                "VDDG CCD Voltage",
                "Ai Tweaker > VDDG CCD Voltage",
                "ASUS identifies VDDG CCD as Infinity Fabric data-path voltage.",
            ),
        ),
    ),
    identity(
        "vddg-voltage-control",
        "VDDG Voltage Control",
        "Selects global or per-CCD control for VDDG-related voltages.",
        "the option selects the apply mode for VDDG-related voltages",
        "MSI does not enumerate the ASUS global and per-CCD labels on the reviewed page.",
        (
            source(
                5440,
                6,
                40,
                "VDDG Voltage Control",
                f"{MSI_VOLTAGE} > VDDG Voltage Control",
                "MSI selects the apply mode for all VDDG-related voltages.",
            ),
            source(
                6566,
                8,
                90,
                "VDDG Voltage Control",
                f"{ASUS_PBS} > VDDG Voltage Control",
                "ASUS selects Auto, global, or per-CCD VDDG control.",
            ),
        ),
    ),
    identity(
        "global-vddg-ccd-voltage",
        "Global VDDG CCD Voltage",
        "Sets the global CCD portion of VDDG fabric voltage.",
        "the value sets global VDDG CCD voltage",
        "MSI does not state a numeric range; ASUS documents 650-1650 mV in 10 mV steps.",
        (
            source(
                5441,
                6,
                40,
                "Global VDDG CCD Voltage",
                f"{MSI_VOLTAGE} > VDDG Voltage Control > Global VDDG CCD Voltage",
                "MSI sets global VDDG CCD voltage with automatic firmware selection available.",
            ),
            source(
                6567,
                8,
                90,
                "Global VDDG CCD Voltage",
                f"{ASUS_PBS} > VDDG Voltage Control > Global VDDG Voltage Control > Global VDDG CCD Voltage",
                "ASUS sets global VDDG CCD voltage when global control is selected.",
            ),
        ),
    ),
    identity(
        "global-vddg-iod-voltage",
        "Global VDDG IOD Voltage",
        "Sets the global I/O-die portion of VDDG fabric voltage.",
        "the value sets global VDDG IOD voltage",
        "MSI does not state a numeric range; ASUS documents 650-1650 mV in 10 mV steps.",
        (
            source(
                5442,
                6,
                40,
                "Global VDDG IOD Voltage",
                f"{MSI_VOLTAGE} > VDDG Voltage Control > Global VDDG IOD Voltage",
                "MSI sets global VDDG IOD voltage with automatic firmware selection available.",
            ),
            source(
                6568,
                8,
                90,
                "Global VDDG IOD Voltage",
                f"{ASUS_PBS} > VDDG Voltage Control > Global VDDG Voltage Control > Global VDDG IOD Voltage",
                "ASUS sets global VDDG IOD voltage when global control is selected.",
            ),
        ),
    ),
    identity(
        "ccd0-ccd-vddg-voltage",
        "CCD0-CCD VDDG Voltage",
        "Sets the CCD portion of VDDG voltage for CCD0.",
        "the value sets VDDG CCD voltage for CCD0",
        "MSI does not state a numeric range; ASUS documents 650-1650 mV in 10 mV steps.",
        (
            source(
                5443,
                6,
                40,
                "CCD0- CCD VDDG Voltage",
                f"{MSI_VOLTAGE} > VDDG Voltage Control > CCD0-CCD VDDG Voltage",
                "MSI sets VDDG CCD voltage for CCD0.",
            ),
            source(
                6569,
                8,
                91,
                "CCD0-CCD VDDG Voltage",
                f"{ASUS_PBS} > VDDG Voltage Control > Per-CCD VDDG Voltage Control > CCD0-CCD VDDG Voltage",
                "ASUS sets VDDG CCD voltage for CCD0 under per-CCD control.",
            ),
        ),
    ),
    identity(
        "ccd0-iod-vddg-voltage",
        "CCD0-IOD VDDG Voltage",
        "Sets the I/O-die portion of VDDG voltage for CCD0.",
        "the value sets VDDG IOD voltage for CCD0",
        "MSI does not state a numeric range; ASUS documents 650-1650 mV in 10 mV steps.",
        (
            source(
                5444,
                6,
                40,
                "CCD0-IOD VDDG Voltage",
                f"{MSI_VOLTAGE} > VDDG Voltage Control > CCD0-IOD VDDG Voltage",
                "MSI sets VDDG IOD voltage for CCD0.",
            ),
            source(
                6570,
                8,
                91,
                "CCD0-IOD VDDG Voltage",
                f"{ASUS_PBS} > VDDG Voltage Control > Per-CCD VDDG Voltage Control > CCD0-IOD VDDG Voltage",
                "ASUS sets VDDG IOD voltage for CCD0 under per-CCD control.",
            ),
        ),
    ),
    identity(
        "vddp-voltage-adjust",
        "VDDP Voltage Adjust",
        "Sets the processor VDDP voltage used for DDR PHY signaling.",
        "the value adjusts CPU VDDP voltage with automatic firmware selection available",
        "MSI does not document the ASUS 5 mV step size or DRAM-voltage ceiling.",
        (
            source(
                5449,
                6,
                41,
                "VDDP Voltage Adjust",
                f"{MSI_VOLTAGE} > VDDP Voltage Control > VDDP Voltage Adjust",
                "MSI sets CPU VDDP voltage with Auto available.",
            ),
            source(
                6573,
                8,
                91,
                "VDDP Voltage Adjust",
                f"{ASUS_PBS} > VDDP Voltage Control > VDDP Voltage Adjust",
                "ASUS identifies VDDP as DDR PHY signaling voltage and documents 5 mV steps.",
            ),
        ),
    ),
    identity(
        "soc-voltage",
        "SoC Voltage",
        "Sets the processor SoC/Uncore voltage used by memory, Infinity Fabric, and integrated graphics.",
        "the value sets VDD_SOC for SoC/Uncore components",
        "ASUS documents LN2-dependent range expansion; MSI does not state ranges.",
        (
            source(
                5438,
                6,
                40,
                "SOC Voltage",
                f"{MSI_VOLTAGE} > CPU NB/SoC Voltage > SOC Voltage",
                "MSI sets CPU VDD_SOC with Auto available.",
            ),
            source(
                6577,
                8,
                91,
                "SoC Voltage",
                f"{ASUS_PBS} > SoC/Uncore OC Mode > SoC Voltage",
                "ASUS sets VDD_SOC for memory, Infinity Fabric, and integrated graphics.",
            ),
        ),
    ),
)

KNOWN_AMBIGUOUS_IDS = {572, 6111, 6690, 7392, 7395, 7657, 8320}
KNOWN_COLLISION_IDS = {47, 77, 435, 491, 5137, 5284, 5292, 6158, 6161, 6321, 6378}
KNOWN_REJECTED_DUPLICATE_IDS = {4901}
KNOWN_SEMANTIC_CONFLICT_IDS = {419, 6306}
WAVE18_EVIDENCE_GAP_IDS = {283, 6580}
BASELINE_COUNTS = {
    CanonicalSetting: 275,
    CanonicalSettingAlias: 311,
    SettingCanonicalMapping: 1022,
    CanonicalMappingEvent: 1022,
    CanonicalIdentityEvent: 290,
    CanonicalSettingClassification: 275,
    CanonicalClassificationEvent: 511,
    SettingMenuContext: 819,
    SettingMenuContextEvent: 819,
    SettingMenuContextCitation: 893,
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
        raise RuntimeError("The verified pre-v1.23 backup is missing or changed.")
    if inspect_backup(BACKUP_PATH).database_revision != config.EXPECTED_DATABASE_REVISION:
        raise RuntimeError("The pre-v1.23 backup has an unexpected revision.")
    if (
        session.scalar(text("SELECT version_num FROM alembic_version"))
        != config.EXPECTED_DATABASE_REVISION
    ):
        raise RuntimeError("The live schema revision changed.")
    if session.scalar(text("PRAGMA integrity_check")) != "ok":
        raise RuntimeError("SQLite integrity_check failed before Wave 18.")
    if _unmapped_count(session) != 2996:
        raise RuntimeError(f"Expected 2,996 unmapped settings, found {_unmapped_count(session)}.")
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
    if len(IDENTITIES) != 9 or len(sources) != 19 or len({x.setting_id for x in sources}) != 19:
        raise RuntimeError("Wave 18 must contain nine identities and 19 unique records.")
    excluded = (
        KNOWN_AMBIGUOUS_IDS
        | KNOWN_COLLISION_IDS
        | KNOWN_REJECTED_DUPLICATE_IDS
        | KNOWN_SEMANTIC_CONFLICT_IDS
        | WAVE18_EVIDENCE_GAP_IDS
    )
    if excluded.intersection({item.setting_id for item in sources}):
        raise RuntimeError("A known excluded record entered Wave 18.")
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
    for setting_id in (
        KNOWN_AMBIGUOUS_IDS
        | KNOWN_COLLISION_IDS
        | KNOWN_SEMANTIC_CONFLICT_IDS
        | WAVE18_EVIDENCE_GAP_IDS
    ):
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
    return f"v1.23 Wave 18 reviewer decision; confidence={CONFIDENCE_BASIS_POINTS / 100:.2f}%. {seed.rationale} Official source: {manual.vendor} manual {manual.id}, page {item.page_number}, {manual.source_url}; reviewed menu path: {item.menu_path}. Remaining uncertainty: {seed.uncertainty}"


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
                note="v1.23 Wave 18 official-manual label, voltage rail, control scope, units, dependencies, options, and menu hierarchy visually verified.",
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
        SettingCanonicalMapping: 19,
        CanonicalMappingEvent: 19,
        CanonicalIdentityEvent: 9,
        CanonicalSettingClassification: 9,
        CanonicalClassificationEvent: 18,
        SettingMenuContext: 19,
        SettingMenuContextEvent: 19,
        SettingMenuContextCitation: 19,
    }
    failures = [
        f"{model.__tablename__} append-only delta mismatch"
        for model, delta in deltas.items()
        if _count(session, model) != baseline[model] + delta
    ]
    if _unmapped_count(session) != 2977:
        failures.append(f"expected 2,977 unmapped, found {_unmapped_count(session)}")
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
        | KNOWN_SEMANTIC_CONFLICT_IDS
        | WAVE18_EVIDENCE_GAP_IDS
    )
    if any(session.get(SettingCanonicalMapping, setting_id) is not None for setting_id in excluded):
        failures.append("a known excluded record was mapped")
    if session.scalar(text("PRAGMA integrity_check")) != "ok":
        failures.append("SQLite integrity_check failed")
    foreign_keys = list(session.execute(text("PRAGMA foreign_key_check")))
    if foreign_keys:
        failures.append(f"foreign-key violations: {foreign_keys!r}")
    if failures:
        raise RuntimeError("Wave 18 verification failed:\n- " + "\n- ".join(failures))


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
            "reason": "Not approved in Wave 18; requires a later evidence disposition.",
        }
        for setting, manual in rows
    ]
    audit = build_public_audit(session)
    if audit["status"] != "pass":
        raise RuntimeError("Public BIOS knowledge audit failed after Wave 18.")
    return {
        "milestone": "v1.23 AMD voltage-controls canonicalization Wave 18",
        "generated_at": utc_now_iso(),
        "actor": ACTOR,
        "backup": {"path": str(BACKUP_PATH), "sha256": BACKUP_SHA256},
        "database_revision": config.EXPECTED_DATABASE_REVISION,
        "summary": {
            "new_canonical_settings": 9,
            "total_mappings_created": 19,
            "reviewed_menu_contexts": 19,
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
                "reason": "Disable Memory Error Injection has contradictory official label and behavior wording and remains unmapped.",
            },
            {
                "setting_ids": sorted(WAVE18_EVIDENCE_GAP_IDS),
                "reason": "VDD Misc Voltage remains unmapped because the ASRock prose describes VDDG rather than independently verifying the VDD Misc rail.",
            },
        ],
        "remaining_manual_review": remaining,
        "public_knowledge_audit": audit,
    }


def _markdown(payload: dict[str, object]) -> str:
    summary = payload["summary"]
    lines = [
        "# TuneForge v1.23 AMD Voltage Controls Wave 18",
        "",
        "## Outcome",
        "",
        f"- New canonical settings: {summary['new_canonical_settings']}",
        f"- New verified mappings: {summary['total_mappings_created']}",
        f"- New reviewed menu contexts: {summary['reviewed_menu_contexts']}",
        f"- Remaining approved unmapped settings: {summary['remaining_manual_review']}",
        "",
        "Every decision uses visually reviewed official ASRock, ASUS, and MSI pages. Name, voltage rail, control scope, units, options, dependencies, and menu context agree; no community source was required.",
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
            "Known semantic collisions and records lacking equivalent official evidence remain unmapped. `VDD Misc Voltage` remains excluded because one official description is mis-associated with VDDG. The complete decision report and pending backlog are stored in `data/reports/v1.23-amd-voltage-controls-wave18.json`.",
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
        "--apply", action="store_true", help="Commit the pinned v1.23 Wave 18 batch"
    )
    args = parser.parse_args()
    with SessionLocal() as session:
        _verify_preflight(session)
        print(
            "Preflight passed: backup, revision, counts, three manuals, 19 source pins, exclusions, and trust gates match."
        )
        print("Planned outcome: nine new identities and 19 mappings with reviewed menu contexts.")
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
        print("Applied: new_identities=9, mappings=19, menu_contexts=19.")
        print(f"Remaining approved unmapped: {payload['summary']['remaining_manual_review']}.")
        print(f"JSON report: {REPORT_JSON}")
        print(f"Summary report: {REPORT_MARKDOWN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
