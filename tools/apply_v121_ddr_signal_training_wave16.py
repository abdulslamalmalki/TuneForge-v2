# ruff: noqa: E501
"""Apply TuneForge's pinned v1.21 DDR signal/training Wave 16.

Dry-run is the default. Twenty-four identities and 58 approved Source Settings
were visually verified in official ASRock, ASUS, and MSI manuals.
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
BACKUP_PATH = ROOT / "data" / "backups" / "tuneforge-v1.21-pre-wave16.zip"
BACKUP_SHA256 = "4a80df739dc5e1b33ff9172b78c2f9d4806c6c72cb153721a517762f1ddc63bf"
REPORT_JSON = ROOT / "data" / "reports" / "v1.21-ddr-signal-training-wave16.json"
REPORT_MARKDOWN = ROOT / "docs" / "v1.21-ddr-signal-training-wave16.md"
ACTOR = "v1.21-ddr-signal-training-official-evidence-reviewer"
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


def pin(
    setting_id: int,
    manual_id: int,
    page: int,
    name: str,
    menu_path: str,
    evidence: str,
) -> SourcePin:
    return SourcePin(setting_id, manual_id, page, name, menu_path, evidence)


MEMORY_RISK = "Changing memory signal impedance, drive strength, or training parameters can cause failed training, instability, data corruption, or require firmware recovery."


def identity(
    canonical_id: str,
    name: str,
    description: str,
    equivalence: str,
    uncertainty: str,
    sources: tuple[SourcePin, ...],
) -> IdentitySeed:
    vendors = (
        "ASRock and ASUS"
        if {item.manual_id for item in sources} == {2, 8}
        else "The reviewed official vendors"
    )
    return IdentitySeed(
        canonical_id,
        name,
        description,
        f"{vendors} use the exact normalized name in corresponding DDR signal or training controls; official descriptions and options agree that {equivalence}.",
        uncertainty,
        "memory",
        "high",
        MEMORY_RISK,
        sources,
    )


ASROCK_BUS = "Advanced > AMD CBS > UMC Common Options > DDR Options > DDR Bus Configuration"
ASROCK_TRAINING = "Advanced > AMD CBS > UMC Common Options > DDR Options > DDR Training Options"
ASUS_CBS_BUS = "Advanced > AMD CBS > UMC Common Options > DDR Bus Configuration"
ASUS_PBS_BUS = "Advanced > AMD PBS > DDR and Infinity Fabric Frequency/Timings > DDR Options > DDR Bus Configuration"
ASUS_CBS_TRAINING = "Advanced > AMD CBS > UMC Common Options > DDR Options > DDR Training Options"
ASUS_TWEAKS = "Advanced > AMD CBS > UMC Common Options > Additional Memory Tweaks"
MSI_BUS = "OC > Advanced DRAM Configuration > DDR Bus Configuration"


def p(
    setting_id: int,
    manual_id: int,
    page: int,
    name: str,
    parent: str,
    evidence: str,
) -> SourcePin:
    return pin(setting_id, manual_id, page, name, f"{parent} > {name}", evidence)


def rank_identity(
    rank: str,
    signal: str,
    direction: str,
    asrock_id: int,
    asrock_page: int,
    asus_id: int,
    asus_page: int,
) -> IdentitySeed:
    if signal == "odt":
        name = f"Processor ODT Impedance Pull {direction} {rank}"
        canonical_id = f"processor-odt-impedance-pull-{direction.lower()}-{rank.lower()}"
        description = f"Selects processor on-die termination pull-{direction.lower()} impedance for memory rank {rank}."
        function = (
            f"the option selects processor ODT pull-{direction.lower()} impedance for rank {rank}"
        )
    else:
        name = f"Processor DQ drive strengths Pull {direction} {rank}"
        canonical_id = f"processor-dq-drive-strengths-pull-{direction.lower()}-{rank.lower()}"
        description = (
            f"Selects processor DQ pull-{direction.lower()} drive strength for memory rank {rank}."
        )
        function = f"the option selects processor DQ pull-{direction.lower()} drive strength for rank {rank}"
    evidence = f"The official ASRock and ASUS manuals both document that {function}."
    asus_parent = (
        f"Ai Tweaker > DRAM Timing Control > DRAM Signal Control > Processor ODT {rank} Page"
    )
    return identity(
        canonical_id,
        name,
        description,
        function,
        "Available impedance values and automatic selection remain firmware-dependent.",
        (
            p(asrock_id, 2, asrock_page, name, ASROCK_BUS, evidence),
            p(asus_id, 8, asus_page, name, asus_parent, evidence),
        ),
    )


RANK_IDENTITIES = tuple(
    rank_identity(*item)
    for item in (
        ("P0", "odt", "Up", 382, 57, 5865, 23),
        ("P0", "odt", "Down", 383, 57, 5866, 23),
        ("P0", "dq", "Up", 384, 57, 5867, 23),
        ("P0", "dq", "Down", 385, 57, 5868, 23),
        ("P1", "odt", "Up", 393, 58, 5877, 24),
        ("P1", "odt", "Down", 394, 58, 5878, 24),
        ("P1", "dq", "Up", 395, 58, 5879, 24),
        ("P1", "dq", "Down", 396, 58, 5880, 24),
        ("P3", "odt", "Up", 404, 58, 5901, 26),
        ("P3", "odt", "Down", 405, 58, 5902, 26),
        ("P3", "dq", "Up", 406, 58, 5903, 26),
        ("P3", "dq", "Down", 407, 58, 5904, 26),
    )
)


IDENTITIES = (
    identity(
        "processor-ca-drive-strengths",
        "Processor CA drive strengths",
        "Selects processor command/address signal drive strength.",
        "the option selects drive strength for processor CA signals",
        "The reviewed manuals expose overlapping, but not identical, impedance lists.",
        (
            p(
                5411,
                6,
                37,
                "Processor CA drive strengths",
                MSI_BUS,
                "MSI selects drive strength for all CA0-13 I/Os.",
            ),
            p(
                6281,
                8,
                67,
                "Processor CA drive strengths",
                ASUS_CBS_BUS,
                "ASUS selects processor CA drive strength.",
            ),
            p(
                6503,
                8,
                84,
                "Processor CA drive strengths",
                ASUS_PBS_BUS,
                "ASUS selects processor CA drive strength in the AMD PBS copy.",
            ),
        ),
    ),
    identity(
        "processor-cs-drive-strengths",
        "Processor CS drive strengths",
        "Selects processor chip-select signal drive strength.",
        "the option selects processor CS drive strength",
        "One ASRock option token renders as Deferred; the common impedance choices and function agree.",
        (
            p(
                375,
                2,
                56,
                "Processor CS drive strengths",
                ASROCK_BUS,
                "ASRock selects processor CS drive strength.",
            ),
            p(
                6283,
                8,
                67,
                "Processor CS drive strengths",
                ASUS_CBS_BUS,
                "ASUS selects processor CS drive strength.",
            ),
            p(
                6499,
                8,
                84,
                "Processor CS drive strengths",
                ASUS_PBS_BUS,
                "ASUS selects processor CS drive strength in the AMD PBS copy.",
            ),
        ),
    ),
    identity(
        "processor-dq-drive-strengths",
        "Processor DQ drive strengths",
        "Selects drive strength for processor DQ and DMI data signals.",
        "the option selects drive strength for all DQ and DMI I/Os",
        "The available impedance values remain firmware-dependent.",
        (
            p(
                5412,
                6,
                37,
                "Processor DQ drive strengths",
                MSI_BUS,
                "MSI selects drive strength for all DQ and DMI I/Os.",
            ),
            p(
                6505,
                8,
                84,
                "Processor DQ drive strengths",
                ASUS_PBS_BUS,
                "ASUS selects drive strength for all DQ and DMI I/Os.",
            ),
        ),
    ),
    identity(
        "processor-odt-impedance",
        "Processor ODT Impedance",
        "Selects processor on-die termination impedance for DBYTE I/O signals.",
        "the option selects ODT impedance for all DBYTE I/Os",
        "The available impedance values remain firmware-dependent.",
        (
            p(
                5413,
                6,
                37,
                "Processor ODT Impedance",
                MSI_BUS,
                "MSI selects ODT impedance for all DBYTE I/Os.",
            ),
            p(
                6506,
                8,
                84,
                "Processor ODT impedance",
                ASUS_PBS_BUS,
                "ASUS selects impedance for all DBYTE I/Os.",
            ),
        ),
    ),
    *RANK_IDENTITIES,
    identity(
        "rx2d-trainopt",
        "RX2D_TrainOpt",
        "Selects automatic or manual configuration of two-dimensional memory receive training.",
        "the option selects automatic or manual RX2D training configuration",
        "The exact training algorithm is not documented.",
        (
            p(
                439,
                2,
                61,
                "RX2D_TrainOpt",
                ASROCK_TRAINING,
                "ASRock configures RX2D training options.",
            ),
            p(
                6325,
                8,
                70,
                "RX2D_TrainOpt",
                ASUS_CBS_TRAINING,
                "ASUS selects Auto or Manual RX2D training.",
            ),
            p(
                6517,
                8,
                85,
                "RX2D_TrainOpt",
                ASUS_TWEAKS,
                "ASUS selects Auto or Manual RX2D training in Additional Memory Tweaks.",
            ),
        ),
    ),
    identity(
        "rx2d-dfe",
        "RX2D_DFE",
        "Controls whether decision-feedback equalization is forced during two-dimensional receive training.",
        "the option forces receive DFE on or off",
        "One ASUS copy documents a dependency on Runtime Reduction.",
        (
            p(
                440,
                2,
                61,
                "RX2D_DFE",
                f"{ASROCK_TRAINING} > RX2D_TrainOpt",
                "ASRock forces receive DFE on or off.",
            ),
            p(
                6326,
                8,
                70,
                "RX2D_DFE",
                f"{ASUS_CBS_TRAINING} > RX2D_TrainOpt",
                "ASUS forces receive DFE on or off.",
            ),
            p(
                6518,
                8,
                85,
                "RX2D_DFE",
                f"{ASUS_TWEAKS} > RX2D_TrainOpt",
                "ASUS forces receive DFE on or off and documents the Runtime Reduction dependency.",
            ),
        ),
    ),
    identity(
        "rx2d-voltage-step-size",
        "RX2D Voltage Step Size (2^n)",
        "Selects the DAC voltage-step spacing used during two-dimensional receive training.",
        "values zero through three select one, two, four, or eight DAC settings between checks",
        "The electrical voltage represented by one DAC step is not stated.",
        (
            p(
                441,
                2,
                61,
                "RX2D Voltage Step Size (2^n)",
                f"{ASROCK_TRAINING} > RX2D_TrainOpt",
                "ASRock maps 0-3 to 1, 2, 4, or 8 DAC settings.",
            ),
            p(
                6327,
                8,
                70,
                "RX2D Voltage Step Size (2^n)",
                f"{ASUS_CBS_TRAINING} > RX2D_TrainOpt",
                "ASUS maps 0-3 to 1, 2, 4, or 8 DAC settings.",
            ),
            p(
                6519,
                8,
                85,
                "RX2D Voltage Step Size (2^n)",
                f"{ASUS_TWEAKS} > RX2D_TrainOpt",
                "ASUS maps 0-3 to 1, 2, 4, or 8 DAC settings in Additional Memory Tweaks.",
            ),
        ),
    ),
    identity(
        "rx-dfe-taps",
        "RX DFE Taps",
        "Selects the number of receive decision-feedback-equalization taps.",
        "the option selects the receive DFE tap count",
        "An ASUS copy states that the value applies only when RX2D_DFE is enabled.",
        (
            p(
                448,
                2,
                62,
                "RX DFE Taps",
                ASROCK_TRAINING,
                "ASRock selects the number of RX DFE taps.",
            ),
            p(
                6335,
                8,
                71,
                "RX DFE Taps",
                ASUS_CBS_TRAINING,
                "ASUS selects one through four RX DFE taps.",
            ),
            p(
                6527,
                8,
                86,
                "RX DFE Taps",
                ASUS_TWEAKS,
                "ASUS selects one through four RX DFE taps and documents the RX2D_DFE dependency.",
            ),
        ),
    ),
    identity(
        "tx2d-trainopt",
        "TX2D_TrainOpt",
        "Selects automatic or manual configuration of two-dimensional memory transmit training.",
        "the option selects automatic or manual TX2D training configuration",
        "The exact training algorithm is not documented.",
        (
            p(
                443,
                2,
                61,
                "TX2D_TrainOpt",
                ASROCK_TRAINING,
                "ASRock configures TX2D training options.",
            ),
            p(
                6329,
                8,
                70,
                "TX2D_TrainOpt",
                ASUS_CBS_TRAINING,
                "ASUS selects Auto or Manual TX2D training.",
            ),
            p(
                6521,
                8,
                85,
                "TX2D_TrainOpt",
                ASUS_TWEAKS,
                "ASUS selects Auto or Manual TX2D training in Additional Memory Tweaks.",
            ),
        ),
    ),
    identity(
        "tx2d-dfe",
        "TX2D_DFE",
        "Controls whether decision-feedback equalization is forced during two-dimensional transmit training.",
        "the option forces transmit DFE on or off",
        "Automatic behavior remains firmware-dependent.",
        (
            p(
                444,
                2,
                62,
                "TX2D_DFE",
                f"{ASROCK_TRAINING} > TX2D_TrainOpt",
                "ASRock forces transmit DFE on or off.",
            ),
            p(
                6330,
                8,
                71,
                "TX2D_DFE",
                f"{ASUS_CBS_TRAINING} > TX2D_TrainOpt",
                "ASUS enables or disables transmit DFE.",
            ),
            p(
                6522,
                8,
                86,
                "TX2D_DFE",
                f"{ASUS_TWEAKS} > TX2D_TrainOpt",
                "ASUS enables or disables transmit DFE in Additional Memory Tweaks.",
            ),
        ),
    ),
    identity(
        "tx2d-voltage-step-size",
        "TX2D Voltage Step Size (2^n)",
        "Selects the DAC voltage-step spacing used during two-dimensional transmit training.",
        "values zero through three select one, two, four, or eight DAC settings between checks",
        "The electrical voltage represented by one DAC step is not stated.",
        (
            p(
                445,
                2,
                62,
                "TX2D Voltage Step Size (2^n)",
                f"{ASROCK_TRAINING} > TX2D_TrainOpt",
                "ASRock maps 0-3 to 1, 2, 4, or 8 DAC settings.",
            ),
            p(
                6331,
                8,
                71,
                "TX2D Voltage Step Size (2^n)",
                f"{ASUS_CBS_TRAINING} > TX2D_TrainOpt",
                "ASUS maps 0-3 to 1, 2, 4, or 8 DAC settings.",
            ),
            p(
                6523,
                8,
                86,
                "TX2D Voltage Step Size (2^n)",
                f"{ASUS_TWEAKS} > TX2D_TrainOpt",
                "ASUS maps 0-3 to 1, 2, 4, or 8 DAC settings in Additional Memory Tweaks.",
            ),
        ),
    ),
    identity(
        "tx-dfe-taps",
        "TX DFE Taps",
        "Selects the number of transmit decision-feedback-equalization taps.",
        "the option selects the transmit DFE tap count",
        "An ASUS copy states that the value applies only when TX2D_DFE is enabled.",
        (
            p(
                434,
                2,
                60,
                "TX DFE Taps",
                ASROCK_TRAINING,
                "ASRock selects the number of TX DFE taps.",
            ),
            p(
                6320,
                8,
                70,
                "TX DFE Taps",
                ASUS_CBS_TRAINING,
                "ASUS selects one through four TX DFE taps.",
            ),
            p(
                6528,
                8,
                86,
                "TX DFE Taps",
                ASUS_TWEAKS,
                "ASUS selects one through four TX DFE taps and documents the TX2D_DFE dependency.",
            ),
        ),
    ),
)

KNOWN_AMBIGUOUS_IDS = {572, 6111, 6690, 7392, 7395, 7657, 8320}
KNOWN_COLLISION_IDS = {47, 77, 435, 491, 5137, 5284, 5292, 6158, 6161, 6321, 6378}
KNOWN_REJECTED_DUPLICATE_IDS = {4901}
KNOWN_SEMANTIC_CONFLICT_IDS = {419, 6306}
BASELINE_COUNTS = {
    CanonicalSetting: 240,
    CanonicalSettingAlias: 276,
    SettingCanonicalMapping: 942,
    CanonicalMappingEvent: 942,
    CanonicalIdentityEvent: 255,
    CanonicalSettingClassification: 240,
    CanonicalClassificationEvent: 441,
    SettingMenuContext: 739,
    SettingMenuContextEvent: 739,
    SettingMenuContextCitation: 813,
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
        raise RuntimeError("The verified pre-v1.21 backup is missing or changed.")
    if inspect_backup(BACKUP_PATH).database_revision != config.EXPECTED_DATABASE_REVISION:
        raise RuntimeError("The pre-v1.21 backup has an unexpected revision.")
    if (
        session.scalar(text("SELECT version_num FROM alembic_version"))
        != config.EXPECTED_DATABASE_REVISION
    ):
        raise RuntimeError("The live schema revision changed.")
    if session.scalar(text("PRAGMA integrity_check")) != "ok":
        raise RuntimeError("SQLite integrity_check failed before Wave 16.")
    if _unmapped_count(session) != 3076:
        raise RuntimeError(f"Expected 3,076 unmapped settings, found {_unmapped_count(session)}.")
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
    if len(IDENTITIES) != 24 or len(sources) != 58 or len({x.setting_id for x in sources}) != 58:
        raise RuntimeError("Wave 16 must contain 24 identities and 58 unique records.")
    excluded = (
        KNOWN_AMBIGUOUS_IDS
        | KNOWN_COLLISION_IDS
        | KNOWN_REJECTED_DUPLICATE_IDS
        | KNOWN_SEMANTIC_CONFLICT_IDS
    )
    if excluded.intersection({item.setting_id for item in sources}):
        raise RuntimeError("A known excluded record entered Wave 16.")
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
    return f"v1.21 Wave 16 reviewer decision; confidence={CONFIDENCE_BASIS_POINTS / 100:.2f}%. {seed.rationale} Official source: {manual.vendor} manual {manual.id}, page {item.page_number}, {manual.source_url}; reviewed menu path: {item.menu_path}. Remaining uncertainty: {seed.uncertainty}"


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
                note="v1.21 Wave 16 official-manual label, signal function, options, dependencies, and menu hierarchy visually verified.",
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
        CanonicalSetting: 24,
        CanonicalSettingAlias: 24,
        SettingCanonicalMapping: 58,
        CanonicalMappingEvent: 58,
        CanonicalIdentityEvent: 24,
        CanonicalSettingClassification: 24,
        CanonicalClassificationEvent: 48,
        SettingMenuContext: 58,
        SettingMenuContextEvent: 58,
        SettingMenuContextCitation: 58,
    }
    failures = [
        f"{model.__tablename__} append-only delta mismatch"
        for model, delta in deltas.items()
        if _count(session, model) != baseline[model] + delta
    ]
    if _unmapped_count(session) != 3018:
        failures.append(f"expected 3,018 unmapped, found {_unmapped_count(session)}")
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
        raise RuntimeError("Wave 16 verification failed:\n- " + "\n- ".join(failures))


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
            "reason": "Not approved in Wave 16; requires a later evidence disposition.",
        }
        for setting, manual in rows
    ]
    audit = build_public_audit(session)
    if audit["status"] != "pass":
        raise RuntimeError("Public BIOS knowledge audit failed after Wave 16.")
    return {
        "milestone": "v1.21 DDR signal/training canonicalization Wave 16",
        "generated_at": utc_now_iso(),
        "actor": ACTOR,
        "backup": {"path": str(BACKUP_PATH), "sha256": BACKUP_SHA256},
        "database_revision": config.EXPECTED_DATABASE_REVISION,
        "summary": {
            "new_canonical_settings": 24,
            "total_mappings_created": 58,
            "reviewed_menu_contexts": 58,
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
        "# TuneForge v1.21 DDR Signal and Training Wave 16",
        "",
        "## Outcome",
        "",
        f"- New canonical settings: {summary['new_canonical_settings']}",
        f"- New verified mappings: {summary['total_mappings_created']}",
        f"- New reviewed menu contexts: {summary['reviewed_menu_contexts']}",
        f"- Remaining approved unmapped settings: {summary['remaining_manual_review']}",
        "",
        "Every decision uses visually reviewed official ASRock, ASUS, and MSI pages. Name, signal function, options, dependencies, and menu context agree; no community source was required.",
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
            "Known semantic collisions and records lacking equivalent official evidence remain unmapped. The complete decision report and pending backlog are stored in `data/reports/v1.21-ddr-signal-training-wave16.json`.",
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
        "--apply", action="store_true", help="Commit the pinned v1.21 Wave 16 batch"
    )
    args = parser.parse_args()
    with SessionLocal() as session:
        _verify_preflight(session)
        print(
            "Preflight passed: backup, revision, counts, three manuals, 58 source pins, exclusions, and trust gates match."
        )
        print("Planned outcome: 24 new identities and 58 mappings with reviewed menu contexts.")
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
        print("Applied: new_identities=24, mappings=58, menu_contexts=58.")
        print(f"Remaining approved unmapped: {payload['summary']['remaining_manual_review']}.")
        print(f"JSON report: {REPORT_JSON}")
        print(f"Summary report: {REPORT_MARKDOWN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
