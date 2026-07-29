# ruff: noqa: E501
"""Apply TuneForge's pinned v1.22 AMD power-limits Wave 17.

Dry-run is the default. Eleven identities and 22 approved Source Settings were
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
BACKUP_PATH = ROOT / "data" / "backups" / "tuneforge-v1.22-pre-wave17.zip"
BACKUP_SHA256 = "ae246ab432a9092e01b2e0a6a72ac003d249017f963e5e5fb30e108284a26271"
REPORT_JSON = ROOT / "data" / "reports" / "v1.22-amd-power-limits-wave17.json"
REPORT_MARKDOWN = ROOT / "docs" / "v1.22-amd-power-limits-wave17.md"
ACTOR = "v1.22-amd-power-limits-official-evidence-reviewer"
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
        f"The reviewed official vendors use the exact normalized name in corresponding AMD SMU controls; descriptions, units, dependencies, and options agree that {equivalence}.",
        uncertainty,
        category_id,
        risk_level,
        risk_rationale,
        sources,
    )


ASROCK_SMU = "Advanced > AMD CBS > SMU Common Options"
ASUS_SMU = "Advanced > AMD CBS > SMU Common Options"
MSI_SMU = "OC > Advanced CPU Configuration > AMD CBS > SMU Common Options"
POWER_RISK = "Changing processor power or current limits can increase power draw, temperature, throttling, instability, or exceed platform cooling and electrical margins."


IDENTITIES = (
    identity(
        "tdp",
        "TDP",
        "Sets the sustained thermal design power limit.",
        "the value sets the sustained TDP limit",
        "MSI does not state the unit on the reviewed page; ASUS documents milliwatts.",
        "cpu-performance",
        "high",
        POWER_RISK,
        (
            source(
                5291,
                6,
                29,
                "TDP",
                f"{MSI_SMU} > TDP Control > TDP",
                "MSI exposes TDP only when TDP Control is Manual.",
            ),
            source(
                6376,
                8,
                73,
                "TDP",
                f"{ASUS_SMU} > TDP Control > TDP",
                "ASUS sets the sustained power limit in milliwatts under manual TDP Control.",
            ),
        ),
    ),
    identity(
        "ppt",
        "PPT",
        "Sets the package power tracking limit.",
        "the value sets the PPT power limit",
        "MSI does not state the unit on the reviewed page; ASUS documents milliwatts.",
        "cpu-performance",
        "high",
        POWER_RISK,
        (
            source(
                5293,
                6,
                29,
                "PPT",
                f"{MSI_SMU} > PPT Control > PPT",
                "MSI exposes PPT only when PPT Control is Manual.",
            ),
            source(
                6379,
                8,
                74,
                "PPT",
                f"{ASUS_SMU} > PPT Control > PPT",
                "ASUS sets PPT in milliwatts under manual PPT Control.",
            ),
        ),
    ),
    identity(
        "tjmax",
        "TjMax",
        "Sets the maximum processor control-temperature limit.",
        "the value sets the maximum Tctl operating temperature",
        "ASUS documents IRM-limit enforcement; MSI does not mention it.",
        "cpu-performance",
        "high",
        "Raising the maximum temperature limit can increase processor temperature and reduce thermal safety margin.",
        (
            source(
                5295,
                6,
                30,
                "TjMax",
                f"{MSI_SMU} > Thermal Control > TjMax",
                "MSI sets the maximum Tctl limit when Thermal Control is Manual.",
            ),
            source(
                6381,
                8,
                74,
                "TjMax",
                f"{ASUS_SMU} > Thermal Control > TjMax",
                "ASUS sets maximum operating temperature with IRM enforcement.",
            ),
        ),
    ),
    identity(
        "tdc-vddcr-vdd",
        "TDC_VDDCR_VDD",
        "Sets the sustained VDDCR_VDD thermal design current limit.",
        "the value sets the VDDCR_VDD TDC limit",
        "MSI does not state the unit on the reviewed page; ASUS documents milliamps.",
        "cpu-performance",
        "high",
        POWER_RISK,
        (
            source(
                5297,
                6,
                30,
                "TDC_VDDCR_VDD",
                f"{MSI_SMU} > TDC Control > TDC_VDDCR_VDD",
                "MSI sets VDDCR_VDD TDC when TDC Control is Manual.",
            ),
            source(
                6383,
                8,
                74,
                "TDC_VDDCR_VDD",
                f"{ASUS_SMU} > TDC Control > TDC_VDDCR_VDD",
                "ASUS sets VDDCR_VDD TDC in milliamps with IRM enforcement.",
            ),
        ),
    ),
    identity(
        "edc-control",
        "EDC Control",
        "Selects default or manually customized electrical design current limits.",
        "Auto uses default EDC limits and Manual permits customized EDC limits",
        "The child EDC value is not part of this identity.",
        "cpu-performance",
        "high",
        POWER_RISK,
        (
            source(
                494,
                2,
                66,
                "EDC Control",
                f"{ASROCK_SMU} > EDC Control",
                "ASRock selects default or customized EDC limits.",
            ),
            source(
                6384,
                8,
                74,
                "EDC Control",
                f"{ASUS_SMU} > EDC Control",
                "ASUS selects default or customized EDC limits.",
            ),
        ),
    ),
    identity(
        "syncfifo-mode-override",
        "SyncFifo Mode Override",
        "Overrides automatic SyncFifo mode selection.",
        "the option enables, disables, or automatically selects SyncFifo mode override",
        "ASRock states Auto disables SyncFifo mode; ASUS does not restate that behavior.",
        "power-management",
        "high",
        "Changing low-level clock-domain synchronization behavior can affect processor fabric stability.",
        (
            source(
                497,
                2,
                67,
                "SyncFifo Mode Override",
                f"{ASROCK_SMU} > SyncFifo Mode Override",
                "ASRock exposes Auto, Disabled, and Enabled and documents Auto behavior.",
            ),
            source(
                6404,
                8,
                75,
                "SyncFifo Mode Override",
                f"{ASUS_SMU} > SyncFifo Mode Override",
                "ASUS exposes Disable, Enable, and Auto.",
            ),
        ),
    ),
    identity(
        "sustained-powerlimit",
        "Sustained PowerLimit",
        "Sets the sustained processor power limit.",
        "the value configures the sustained power limit",
        "The reviewed pages do not state the unit.",
        "cpu-performance",
        "high",
        POWER_RISK,
        (
            source(
                498,
                2,
                67,
                "Sustained PowerLimit",
                f"{ASROCK_SMU} > Sustained PowerLimit",
                "ASRock adjusts the sustained power limit.",
            ),
            source(
                6405,
                8,
                75,
                "Sustained PowerLimit",
                f"{ASUS_SMU} > Sustained PowerLimit",
                "ASUS identifies the SMU sustained-power-limit control.",
            ),
        ),
    ),
    identity(
        "fast-ppt-limit",
        "Fast PPT Limit",
        "Sets the fast package power tracking limit.",
        "the value configures the fast PPT limit",
        "The reviewed pages do not state the unit or averaging interval.",
        "cpu-performance",
        "high",
        POWER_RISK,
        (
            source(
                499,
                2,
                67,
                "Fast PPT Limit",
                f"{ASROCK_SMU} > Fast PPT Limit",
                "ASRock adjusts the Fast PPT limit.",
            ),
            source(
                6406,
                8,
                76,
                "Fast PPT Limit",
                f"{ASUS_SMU} > Fast PPT Limit",
                "ASUS identifies the SMU fast-PPT-limit control.",
            ),
        ),
    ),
    identity(
        "slow-ppt-limit",
        "Slow PPT Limit",
        "Sets the slow package power tracking limit.",
        "the value configures the slow PPT limit",
        "The reviewed pages do not state the unit or averaging interval.",
        "cpu-performance",
        "high",
        POWER_RISK,
        (
            source(
                500,
                2,
                67,
                "Slow PPT Limit",
                f"{ASROCK_SMU} > Slow PPT Limit",
                "ASRock adjusts the Slow PPT limit.",
            ),
            source(
                6407,
                8,
                76,
                "Slow PPT Limit",
                f"{ASUS_SMU} > Slow PPT Limit",
                "ASUS identifies the SMU slow-PPT-limit control.",
            ),
        ),
    ),
    identity(
        "slow-ppt-time-constant",
        "Slow PPT Time Constant",
        "Sets the slow package power tracking time constant in seconds.",
        "the value configures the slow PPT time constant in seconds",
        "The valid numeric range is not stated.",
        "cpu-performance",
        "high",
        POWER_RISK,
        (
            source(
                501,
                2,
                67,
                "Slow PPT Time Constant",
                f"{ASROCK_SMU} > Slow PPT Time Constant",
                "ASRock adjusts the Slow PPT time constant in seconds.",
            ),
            source(
                6408,
                8,
                76,
                "Slow PPT Time Constant",
                f"{ASUS_SMU} > Slow PPT Time Constant",
                "ASUS identifies the Slow PPT time constant in seconds.",
            ),
        ),
    ),
    identity(
        "gfxoff",
        "GFXOFF",
        "Enables or disables the AMD integrated-graphics GFXOFF low-power state.",
        "the option enables, disables, or automatically selects GFXOFF",
        "Availability depends on processor graphics support.",
        "power-management",
        "moderate",
        "Changing the graphics low-power state can affect idle power and integrated-graphics resume behavior.",
        (
            source(
                502, 2, 67, "GFXOFF", f"{ASROCK_SMU} > GFXOFF", "ASRock enables or disables GFXOFF."
            ),
            source(
                6409,
                8,
                76,
                "GFXOFF",
                f"{ASUS_SMU} > GFXOFF",
                "ASUS exposes Disable, Enable, and Auto for GFXOFF.",
            ),
        ),
    ),
)

KNOWN_AMBIGUOUS_IDS = {572, 6111, 6690, 7392, 7395, 7657, 8320}
KNOWN_COLLISION_IDS = {47, 77, 435, 491, 5137, 5284, 5292, 6158, 6161, 6321, 6378}
KNOWN_REJECTED_DUPLICATE_IDS = {4901}
KNOWN_SEMANTIC_CONFLICT_IDS = {419, 6306}
BASELINE_COUNTS = {
    CanonicalSetting: 264,
    CanonicalSettingAlias: 300,
    SettingCanonicalMapping: 1000,
    CanonicalMappingEvent: 1000,
    CanonicalIdentityEvent: 279,
    CanonicalSettingClassification: 264,
    CanonicalClassificationEvent: 489,
    SettingMenuContext: 797,
    SettingMenuContextEvent: 797,
    SettingMenuContextCitation: 871,
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
        raise RuntimeError("The verified pre-v1.22 backup is missing or changed.")
    if inspect_backup(BACKUP_PATH).database_revision != config.EXPECTED_DATABASE_REVISION:
        raise RuntimeError("The pre-v1.22 backup has an unexpected revision.")
    if (
        session.scalar(text("SELECT version_num FROM alembic_version"))
        != config.EXPECTED_DATABASE_REVISION
    ):
        raise RuntimeError("The live schema revision changed.")
    if session.scalar(text("PRAGMA integrity_check")) != "ok":
        raise RuntimeError("SQLite integrity_check failed before Wave 17.")
    if _unmapped_count(session) != 3018:
        raise RuntimeError(f"Expected 3,018 unmapped settings, found {_unmapped_count(session)}.")
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
    if len(IDENTITIES) != 11 or len(sources) != 22 or len({x.setting_id for x in sources}) != 22:
        raise RuntimeError("Wave 17 must contain 11 identities and 22 unique records.")
    excluded = (
        KNOWN_AMBIGUOUS_IDS
        | KNOWN_COLLISION_IDS
        | KNOWN_REJECTED_DUPLICATE_IDS
        | KNOWN_SEMANTIC_CONFLICT_IDS
    )
    if excluded.intersection({item.setting_id for item in sources}):
        raise RuntimeError("A known excluded record entered Wave 17.")
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
    for setting_id in KNOWN_AMBIGUOUS_IDS | KNOWN_COLLISION_IDS | KNOWN_SEMANTIC_CONFLICT_IDS:
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
    return f"v1.22 Wave 17 reviewer decision; confidence={CONFIDENCE_BASIS_POINTS / 100:.2f}%. {seed.rationale} Official source: {manual.vendor} manual {manual.id}, page {item.page_number}, {manual.source_url}; reviewed menu path: {item.menu_path}. Remaining uncertainty: {seed.uncertainty}"


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
                note="v1.22 Wave 17 official-manual label, power or thermal function, units, dependencies, options, and menu hierarchy visually verified.",
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
        CanonicalSetting: 11,
        CanonicalSettingAlias: 11,
        SettingCanonicalMapping: 22,
        CanonicalMappingEvent: 22,
        CanonicalIdentityEvent: 11,
        CanonicalSettingClassification: 11,
        CanonicalClassificationEvent: 22,
        SettingMenuContext: 22,
        SettingMenuContextEvent: 22,
        SettingMenuContextCitation: 22,
    }
    failures = [
        f"{model.__tablename__} append-only delta mismatch"
        for model, delta in deltas.items()
        if _count(session, model) != baseline[model] + delta
    ]
    if _unmapped_count(session) != 2996:
        failures.append(f"expected 2,996 unmapped, found {_unmapped_count(session)}")
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
    )
    if any(session.get(SettingCanonicalMapping, setting_id) is not None for setting_id in excluded):
        failures.append("a known excluded record was mapped")
    if session.scalar(text("PRAGMA integrity_check")) != "ok":
        failures.append("SQLite integrity_check failed")
    foreign_keys = list(session.execute(text("PRAGMA foreign_key_check")))
    if foreign_keys:
        failures.append(f"foreign-key violations: {foreign_keys!r}")
    if failures:
        raise RuntimeError("Wave 17 verification failed:\n- " + "\n- ".join(failures))


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
            "reason": "Not approved in Wave 17; requires a later evidence disposition.",
        }
        for setting, manual in rows
    ]
    audit = build_public_audit(session)
    if audit["status"] != "pass":
        raise RuntimeError("Public BIOS knowledge audit failed after Wave 17.")
    return {
        "milestone": "v1.22 AMD power-limits canonicalization Wave 17",
        "generated_at": utc_now_iso(),
        "actor": ACTOR,
        "backup": {"path": str(BACKUP_PATH), "sha256": BACKUP_SHA256},
        "database_revision": config.EXPECTED_DATABASE_REVISION,
        "summary": {
            "new_canonical_settings": 11,
            "total_mappings_created": 22,
            "reviewed_menu_contexts": 22,
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
        ],
        "remaining_manual_review": remaining,
        "public_knowledge_audit": audit,
    }


def _markdown(payload: dict[str, object]) -> str:
    summary = payload["summary"]
    lines = [
        "# TuneForge v1.22 AMD Power Limits Wave 17",
        "",
        "## Outcome",
        "",
        f"- New canonical settings: {summary['new_canonical_settings']}",
        f"- New verified mappings: {summary['total_mappings_created']}",
        f"- New reviewed menu contexts: {summary['reviewed_menu_contexts']}",
        f"- Remaining approved unmapped settings: {summary['remaining_manual_review']}",
        "",
        "Every decision uses visually reviewed official ASRock, ASUS, and MSI pages. Name, power or thermal function, units, options, dependencies, and menu context agree; no community source was required.",
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
            "Known semantic collisions and records lacking equivalent official evidence remain unmapped. Ambiguous repeated `PPT Control` records remain excluded. The complete decision report and pending backlog are stored in `data/reports/v1.22-amd-power-limits-wave17.json`.",
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
        "--apply", action="store_true", help="Commit the pinned v1.22 Wave 17 batch"
    )
    args = parser.parse_args()
    with SessionLocal() as session:
        _verify_preflight(session)
        print(
            "Preflight passed: backup, revision, counts, three manuals, 22 source pins, exclusions, and trust gates match."
        )
        print("Planned outcome: 11 new identities and 22 mappings with reviewed menu contexts.")
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
        print("Applied: new_identities=11, mappings=22, menu_contexts=22.")
        print(f"Remaining approved unmapped: {payload['summary']['remaining_manual_review']}.")
        print(f"JSON report: {REPORT_JSON}")
        print(f"Summary report: {REPORT_MARKDOWN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
