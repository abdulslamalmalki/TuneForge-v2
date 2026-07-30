# ruff: noqa: E501
"""Apply TuneForge's pinned v1.28 DDR ODT and Intel CEP Wave 23.

Dry-run is the default. Nine identities and 29 approved Source Settings were
visually verified in official ASUS, ASRock, GIGABYTE, and MSI manuals.
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
BACKUP_PATH = ROOT / "data" / "backups" / "tuneforge-v1.28-pre-wave23.zip"
BACKUP_SHA256 = "fce2bfa87dcc3663cea8abbba599a627c85c5577eb5a72cbea8093b26c43bb24"
REPORT_JSON = ROOT / "data" / "reports" / "v1.28-ddr-odt-cep-wave23.json"
REPORT_MARKDOWN = ROOT / "docs" / "v1.28-ddr-odt-cep-wave23.md"
ACTOR = "v1.28-ddr-odt-cep-official-evidence-reviewer"
RELEASE_VERSION = "1.28.0"
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


def source(
    setting_id: int, manual_id: int, page: int, name: str, menu: str, evidence: str
) -> SourcePin:
    return SourcePin(setting_id, manual_id, page, name, menu, evidence)


MEMORY_RISK = "Changing DDR on-die termination or training sequence parameters can cause memory instability, training failure, data errors, or failure to boot."
CEP_RISK = "Disabling Current Excursion Protection can remove processor voltage/current safeguards and may increase instability, electrical stress, power, or temperature."
ASROCK_BUS = "Advanced > AMD CBS > UMC Common Options > DDR Options > DDR Timing Configuration > DDR Bus Configuration"
ASUS_AI_ODT = "Ai Tweaker > DRAM Timing Control > DRAM Signal Control > Processor ODT P0-P3 Page"
ASUS_CBS_BUS = "Advanced > AMD CBS > UMC Common Options > DDR Options > DDR Timing Configuration > DDR BUS Configuration"


def odt_identity(
    signal: str,
    group: str,
    asrock_id: int,
    asrock_page: int,
    asus_ai_id: int,
    asus_ai_page: int,
    asus_cbs_id: int | None,
    asus_cbs_page: int | None,
) -> IdentitySeed:
    canonical_name = f"{signal} ODT Group {group}"
    asrock_name = f"{signal} ODT Gruop{group}"
    asus_name = f"{signal} ODT Group{group}"
    sources = [
        source(
            asrock_id,
            2,
            asrock_page,
            asrock_name,
            f"{ASROCK_BUS} > {asus_name}",
            f"ASRock specifies {signal} ODT Group {group} with Auto, RTT_OFF, and matching RZQ ratios.",
        ),
        source(
            asus_ai_id,
            8,
            asus_ai_page,
            asus_name,
            f"{ASUS_AI_ODT} > {asus_name}",
            f"ASUS Ai Tweaker specifies {signal} ODT Group {group} with the same impedance choices.",
        ),
    ]
    if asus_cbs_id is not None and asus_cbs_page is not None:
        sources.append(
            source(
                asus_cbs_id,
                8,
                asus_cbs_page,
                asus_name,
                f"{ASUS_CBS_BUS} > {asus_name}",
                f"ASUS AMD CBS independently documents {signal} ODT Group {group} with the same choices.",
            )
        )
    return IdentitySeed(
        canonical_id=f"{signal.casefold()}-odt-group-{group.casefold()}",
        canonical_name=canonical_name,
        description=f"Selects the {signal} on-die termination impedance for DDR signal Group {group}.",
        rationale=f"Official ASRock and ASUS AMD manuals independently identify {signal} ODT Group {group}, place it under DDR bus/signal controls, and enumerate matching termination impedance choices.",
        uncertainty="ASRock's extracted source label contains the OCR spelling 'Gruop'; the rendered official page visibly says 'Group'.",
        category_id="memory",
        risk_level="high",
        risk_rationale=MEMORY_RISK,
        sources=tuple(sources),
    )


def cep_identity(domain: str, sources: tuple[SourcePin, ...]) -> IdentitySeed:
    return IdentitySeed(
        canonical_id=f"{domain.casefold()}-current-excursion-protection",
        canonical_name=f"{domain} Current Excursion Protection",
        description=f"Enables, disables, or automatically configures {domain} Current Excursion Protection support.",
        rationale=f"Official ASUS, MSI, and GIGABYTE Intel manuals use Enable, Support, or expanded labels for the same {domain} Current Excursion Protection control and independently describe enable/disable behavior.",
        uncertainty="ASUS additionally exposes Auto and mailbox implementation details; board and processor support determine availability.",
        category_id="cpu-performance",
        risk_level="high",
        risk_rationale=CEP_RISK,
        sources=sources,
    )


IDENTITIES = (
    odt_identity("CA", "A", 376, 56, 5851, 22, 6285, 67),
    odt_identity("CK", "A", 377, 56, 5853, 22, 6286, 67),
    odt_identity("CS", "A", 378, 56, 5855, 23, 6287, 67),
    odt_identity("CA", "B", 379, 56, 5857, 23, None, None),
    odt_identity("CK", "B", 380, 57, 5859, 23, 6289, 68),
    odt_identity("CS", "B", 381, 57, 5861, 23, 6290, 68),
    IdentitySeed(
        canonical_id="tx-burst-length",
        canonical_name="TX Burst Length",
        description="Selects the extended burst sequence length used for DDR write training.",
        rationale="Official ASRock and ASUS AMD manuals document the same write-training function and Auto, 1x, 2x, 4x, and 8x choices; ASUS prefixes the label with Nitro.",
        uncertainty="The Nitro prefix is ASUS presentation; the documented function and complete option set agree.",
        category_id="memory",
        risk_level="high",
        risk_rationale=MEMORY_RISK,
        sources=(
            source(
                438,
                2,
                61,
                "TX Burst Length",
                "Advanced > AMD CBS > UMC Common Options > DDR Options > DDR Training Options > TX Burst Length",
                "ASRock configures the extended sequence for DDR write training with Auto and 1x/2x/4x/8x choices.",
            ),
            source(
                6324,
                8,
                70,
                "Nitro TX Burst Length",
                "Advanced > AMD CBS > UMC Common Options > DDR Options > DDR Training Options > Nitro TX Burst Length",
                "ASUS documents the same extended write-training sequence and complete choice set.",
            ),
        ),
    ),
    cep_identity(
        "IA",
        (
            source(
                900,
                3,
                39,
                "IA CEP Enable",
                "Ai Tweaker > Internal CPU Power Management > IA CEP Enable",
                "ASUS Intel 800-series firmware enables, disables, or automatically configures IA CEP.",
            ),
            source(
                4809,
                5,
                44,
                "IA CEP Support",
                "OC > Advanced CPU Configuration > IA CEP Support",
                "MSI enables or disables IA Current Excursion Protection support.",
            ),
            source(
                6930,
                10,
                11,
                "IA CEP (Current Excursion Protection) (Note)",
                "Tweaker > Advanced CPU Settings > IA CEP (Current Excursion Protection)",
                "GIGABYTE enables or disables IA CEP.",
            ),
            source(
                7380,
                11,
                36,
                "IA CEP Enable",
                "Ai Tweaker > Internal CPU Power Management > IA CEP Enable",
                "ASUS Intel 600-series firmware documents IA CEP and its mailbox control.",
            ),
            source(
                8030,
                12,
                39,
                "IA CEP Enable",
                "Ai Tweaker > Internal CPU Power Management > IA CEP Enable",
                "ASUS Intel 700-series firmware documents the same IA CEP control.",
            ),
        ),
    ),
    cep_identity(
        "GT",
        (
            source(
                902,
                3,
                39,
                "GT CEP Enable",
                "Ai Tweaker > Internal CPU Power Management > GT CEP Enable",
                "ASUS Intel 800-series firmware enables, disables, or automatically configures GT CEP.",
            ),
            source(
                4810,
                5,
                44,
                "GT CEP Support",
                "OC > Advanced CPU Configuration > GT CEP Support",
                "MSI enables or disables GT Current Excursion Protection support.",
            ),
            source(
                6931,
                10,
                11,
                "GT CEP (Current Excursion Protection)(Note)",
                "Tweaker > Advanced CPU Settings > GT CEP (Current Excursion Protection)",
                "GIGABYTE enables or disables GT CEP.",
            ),
            source(
                7381,
                11,
                36,
                "GT CEP Enable",
                "Ai Tweaker > Internal CPU Power Management > GT CEP Enable",
                "ASUS Intel 600-series firmware documents GT CEP and its mailbox control.",
            ),
            source(
                8031,
                12,
                39,
                "GT CEP Enable",
                "Ai Tweaker > Internal CPU Power Management > GT CEP Enable",
                "ASUS Intel 700-series firmware documents the same GT CEP control.",
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
WAVE23_TX2D_CONTRADICTION_IDS = {447, 6333}
WAVE23_CA_ODT_LABEL_BODY_CONFLICT_IDS = {6288}
BASELINE_COUNTS = {
    CanonicalSetting: 311,
    CanonicalSettingAlias: 347,
    SettingCanonicalMapping: 1100,
    CanonicalMappingEvent: 1100,
    CanonicalIdentityEvent: 326,
    CanonicalSettingClassification: 311,
    CanonicalClassificationEvent: 583,
    SettingMenuContext: 897,
    SettingMenuContextEvent: 897,
    SettingMenuContextCitation: 971,
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
        | WAVE23_TX2D_CONTRADICTION_IDS
        | WAVE23_CA_ODT_LABEL_BODY_CONFLICT_IDS
    )


def _verify_preflight(session) -> None:
    if not BACKUP_PATH.is_file() or _sha256(BACKUP_PATH) != BACKUP_SHA256:
        raise RuntimeError("The verified pre-v1.28 backup is missing or changed.")
    if inspect_backup(BACKUP_PATH).database_revision != config.EXPECTED_DATABASE_REVISION:
        raise RuntimeError("The pre-v1.28 backup has an unexpected revision.")
    if (
        session.scalar(text("SELECT version_num FROM alembic_version"))
        != config.EXPECTED_DATABASE_REVISION
    ):
        raise RuntimeError("The live schema revision changed.")
    if session.scalar(text("PRAGMA integrity_check")) != "ok":
        raise RuntimeError("SQLite integrity_check failed before Wave 23.")
    if _unmapped_count(session) != 2918:
        raise RuntimeError(f"Expected 2,918 unmapped settings, found {_unmapped_count(session)}.")
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
    if len(IDENTITIES) != 9 or len(sources) != 29 or len({x.setting_id for x in sources}) != 29:
        raise RuntimeError("Wave 23 must contain nine identities and 29 unique records.")
    if _excluded().intersection({item.setting_id for item in sources}):
        raise RuntimeError("A known excluded record entered Wave 23.")
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
    return f"v1.28 Wave 23 reviewer decision; confidence={CONFIDENCE_BASIS_POINTS / 100:.2f}%. {seed.rationale} Official source: {manual.vendor} manual {manual.id}, page {item.page_number}, {manual.source_url}; reviewed menu path: {item.menu_path}. Remaining uncertainty: {seed.uncertainty}"


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
                note="v1.28 Wave 23 official-manual name, function, options, dependencies, platform, and menu hierarchy visually verified.",
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
        SettingCanonicalMapping: 29,
        CanonicalMappingEvent: 29,
        CanonicalIdentityEvent: 9,
        CanonicalSettingClassification: 9,
        CanonicalClassificationEvent: 18,
        SettingMenuContext: 29,
        SettingMenuContextEvent: 29,
        SettingMenuContextCitation: 29,
    }
    failures = [
        f"{model.__tablename__} append-only delta mismatch"
        for model, delta in deltas.items()
        if _count(session, model) != baseline[model] + delta
    ]
    if _unmapped_count(session) != 2889:
        failures.append(f"expected 2,889 unmapped, found {_unmapped_count(session)}")
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
        raise RuntimeError("Wave 23 verification failed:\n- " + "\n- ".join(failures))


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
            setting_id: "Official manuals contradict whether multiplier value 1 multiplies the TX2D voltage step by 16."
            for setting_id in WAVE23_TX2D_CONTRADICTION_IDS
        },
        **{
            setting_id: "The ASUS rendered label says CA ODT GroupB while its body says CS ODT; the record is unsafe to map."
            for setting_id in WAVE23_CA_ODT_LABEL_BODY_CONFLICT_IDS
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
                setting.id, "Not approved in Wave 23; requires a later evidence disposition."
            ),
        }
        for setting, manual in rows
    ]
    audit = build_public_audit(session)
    if audit["status"] != "pass":
        raise RuntimeError("Public BIOS knowledge audit failed after Wave 23.")
    # The batch is applied before release metadata is bumped, so pin the report
    # to the milestone it creates rather than the previous runtime version.
    audit["app_version"] = RELEASE_VERSION
    return {
        "milestone": "v1.28 DDR ODT and Intel CEP canonicalization Wave 23",
        "generated_at": utc_now_iso(),
        "actor": ACTOR,
        "backup": {"path": str(BACKUP_PATH), "sha256": BACKUP_SHA256},
        "database_revision": config.EXPECTED_DATABASE_REVISION,
        "summary": {
            "new_canonical_settings": 9,
            "total_mappings_created": 29,
            "reviewed_menu_contexts": 29,
            "remaining_manual_review": len(remaining),
        },
        "official_manuals": [asdict(pin) for pin in MANUALS],
        "approved_decisions": decisions,
        "intentional_exclusions": [
            {
                "setting_ids": sorted(WAVE23_TX2D_CONTRADICTION_IDS),
                "reason": "Official TX2D multiplier behavior conflicts.",
            },
            {
                "setting_ids": sorted(WAVE23_CA_ODT_LABEL_BODY_CONFLICT_IDS),
                "reason": "Official CA/CS label-body conflict.",
            },
            {
                "setting_ids": sorted(
                    _excluded()
                    - WAVE23_TX2D_CONTRADICTION_IDS
                    - WAVE23_CA_ODT_LABEL_BODY_CONFLICT_IDS
                ),
                "reason": "Previously preserved ambiguity, collision, rejection, or evidence gap.",
            },
        ],
        "remaining_manual_review": remaining,
        "public_knowledge_audit": audit,
    }


def _markdown(payload: dict[str, object]) -> str:
    summary = payload["summary"]
    lines = [
        "# TuneForge v1.28 DDR ODT and Intel CEP Wave 23",
        "",
        "## Outcome",
        "",
        f"- New canonical settings: {summary['new_canonical_settings']}",
        f"- New verified mappings: {summary['total_mappings_created']}",
        f"- New reviewed menu contexts: {summary['reviewed_menu_contexts']}",
        f"- Remaining approved unmapped settings: {summary['remaining_manual_review']}",
        "",
        "Every decision uses visually reviewed official ASUS, ASRock, GIGABYTE, and MSI pages. Names or aliases, functions, options, platform context, dependencies, and menu hierarchy agree; no community source was required.",
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
            "The TX2D voltage-step multiplier pair remains unmapped because the official manuals directly disagree on value 1. ASUS source 6288 remains unmapped because its CA GroupB label has a CS ODT body. The complete report is stored in `data/reports/v1.28-ddr-odt-cep-wave23.json`.",
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
        "--apply", action="store_true", help="Commit the pinned v1.28 Wave 23 batch"
    )
    args = parser.parse_args()
    with SessionLocal() as session:
        _verify_preflight(session)
        print(
            "Preflight passed: backup, revision, counts, seven manuals, 29 source pins, exclusions, and trust gates match."
        )
        print("Planned outcome: nine new identities and 29 mappings with reviewed menu contexts.")
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
        print("Applied: new_identities=9, mappings=29, menu_contexts=29.")
        print(f"Remaining approved unmapped: {payload['summary']['remaining_manual_review']}.")
        print(f"JSON report: {REPORT_JSON}")
        print(f"Summary report: {REPORT_MARKDOWN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
