# ruff: noqa: E501
"""Apply TuneForge's pinned v1.19 Thunderbolt-resources Wave 14.

Dry-run is the default. Eight identities and 23 approved Source Settings were
visually verified in official ASUS and MSI manuals.
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
BACKUP_PATH = ROOT / "data" / "backups" / "tuneforge-v1.19-pre-wave14.zip"
BACKUP_SHA256 = "5255a3b42c9e067245155b565864f8be00330269d6f4b7db8427d3abb8df0c3c"
REPORT_JSON = ROOT / "data" / "reports" / "v1.19-thunderbolt-resources-wave14.json"
REPORT_MARKDOWN = ROOT / "docs" / "v1.19-thunderbolt-resources-wave14.md"
ACTOR = "v1.19-thunderbolt-resources-official-evidence-reviewer"
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


IDENTITIES = (
    IdentitySeed(
        "extra-bus-reserved",
        "Extra Bus Reserved",
        "Selects the extra bus reservation and root-port type for one-port or two-port discrete Thunderbolt hosts.",
        "ASUS and MSI use the exact normalized name in discrete Thunderbolt controller configuration and document the same 56-bus one-port and 106-bus two-port choices.",
        "The reviewed manuals do not describe allocation behavior outside the listed one-port and two-port host configurations.",
        "peripheral-io",
        "moderate",
        "Changing Thunderbolt bus reservations can affect controller enumeration and PCIe address-space allocation.",
        (
            src(
                4576,
                5,
                21,
                "Extra Bus Reserved",
                "Settings > Advanced > Intel Thunderbolt > Discrete Thunderbolt(TM) Configuration > DTBT Controller 0 Configuration > Extra Bus Reserved",
                "The MSI manual assigns 56 buses to a one-port host or 106 buses to a two-port host.",
            ),
            src(
                7564,
                11,
                59,
                "Extra Bus Reserved",
                "Advanced > Thunderbolt(TM) Configuration > Discrete Thunderbolt(TM) Configuration > DTBT Controller 0 Configuration > Extra Bus Reserved",
                "The ASUS manual selects the TBT root-port type using 56 buses for one port or 106 buses for two ports.",
            ),
            src(
                8213,
                12,
                63,
                "Extra Bus Reserved",
                "Advanced > Thunderbolt(TM) Configuration > Discrete Thunderbolt(TM) Configuration > DTBT Controller 0 Configuration > Extra Bus Reserved",
                "The ASUS manual selects the TBT root-port type using 56 buses for one port or 106 buses for two ports.",
            ),
        ),
    ),
    IdentitySeed(
        "memory-alignment",
        "Memory Alignment",
        "Sets memory-alignment bits for the discrete Thunderbolt root bridge.",
        "ASUS and MSI use the exact normalized name under the same discrete Thunderbolt controller resource block; ASUS documents the 0-31 alignment-bit range and MSI documents the same alignment function.",
        "MSI does not state the numeric range on the reviewed page.",
        "peripheral-io",
        "moderate",
        "Changing root-bridge alignment can affect Thunderbolt resource allocation and device enumeration.",
        (
            src(
                4578,
                5,
                21,
                "Memory Alignment",
                "Settings > Advanced > Intel Thunderbolt > Discrete Thunderbolt(TM) Configuration > DTBT Controller 0 Configuration > Memory Alignment",
                "The MSI manual sets memory alignment for the discrete Thunderbolt controller.",
            ),
            src(
                7566,
                11,
                59,
                "Memory Alignment",
                "Advanced > Thunderbolt(TM) Configuration > Discrete Thunderbolt(TM) Configuration > DTBT Controller 0 Configuration > Memory Alignment",
                "The ASUS manual sets memory-alignment bits from 0 through 31.",
            ),
            src(
                8215,
                12,
                63,
                "Memory Alignment",
                "Advanced > Thunderbolt(TM) Configuration > Discrete Thunderbolt(TM) Configuration > DTBT Controller 0 Configuration > Memory Alignment",
                "The ASUS manual sets memory-alignment bits from 0 through 31.",
            ),
        ),
    ),
    IdentitySeed(
        "pmemory-alignment",
        "PMemory Alignment",
        "Sets prefetchable-memory alignment bits for the discrete Thunderbolt root bridge.",
        "ASUS and MSI use the exact normalized name under the same controller resource block and identify the same prefetchable-memory alignment function.",
        "MSI does not state the numeric range on the reviewed page.",
        "peripheral-io",
        "moderate",
        "Changing prefetchable-memory alignment can affect Thunderbolt resource allocation and device enumeration.",
        (
            src(
                4580,
                5,
                21,
                "PMemory Alignment",
                "Settings > Advanced > Intel Thunderbolt > Discrete Thunderbolt(TM) Configuration > DTBT Controller 0 Configuration > PMemory Alignment",
                "The MSI manual sets prefetchable-memory alignment for the discrete Thunderbolt controller.",
            ),
            src(
                7568,
                11,
                59,
                "PMemory Alignment",
                "Advanced > Thunderbolt(TM) Configuration > Discrete Thunderbolt(TM) Configuration > DTBT Controller 0 Configuration > PMemory Alignment",
                "The ASUS manual sets prefetchable-memory alignment bits from 0 through 31.",
            ),
            src(
                8217,
                12,
                63,
                "PMemory Alignment",
                "Advanced > Thunderbolt(TM) Configuration > Discrete Thunderbolt(TM) Configuration > DTBT Controller 0 Configuration > PMemory Alignment",
                "The ASUS manual sets prefetchable-memory alignment bits from 0 through 31.",
            ),
        ),
    ),
    IdentitySeed(
        "reserved-io",
        "Reserved I/O",
        "Sets reserved I/O resources for the discrete Thunderbolt root bridge.",
        "ASUS and MSI use the exact normalized name under the same discrete Thunderbolt controller resource block and describe the same reserved-I/O function.",
        "MSI does not state the numeric range on the reviewed page.",
        "peripheral-io",
        "moderate",
        "Changing reserved I/O can affect Thunderbolt controller enumeration and address allocation.",
        (
            src(
                4581,
                5,
                21,
                "Reserved I/O",
                "Settings > Advanced > Intel Thunderbolt > Discrete Thunderbolt(TM) Configuration > DTBT Controller 0 Configuration > Reserved I/O",
                "The MSI manual sets reserved I/O for the discrete Thunderbolt controller.",
            ),
            src(
                7569,
                11,
                59,
                "Reserved I/O",
                "Advanced > Thunderbolt(TM) Configuration > Discrete Thunderbolt(TM) Configuration > DTBT Controller 0 Configuration > Reserved I/O",
                "The ASUS manual permits reserved-I/O values from 0 through 60.",
            ),
            src(
                8218,
                12,
                63,
                "Reserved I/O",
                "Advanced > Thunderbolt(TM) Configuration > Discrete Thunderbolt(TM) Configuration > DTBT Controller 0 Configuration > Reserved I/O",
                "The ASUS manual permits reserved-I/O values from 0 through 60.",
            ),
        ),
    ),
    IdentitySeed(
        "reserved-memory",
        "Reserved Memory",
        "Sets reserved non-prefetchable memory for the discrete Thunderbolt root bridge.",
        "ASUS and MSI use the exact normalized name in the same controller resource block and describe reserved memory for the root bridge.",
        "MSI does not state the numeric range on the reviewed page.",
        "peripheral-io",
        "moderate",
        "Changing reserved memory can affect Thunderbolt controller enumeration and memory-map allocation.",
        (
            src(
                4577,
                5,
                21,
                "Reserved Memory",
                "Settings > Advanced > Intel Thunderbolt > Discrete Thunderbolt(TM) Configuration > DTBT Controller 0 Configuration > Reserved Memory",
                "The MSI manual sets reserved memory for the root bridge.",
            ),
            src(
                7565,
                11,
                59,
                "Reserved Memory",
                "Advanced > Thunderbolt(TM) Configuration > Discrete Thunderbolt(TM) Configuration > DTBT Controller 0 Configuration > Reserved Memory",
                "The ASUS manual sets reserved non-prefetchable memory for the root bridge from 1 through 4096.",
            ),
            src(
                8214,
                12,
                63,
                "Reserved Memory",
                "Advanced > Thunderbolt(TM) Configuration > Discrete Thunderbolt(TM) Configuration > DTBT Controller 0 Configuration > Reserved Memory",
                "The ASUS manual sets reserved non-prefetchable memory for the root bridge from 1 through 4096.",
            ),
        ),
    ),
    IdentitySeed(
        "reserved-pmemory",
        "Reserved PMemory",
        "Sets reserved prefetchable memory for the discrete Thunderbolt root bridge.",
        "ASUS and MSI use the exact normalized name in the same controller resource block and describe reserved prefetchable memory for the root bridge.",
        "MSI does not state the numeric range on the reviewed page.",
        "peripheral-io",
        "moderate",
        "Changing reserved prefetchable memory can affect Thunderbolt controller enumeration and memory-map allocation.",
        (
            src(
                4579,
                5,
                21,
                "Reserved PMemory",
                "Settings > Advanced > Intel Thunderbolt > Discrete Thunderbolt(TM) Configuration > DTBT Controller 0 Configuration > Reserved PMemory",
                "The MSI manual sets reserved prefetchable memory for the root bridge.",
            ),
            src(
                7567,
                11,
                59,
                "Reserved PMemory",
                "Advanced > Thunderbolt(TM) Configuration > Discrete Thunderbolt(TM) Configuration > DTBT Controller 0 Configuration > Reserved PMemory",
                "The ASUS manual sets reserved prefetchable memory for the root bridge from 1 through 4096.",
            ),
            src(
                8216,
                12,
                63,
                "Reserved PMemory",
                "Advanced > Thunderbolt(TM) Configuration > Discrete Thunderbolt(TM) Configuration > DTBT Controller 0 Configuration > Reserved PMemory",
                "The ASUS manual sets reserved prefetchable memory for the root bridge from 1 through 4096.",
            ),
        ),
    ),
    IdentitySeed(
        "windows-10-thunderbolt-support",
        "Windows 10 Thunderbolt Support",
        "Selects Windows 10 native Thunderbolt support, including RTD3 support where enabled.",
        "ASUS and MSI use the exact normalized name in discrete Thunderbolt configuration and document matching disabled, native-support, and native-support-plus-RTD3 behavior.",
        "Behavior on operating systems other than Windows 10 is not documented on the reviewed pages.",
        "peripheral-io",
        "moderate",
        "Changing native Thunderbolt and RTD3 support can affect operating-system device discovery and controller power management.",
        (
            src(
                4572,
                5,
                21,
                "Windows 10 Thunderbolt Support",
                "Settings > Advanced > Intel Thunderbolt > Discrete Thunderbolt(TM) Configuration > Windows 10 Thunderbolt Support",
                "The MSI manual distinguishes no native support, native support, and native support with RTD3.",
            ),
            src(
                7560,
                11,
                59,
                "Windows 10 Thunderbolt Support",
                "Advanced > Thunderbolt(TM) Configuration > Discrete Thunderbolt(TM) Configuration > Windows 10 Thunderbolt Support",
                "The ASUS manual distinguishes no native support, native support, and native support with RTD3.",
            ),
            src(
                8209,
                12,
                62,
                "Windows 10 Thunderbolt Support",
                "Advanced > Thunderbolt(TM) Configuration > Discrete Thunderbolt(TM) Configuration > Windows 10 Thunderbolt Support",
                "The ASUS manual distinguishes no native support, native support, and native support with RTD3.",
            ),
        ),
    ),
    IdentitySeed(
        "dtbt-controller-0",
        "DTBT Controller 0",
        "Enables or disables discrete Thunderbolt controller 0.",
        "ASUS and MSI use the exact normalized name under discrete Thunderbolt configuration and both document the same enable-or-disable controller function.",
        "The reviewed manuals do not describe systems with additional discrete Thunderbolt controllers.",
        "peripheral-io",
        "moderate",
        "Disabling the controller removes firmware and operating-system access to devices attached through discrete Thunderbolt controller 0.",
        (
            src(
                4574,
                5,
                21,
                "DTBT Controller 0",
                "Settings > Advanced > Intel Thunderbolt > Discrete Thunderbolt(TM) Configuration > DTBT Controller 0 Configuration > DTBT Controller 0",
                "The MSI manual enables or disables discrete Thunderbolt controller 0.",
            ),
            src(
                8211,
                12,
                63,
                "DTBT Controller 0",
                "Advanced > Thunderbolt(TM) Configuration > Discrete Thunderbolt(TM) Configuration > DTBT Controller 0 Configuration > DTBT Controller 0",
                "The ASUS manual enables or disables discrete Thunderbolt controller 0.",
            ),
        ),
    ),
)

KNOWN_AMBIGUOUS_IDS = {572, 6111, 6690, 7392, 7395, 7657, 8320}
KNOWN_COLLISION_IDS = {47, 77, 435, 491, 5137, 5284, 5292, 6158, 6161, 6321, 6378}
KNOWN_REJECTED_DUPLICATE_IDS = {4901}
BASELINE_COUNTS = {
    CanonicalSetting: 209,
    CanonicalSettingAlias: 245,
    SettingCanonicalMapping: 873,
    CanonicalMappingEvent: 873,
    CanonicalIdentityEvent: 224,
    CanonicalSettingClassification: 209,
    CanonicalClassificationEvent: 379,
    SettingMenuContext: 670,
    SettingMenuContextEvent: 670,
    SettingMenuContextCitation: 744,
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
        raise RuntimeError("The verified pre-v1.19 backup is missing or changed.")
    if inspect_backup(BACKUP_PATH).database_revision != config.EXPECTED_DATABASE_REVISION:
        raise RuntimeError("The pre-v1.19 backup has an unexpected revision.")
    if (
        session.scalar(text("SELECT version_num FROM alembic_version"))
        != config.EXPECTED_DATABASE_REVISION
    ):
        raise RuntimeError("The live schema revision changed.")
    if session.scalar(text("PRAGMA integrity_check")) != "ok":
        raise RuntimeError("SQLite integrity_check failed before Wave 14.")
    if _unmapped_count(session) != 3145:
        raise RuntimeError(f"Expected 3,145 unmapped settings, found {_unmapped_count(session)}.")
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
    if len(IDENTITIES) != 8 or len(sources) != 23 or len({x.setting_id for x in sources}) != 23:
        raise RuntimeError("Wave 14 must contain eight identities and 23 unique records.")
    excluded = KNOWN_AMBIGUOUS_IDS | KNOWN_COLLISION_IDS | KNOWN_REJECTED_DUPLICATE_IDS
    if excluded.intersection({item.setting_id for item in sources}):
        raise RuntimeError("A known excluded record entered Wave 14.")
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
    return f"v1.19 Wave 14 reviewer decision; confidence={CONFIDENCE_BASIS_POINTS / 100:.2f}%. {seed.rationale} Official source: {manual.vendor} manual {manual.id}, page {item.page_number}, {manual.source_url}; reviewed menu path: {item.menu_path}. Remaining uncertainty: {seed.uncertainty}"


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
                note="v1.19 Wave 14 official-manual label, behavior, options, and menu hierarchy visually verified.",
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
        SettingCanonicalMapping: 23,
        CanonicalMappingEvent: 23,
        CanonicalIdentityEvent: 8,
        CanonicalSettingClassification: 8,
        CanonicalClassificationEvent: 16,
        SettingMenuContext: 23,
        SettingMenuContextEvent: 23,
        SettingMenuContextCitation: 23,
    }
    failures = [
        f"{model.__tablename__} append-only delta mismatch"
        for model, delta in deltas.items()
        if _count(session, model) != baseline[model] + delta
    ]
    if _unmapped_count(session) != 3122:
        failures.append(f"expected 3,122 unmapped, found {_unmapped_count(session)}")
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
        raise RuntimeError("Wave 14 verification failed:\n- " + "\n- ".join(failures))


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
            "reason": "Not approved in Wave 14; requires a later evidence disposition.",
        }
        for setting, manual in rows
    ]
    audit = build_public_audit(session)
    if audit["status"] != "pass":
        raise RuntimeError("Public BIOS knowledge audit failed after Wave 14.")
    return {
        "milestone": "v1.19 Thunderbolt-resources canonicalization Wave 14",
        "generated_at": utc_now_iso(),
        "actor": ACTOR,
        "backup": {"path": str(BACKUP_PATH), "sha256": BACKUP_SHA256},
        "database_revision": config.EXPECTED_DATABASE_REVISION,
        "summary": {
            "new_canonical_settings": 8,
            "total_mappings_created": 23,
            "reviewed_menu_contexts": 23,
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
        "# TuneForge v1.19 Thunderbolt Resources Wave 14",
        "",
        "## Outcome",
        "",
        f"- New canonical settings: {summary['new_canonical_settings']}",
        f"- New verified mappings: {summary['total_mappings_created']}",
        f"- New reviewed menu contexts: {summary['reviewed_menu_contexts']}",
        f"- Remaining approved unmapped settings: {summary['remaining_manual_review']}",
        "",
        "Every decision uses visually reviewed official ASUS and MSI pages. Name, function, options, and menu context agree; no community source was required.",
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
            "Known semantic collisions and records lacking equivalent official evidence remain unmapped. `TBT Host Router` is intentionally excluded because its ASUS and MSI option semantics conflict. The complete decision report and pending backlog are stored in `data/reports/v1.19-thunderbolt-resources-wave14.json`.",
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
        "--apply", action="store_true", help="Commit the pinned v1.19 Wave 14 batch"
    )
    args = parser.parse_args()
    with SessionLocal() as session:
        _verify_preflight(session)
        print(
            "Preflight passed: backup, revision, counts, three manuals, 23 source pins, exclusions, and trust gates match."
        )
        print("Planned outcome: eight new identities and 23 mappings with reviewed menu contexts.")
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
        print("Applied: new_identities=8, mappings=23, menu_contexts=23.")
        print(f"Remaining approved unmapped: {payload['summary']['remaining_manual_review']}.")
        print(f"JSON report: {REPORT_JSON}")
        print(f"Summary report: {REPORT_MARKDOWN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
