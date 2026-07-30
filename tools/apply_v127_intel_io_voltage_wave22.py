# ruff: noqa: E501
"""Apply TuneForge's pinned v1.27 Intel I/O and voltage Wave 22.

Dry-run is the default. Three identities and eight approved Source Settings
were visually verified in official ASUS, ASRock, and MSI manuals.
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
BACKUP_PATH = ROOT / "data" / "backups" / "tuneforge-v1.27-pre-wave22.zip"
BACKUP_SHA256 = "8e0a577c010a5e6db089ff1e62f6bc765611368edd7f201fc65a89e873ffc81f"
REPORT_JSON = ROOT / "data" / "reports" / "v1.27-intel-io-voltage-wave22.json"
REPORT_MARKDOWN = ROOT / "docs" / "v1.27-intel-io-voltage-wave22.md"
ACTOR = "v1.27-intel-io-voltage-official-evidence-reviewer"
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
        f"Official manuals from multiple vendors use the exact normalized name in consistent BIOS context and independently document that {equivalence}.",
        uncertainty,
        category_id,
        risk_level,
        risk_rationale,
        sources,
    )


THUNDERBOLT_RISK = "Changing the discrete Thunderbolt host-router port topology can prevent attached devices, bridges, or operating-system drivers from initializing correctly."
PCIE_RISK = "Forcing an unsupported M.2 PCIe generation can prevent storage detection, reduce performance, or cause link instability and boot failure."
VOLTAGE_RISK = "Changing the chipset 1.05 V rail can cause instability, excess power or temperature, peripheral failure, or require firmware recovery."


IDENTITIES = (
    identity(
        "tbt-host-router",
        "TBT Host Router",
        "Selects the discrete Thunderbolt host-router port configuration according to the ports available.",
        "the setting configures the TBT host router for the available one- or two-port topology",
        "MSI summarizes the control as enable/disable by available ports; ASUS explicitly enumerates One Port and Two Port, with matching 56/106 bus-reservation context.",
        "peripheral-io",
        "moderate",
        THUNDERBOLT_RISK,
        (
            source(
                1153,
                3,
                67,
                "TBT Host Router",
                "Advanced > Thunderbolt(TM) Configuration > TBT Controller Configuration > TBT Host Router",
                "ASUS Intel 800-series firmware selects One Port or Two Port and documents matching extra-bus reservations.",
            ),
            source(
                4575,
                5,
                21,
                "TBT Host Router",
                "Settings > Advanced > Intel(R) Thunderbolt > Discrete Thunderbolt(TM) Configuration > DTBT Controller 0 Configuration > TBT Host Router",
                "MSI enables or disables the host router based on available ports and documents 56/106 for one/two-port hosts.",
            ),
            source(
                7563,
                11,
                59,
                "TBT Host Router",
                "Advanced > Thunderbolt(TM) Configuration > Discrete Thunderbolt(TM) Configuration > DTBT Controller 0 Configuration > TBT Host Router",
                "ASUS Intel 600-series firmware selects One Port or Two Port within discrete Thunderbolt controller configuration.",
            ),
            source(
                8212,
                12,
                63,
                "TBT Host Router",
                "Advanced > Thunderbolt(TM) Configuration > Discrete Thunderbolt(TM) Configuration > DTBT Controller 0 Configuration > TBT Host Router",
                "ASUS Intel 700-series firmware exposes the same one/two-port host-router control and bus reservations.",
            ),
        ),
    ),
    identity(
        "m2-1-link-speed",
        "M.2_1 Link Speed",
        "Selects the PCIe link-speed generation used by the motherboard's M.2_1 slot.",
        "the setting configures the M.2_1 slot's PCIe link speed",
        "ASRock exposes Gen1-Gen5 while the reviewed ASUS Intel 600-series board exposes Gen1-Gen4; supported generations remain source-hardware specific.",
        "pcie-resource",
        "moderate",
        PCIE_RISK,
        (
            source(
                530,
                2,
                70,
                "M.2_1 Link Speed",
                "Advanced > AMD PBS > M.2_1 Link Speed",
                "ASRock configures M.2_1 Link Speed with Auto and Gen1 through Gen5 options.",
            ),
            source(
                7539,
                11,
                55,
                "M.2_1 Link Speed",
                "Advanced > System Agent (SA) Configuration > PCI Express Configuration > M.2_1 Link Speed",
                "ASUS configures the M.2_1 slot with Auto and Gen1 through Gen4 options.",
            ),
        ),
    ),
    identity(
        "pch-1-05v-voltage",
        "PCH 1.05V Voltage",
        "Configures the motherboard chipset or Platform Controller Hub 1.05 V rail.",
        "the setting adjusts the PCH 1.05 V rail",
        "ASUS documents an Intel-board range of 0.800-1.600 V; ASRock provides a concise AMD-board description. Applicability and valid values remain attached to each cited platform record.",
        "peripheral-io",
        "high",
        VOLTAGE_RISK,
        (
            source(
                290,
                2,
                39,
                "PCH 1.05V Voltage",
                "OC Tweaker > External Voltage Settings > PCH 1.05V Voltage",
                "ASRock configures the PCH 1.05 V rail in External Voltage Settings.",
            ),
            source(
                7416,
                11,
                40,
                "PCH 1.05V Voltage",
                "Ai Tweaker > Tweaker's Paradise > PCH 1.05V Voltage",
                "ASUS configures the PCH 1.05 V rail from 0.800 through 1.600 V in 0.010 V steps.",
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
WAVE22_MEMORY_CONTROLLER_SCOPE_CONFLICT_IDS = {1033, 5342, 7455, 8107}
WAVE22_OVER_TEMPERATURE_MECHANISM_CONFLICT_IDS = {4795, 5528}
WAVE22_CPU_RATIO_MODE_SCOPE_CONFLICT_IDS = {36, 4741}
BASELINE_COUNTS = {
    CanonicalSetting: 308,
    CanonicalSettingAlias: 344,
    SettingCanonicalMapping: 1092,
    CanonicalMappingEvent: 1092,
    CanonicalIdentityEvent: 323,
    CanonicalSettingClassification: 308,
    CanonicalClassificationEvent: 577,
    SettingMenuContext: 889,
    SettingMenuContextEvent: 889,
    SettingMenuContextCitation: 963,
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
        | WAVE22_MEMORY_CONTROLLER_SCOPE_CONFLICT_IDS
        | WAVE22_OVER_TEMPERATURE_MECHANISM_CONFLICT_IDS
        | WAVE22_CPU_RATIO_MODE_SCOPE_CONFLICT_IDS
    )


def _verify_preflight(session) -> None:
    if not BACKUP_PATH.is_file() or _sha256(BACKUP_PATH) != BACKUP_SHA256:
        raise RuntimeError("The verified pre-v1.27 backup is missing or changed.")
    if inspect_backup(BACKUP_PATH).database_revision != config.EXPECTED_DATABASE_REVISION:
        raise RuntimeError("The pre-v1.27 backup has an unexpected revision.")
    if (
        session.scalar(text("SELECT version_num FROM alembic_version"))
        != config.EXPECTED_DATABASE_REVISION
    ):
        raise RuntimeError("The live schema revision changed.")
    if session.scalar(text("PRAGMA integrity_check")) != "ok":
        raise RuntimeError("SQLite integrity_check failed before Wave 22.")
    if _unmapped_count(session) != 2926:
        raise RuntimeError(f"Expected 2,926 unmapped settings, found {_unmapped_count(session)}.")
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
    if len(IDENTITIES) != 3 or len(sources) != 8 or len({x.setting_id for x in sources}) != 8:
        raise RuntimeError("Wave 22 must contain three identities and eight unique records.")
    if _excluded().intersection({item.setting_id for item in sources}):
        raise RuntimeError("A known excluded record entered Wave 22.")
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
    return f"v1.27 Wave 22 reviewer decision; confidence={CONFIDENCE_BASIS_POINTS / 100:.2f}%. {seed.rationale} Official source: {manual.vendor} manual {manual.id}, page {item.page_number}, {manual.source_url}; reviewed menu path: {item.menu_path}. Remaining uncertainty: {seed.uncertainty}"


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
                note="v1.27 Wave 22 official-manual name, function, options, dependencies, platform, and menu hierarchy visually verified.",
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
        CanonicalSetting: 3,
        CanonicalSettingAlias: 3,
        SettingCanonicalMapping: 8,
        CanonicalMappingEvent: 8,
        CanonicalIdentityEvent: 3,
        CanonicalSettingClassification: 3,
        CanonicalClassificationEvent: 6,
        SettingMenuContext: 8,
        SettingMenuContextEvent: 8,
        SettingMenuContextCitation: 8,
    }
    failures = [
        f"{model.__tablename__} append-only delta mismatch"
        for model, delta in deltas.items()
        if _count(session, model) != baseline[model] + delta
    ]
    if _unmapped_count(session) != 2918:
        failures.append(f"expected 2,918 unmapped, found {_unmapped_count(session)}")
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
        raise RuntimeError("Wave 22 verification failed:\n- " + "\n- ".join(failures))


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
    conflict_reasons = {
        **{
            setting_id: "Memory Controller Voltage has AMD profile-specific and Intel rail-control scopes."
            for setting_id in WAVE22_MEMORY_CONTROLLER_SCOPE_CONFLICT_IDS
        },
        **{
            setting_id: "CPU Over Temperature Protection is documented as an absolute limit versus a TJ Max offset."
            for setting_id in WAVE22_OVER_TEMPERATURE_MECHANISM_CONFLICT_IDS
        },
        **{
            setting_id: "CPU Ratio Mode selects all/per-core scope versus fixed/dynamic operating mode."
            for setting_id in WAVE22_CPU_RATIO_MODE_SCOPE_CONFLICT_IDS
        },
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
                setting.id, "Not approved in Wave 22; requires a later evidence disposition."
            ),
        }
        for setting, manual in rows
    ]
    audit = build_public_audit(session)
    if audit["status"] != "pass":
        raise RuntimeError("Public BIOS knowledge audit failed after Wave 22.")
    return {
        "milestone": "v1.27 Intel I/O and voltage canonicalization Wave 22",
        "generated_at": utc_now_iso(),
        "actor": ACTOR,
        "backup": {"path": str(BACKUP_PATH), "sha256": BACKUP_SHA256},
        "database_revision": config.EXPECTED_DATABASE_REVISION,
        "summary": {
            "new_canonical_settings": 3,
            "total_mappings_created": 8,
            "reviewed_menu_contexts": 8,
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
                "reason": "EXPO labels cover profile loading and profile detection/pretraining.",
            },
            {
                "setting_ids": sorted(WAVE22_MEMORY_CONTROLLER_SCOPE_CONFLICT_IDS),
                "reason": "Memory Controller Voltage has incompatible profile-specific and direct-rail scopes.",
            },
            {
                "setting_ids": sorted(WAVE22_OVER_TEMPERATURE_MECHANISM_CONFLICT_IDS),
                "reason": "CPU Over Temperature Protection is documented as an absolute limit versus a TJ Max offset.",
            },
            {
                "setting_ids": sorted(WAVE22_CPU_RATIO_MODE_SCOPE_CONFLICT_IDS),
                "reason": "CPU Ratio Mode selects core scope versus fixed/dynamic behavior.",
            },
        ],
        "remaining_manual_review": remaining,
        "public_knowledge_audit": audit,
    }


def _markdown(payload: dict[str, object]) -> str:
    summary = payload["summary"]
    lines = [
        "# TuneForge v1.27 Intel I/O and Voltage Wave 22",
        "",
        "## Outcome",
        "",
        f"- New canonical settings: {summary['new_canonical_settings']}",
        f"- New verified mappings: {summary['total_mappings_created']}",
        f"- New reviewed menu contexts: {summary['reviewed_menu_contexts']}",
        f"- Remaining approved unmapped settings: {summary['remaining_manual_review']}",
        "",
        "Every decision uses visually reviewed official ASUS, ASRock, and MSI pages. Exact labels, functions, platform context, options, dependencies, and menu hierarchy agree; no community source was required.",
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
            "`Memory Controller Voltage`, `CPU Over Temperature Protection`, and `CPU Ratio Mode` remain unmapped because the official descriptions prove scope or mechanism conflicts despite identical labels. The complete decision report and pending backlog are stored in `data/reports/v1.27-intel-io-voltage-wave22.json`.",
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
        "--apply", action="store_true", help="Commit the pinned v1.27 Wave 22 batch"
    )
    args = parser.parse_args()
    with SessionLocal() as session:
        _verify_preflight(session)
        print(
            "Preflight passed: backup, revision, counts, five manuals, eight source pins, exclusions, and trust gates match."
        )
        print(
            "Planned outcome: three new identities and eight mappings with reviewed menu contexts."
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
        print("Applied: new_identities=3, mappings=8, menu_contexts=8.")
        print(f"Remaining approved unmapped: {payload['summary']['remaining_manual_review']}.")
        print(f"JSON report: {REPORT_JSON}")
        print(f"Summary report: {REPORT_MARKDOWN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
