# ruff: noqa: E501
"""Apply TuneForge's pinned v1.12 common firmware controls Wave 7.

Dry-run is the default. The batch contains seven canonical identities and 37
approved Source Settings visually verified in official ASRock, ASUS, GIGABYTE,
and MSI manuals. Writes preserve the citation-first and append-only architecture.
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
BACKUP_PATH = ROOT / "data" / "backups" / "tuneforge-v1.12-pre-wave7.zip"
BACKUP_SHA256 = "55d7bf1ff61abdfa63179ce3cc816bd9670f83faa8b2f774dc553b415272ea2d"
REPORT_JSON = ROOT / "data" / "reports" / "v1.12-common-firmware-controls-wave7.json"
REPORT_MARKDOWN = ROOT / "docs" / "v1.12-common-firmware-controls-wave7.md"
ACTOR = "v1.12-common-firmware-controls-official-evidence-reviewer"
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
    menu_path: str


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
        6,
        "msi",
        "5ad148e9ec850442778a3cc090df281660712919299a1c4127d012f780ae622c",
        "https://www.msi.com/Motherboard/MPG-X870E-CARBON-WIFI/support",
        49,
    ),
    ManualPin(
        7,
        "gigabyte",
        "b46c52231334bfe914491cc3d6aa9265631914ba9c1de6bb6aa11841b46a2fe5",
        "https://www.gigabyte.com/Motherboard/Z890-AORUS-ELITE-WIFI7/support#support-manual",
        30,
    ),
    ManualPin(
        8,
        "asus",
        "2e7a4b2d504e9f9286b584fcfbe9cba85e0ccd4a061ad545025160408b308213",
        "https://www.asus.com/us/supportonly/tuf%20gaming%20x870-plus%20wifi/helpdesk_manual/",
        116,
    ),
    ManualPin(
        9,
        "gigabyte",
        "14814b731f11f56522bea71ea506b4182de44e3ad729169b81a075fc84f8bb90",
        "https://www.gigabyte.com/Motherboard/B550-AORUS-ELITE-AX-V2-rev-10/support#support-manual",
        19,
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


def _menu_path(manual_id: int, name: str) -> str:
    if name == "Load Optimized Defaults":
        parent = "Exit" if manual_id in {3, 8, 11, 12} else "Save & Exit"
    elif name == "Bootup NumLock State":
        parent = "Settings > Boot" if manual_id in {5, 6} else "Boot"
    elif name == "Onboard LAN Controller":
        parent = (
            "Settings > Advanced > Integrated Peripherals"
            if manual_id in {5, 6}
            else "Settings > IO Ports"
        )
    elif name == "SATA Controller(s)":
        if manual_id in {3, 11, 12}:
            parent = "Advanced > PCH Storage Configuration"
        elif manual_id == 8:
            parent = "Advanced > SATA Configuration"
        else:
            parent = "Settings > IO Ports > SATA Configuration"
    elif name == "Serial Port":
        parent = (
            "Settings > IO Ports > Super IO Configuration"
            if manual_id == 9
            else "Advanced > Onboard Devices Configuration > Serial Port Configuration"
        )
    elif name in {"Discard Changes", "Discard Changes and Exit"}:
        parent = "Exit" if manual_id == 2 else "Settings > Save & Exit"
    else:
        raise ValueError(f"No reviewed menu hierarchy for {name!r} in manual {manual_id}.")
    return f"{parent} > {name}"


def _sources(name: str, rows: tuple[tuple[int, int, int], ...]) -> tuple[SourcePin, ...]:
    return tuple(
        SourcePin(setting_id, manual_id, page, _menu_path(manual_id, name))
        for setting_id, manual_id, page in rows
    )


def identity(
    canonical_id: str,
    name: str,
    description: str,
    rationale: str,
    uncertainty: str,
    category_id: str,
    risk_level: str,
    risk_rationale: str,
    rows: tuple[tuple[int, int, int], ...],
) -> IdentitySeed:
    return IdentitySeed(
        canonical_id,
        name,
        description,
        rationale,
        uncertainty,
        category_id,
        risk_level,
        risk_rationale,
        _sources(name, rows),
    )


IDENTITIES = (
    identity(
        "load-optimized-defaults",
        "Load Optimized Defaults",
        "Loads the firmware's vendor-supplied default values for BIOS Setup parameters.",
        "ASUS and GIGABYTE use the exact name and independently define the same confirmed action that restores default Setup values.",
        "The actual default values vary by motherboard and firmware version.",
        "boot",
        "high",
        "Loading defaults can change storage, boot, security, and tuning configuration and can prevent the current operating-system configuration from booting.",
        (
            (159, 1, 25),
            (1291, 3, 89),
            (5708, 7, 30),
            (6739, 8, 113),
            (6882, 9, 19),
            (7068, 10, 28),
            (7707, 11, 86),
            (8370, 12, 91),
        ),
    ),
    identity(
        "bootup-numlock-state",
        "Bootup NumLock State",
        "Selects the keyboard numeric-keypad NumLock state during or immediately after system boot.",
        "GIGABYTE and MSI use the exact name and define the same keyboard NumLock state applied during boot or after POST.",
        "The exact transition point is described as bootup or after POST depending on vendor wording.",
        "boot",
        "low",
        "This changes only the initial numeric-keypad state and does not affect system stability or stored data.",
        ((139, 1, 22), (4619, 5, 27), (5170, 6, 18), (5687, 7, 28), (6862, 9, 16), (7044, 10, 25)),
    ),
    identity(
        "onboard-lan-controller",
        "Onboard LAN Controller",
        "Enables or disables the motherboard's onboard wired network controller.",
        "GIGABYTE and MSI use the exact name and independently define the same enable/disable control for the onboard LAN controller.",
        "Boards with multiple LAN controllers may expose additional independently numbered controls.",
        "peripheral-io",
        "moderate",
        "Disabling the controller removes onboard wired networking until the setting is restored or another adapter is used.",
        ((85, 1, 15), (4531, 5, 17), (5125, 6, 13), (5618, 7, 20), (6802, 9, 11), (6991, 10, 19)),
    ),
    identity(
        "sata-controllers",
        "SATA Controller(s)",
        "Enables or disables the motherboard's integrated SATA storage controller or controllers.",
        "ASUS and GIGABYTE use the exact name and define the same enable/disable control for integrated SATA controllers.",
        "Port count and whether one or several controller blocks are affected varies by motherboard.",
        "peripheral-io",
        "high",
        "Disabling an active SATA controller can make attached boot or data drives unavailable and prevent the operating system from booting.",
        (
            (1103, 3, 60),
            (5648, 7, 22),
            (6123, 8, 52),
            (7013, 10, 21),
            (7547, 11, 57),
            (8197, 12, 60),
        ),
    ),
    identity(
        "serial-port",
        "Serial Port",
        "Enables or disables the motherboard's onboard serial communications port.",
        "ASUS and GIGABYTE use the exact name and define the same enable/disable control for the onboard serial or COM port.",
        "Availability depends on whether the motherboard exposes a serial-port connector.",
        "peripheral-io",
        "low",
        "Disabling the port affects only devices that depend on the onboard serial interface.",
        ((1171, 3, 69), (6150, 8, 56), (6804, 9, 11), (7593, 11, 66), (8249, 12, 71)),
    ),
    identity(
        "discard-changes",
        "Discard Changes",
        "Discards unsaved BIOS Setup changes and restores the values that were active before the current editing session.",
        "ASRock and MSI use the exact name and define the same action that abandons current unsaved changes without necessarily leaving Setup.",
        "Firmware may present a confirmation dialog or keyboard shortcut.",
        "boot",
        "low",
        "The action abandons only unsaved edits; already saved firmware configuration is not changed.",
        ((622, 2, 85), (4681, 5, 32), (5235, 6, 24)),
    ),
    identity(
        "discard-changes-and-exit",
        "Discard Changes and Exit",
        "Exits BIOS Setup without saving changes made during the current editing session.",
        "ASRock and MSI use the exact name and define the same action that exits Setup while abandoning current unsaved changes.",
        "Firmware may present a confirmation dialog or keyboard shortcut.",
        "boot",
        "low",
        "The action abandons only unsaved edits before exiting; already saved firmware configuration is not changed.",
        ((621, 2, 85), (4678, 5, 32), (5232, 6, 24)),
    ),
)

KNOWN_AMBIGUOUS_IDS = {572, 6111, 6690, 7392, 7395, 7657, 8320}

BASELINE_COUNTS = {
    CanonicalSetting: 140,
    CanonicalSettingAlias: 176,
    SettingCanonicalMapping: 667,
    CanonicalMappingEvent: 667,
    CanonicalIdentityEvent: 155,
    CanonicalSettingClassification: 140,
    CanonicalClassificationEvent: 241,
    SettingMenuContext: 464,
    SettingMenuContextEvent: 464,
    SettingMenuContextCitation: 538,
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


def _all_sources() -> tuple[SourcePin, ...]:
    return tuple(source for seed in IDENTITIES for source in seed.sources)


def _verify_preflight(session) -> None:
    if not BACKUP_PATH.is_file() or _sha256(BACKUP_PATH) != BACKUP_SHA256:
        raise RuntimeError("The verified pre-v1.12 backup is missing or changed.")
    if inspect_backup(BACKUP_PATH).database_revision != config.EXPECTED_DATABASE_REVISION:
        raise RuntimeError("The pre-v1.12 backup has an unexpected revision.")
    if (
        session.scalar(text("SELECT version_num FROM alembic_version"))
        != config.EXPECTED_DATABASE_REVISION
    ):
        raise RuntimeError("The live schema revision changed.")
    if session.scalar(text("PRAGMA integrity_check")) != "ok":
        raise RuntimeError("SQLite integrity_check failed before Wave 7.")
    if _unmapped_count(session) != 3351:
        raise RuntimeError(f"Expected 3,351 unmapped settings, found {_unmapped_count(session)}.")
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
    sources = _all_sources()
    if (
        len(IDENTITIES) != 7
        or len(sources) != 37
        or len({source.setting_id for source in sources}) != 37
    ):
        raise RuntimeError("Wave 7 must contain seven identities and 37 unique records.")
    if KNOWN_AMBIGUOUS_IDS.intersection({source.setting_id for source in sources}):
        raise RuntimeError("A known ambiguous record entered Wave 7.")
    expected_vendor_sets = {
        "load-optimized-defaults": {"asus", "gigabyte"},
        "bootup-numlock-state": {"gigabyte", "msi"},
        "onboard-lan-controller": {"gigabyte", "msi"},
        "sata-controllers": {"asus", "gigabyte"},
        "serial-port": {"asus", "gigabyte"},
        "discard-changes": {"asrock", "msi"},
        "discard-changes-and-exit": {"asrock", "msi"},
    }
    for seed in IDENTITIES:
        if session.get(CanonicalSetting, seed.canonical_id) is not None:
            raise RuntimeError(f"Canonical identity already exists: {seed.canonical_id}.")
        vendors = {pins[source.manual_id].vendor_id for source in seed.sources}
        if vendors != expected_vendor_sets[seed.canonical_id]:
            raise RuntimeError(f"{seed.canonical_id} has unexpected evidence vendors: {vendors}.")
        for source in seed.sources:
            setting = session.get(Setting, source.setting_id)
            manual = session.get(Manual, source.manual_id)
            actual = (
                (
                    setting.manual_id,
                    setting.edited_page_number or setting.page_number,
                    normalize_alias(effective_name(setting)),
                )
                if setting
                else None
            )
            expected = (source.manual_id, source.page_number, normalize_alias(seed.canonical_name))
            if actual != expected:
                raise RuntimeError(
                    f"Pinned source {source.setting_id} changed: {actual!r} != {expected!r}."
                )
            reasons = canonicalization.source_trust_reasons(setting, manual)
            if reasons:
                raise RuntimeError(
                    f"Source {source.setting_id} failed trust gates: {' '.join(reasons)}"
                )
            if session.get(SettingCanonicalMapping, source.setting_id) is not None:
                raise RuntimeError(f"Source {source.setting_id} is already mapped.")
            if session.get(SettingMenuContext, source.setting_id) is not None:
                raise RuntimeError(f"Source {source.setting_id} already has reviewed menu context.")
    for setting_id in KNOWN_AMBIGUOUS_IDS:
        if session.get(SettingCanonicalMapping, setting_id) is not None:
            raise RuntimeError(f"Known ambiguous source {setting_id} was unexpectedly mapped.")


def _mapping_note(session, seed: IdentitySeed, source: SourcePin) -> str:
    manual = session.get(Manual, source.manual_id)
    return f"v1.12 Wave 7 reviewer decision; confidence={CONFIDENCE_BASIS_POINTS / 100:.2f}%. {seed.rationale} Official source: {manual.vendor} manual {manual.id}, page {source.page_number}, {manual.source_url}; reviewed menu path: {source.menu_path}. Remaining uncertainty: {seed.uncertainty}"


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
        for source in seed.sources:
            setting = session.get(Setting, source.setting_id)
            canonical_identity_review.review_menu_context(
                session,
                setting.id,
                menu_path=source.menu_path,
                source_page_number=source.page_number,
                evidence_excerpt=f"The official manual page defines {effective_name(setting)} with the common firmware-control behavior represented by this reviewed hierarchy.",
                note="v1.12 Wave 7 official-manual name, function, options, and menu hierarchy visually verified.",
                actor=ACTOR,
                commit=False,
            )
            canonical_settings.map_setting(
                session,
                setting,
                canonical_id=seed.canonical_id,
                note=_mapping_note(session, seed, source),
                actor=ACTOR,
                mapping_method=canonical_settings.MAPPING_REVIEWER,
                commit=False,
            )


def _verify_final(session, baseline: dict[type, int]) -> None:
    deltas = {
        CanonicalSetting: 7,
        CanonicalSettingAlias: 7,
        SettingCanonicalMapping: 37,
        CanonicalMappingEvent: 37,
        CanonicalIdentityEvent: 7,
        CanonicalSettingClassification: 7,
        CanonicalClassificationEvent: 14,
        SettingMenuContext: 37,
        SettingMenuContextEvent: 37,
        SettingMenuContextCitation: 37,
    }
    failures = [
        f"{model.__tablename__} append-only delta mismatch"
        for model, delta in deltas.items()
        if _count(session, model) != baseline[model] + delta
    ]
    if _unmapped_count(session) != 3314:
        failures.append(f"expected 3,314 unmapped, found {_unmapped_count(session)}")
    for seed in IDENTITIES:
        expected = {source.setting_id for source in seed.sources}
        detail = library.canonical_detail(session, seed.canonical_id)
        public = {item.setting_id for item in detail.source_settings} if detail else set()
        if not expected.issubset(public):
            failures.append(f"{seed.canonical_id} public projection mismatch")
        elif any(
            not item.menu_path for item in detail.source_settings if item.setting_id in expected
        ):
            failures.append(f"{seed.canonical_id} public menu path missing")
    if any(
        session.get(SettingCanonicalMapping, setting_id) is not None
        for setting_id in KNOWN_AMBIGUOUS_IDS
    ):
        failures.append("a known ambiguous record was mapped")
    if session.scalar(text("PRAGMA integrity_check")) != "ok":
        failures.append("SQLite integrity_check failed")
    foreign_keys = list(session.execute(text("PRAGMA foreign_key_check")))
    if foreign_keys:
        failures.append(f"foreign-key violations: {foreign_keys!r}")
    if failures:
        raise RuntimeError("Wave 7 verification failed:\n- " + "\n- ".join(failures))


def _build_payload(session) -> dict[str, object]:
    decisions = []
    for seed in IDENTITIES:
        sources = []
        for source in seed.sources:
            setting = session.get(Setting, source.setting_id)
            manual = session.get(Manual, source.manual_id)
            sources.append(
                {
                    "setting_id": setting.id,
                    "vendor_id": manual.vendor_id,
                    "manual_id": manual.id,
                    "source_name": effective_name(setting),
                    "source_description": effective_description(setting),
                    "page_number": source.page_number,
                    "menu_path": source.menu_path,
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
    remaining_rows = session.execute(
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
            "reason": "Not approved in Wave 7; requires a later one-by-one evidence disposition.",
        }
        for setting, manual in remaining_rows
    ]
    audit = build_public_audit(session)
    if audit["status"] != "pass":
        raise RuntimeError("Public BIOS knowledge audit failed after Wave 7.")
    return {
        "milestone": "v1.12 common firmware controls canonicalization Wave 7",
        "generated_at": utc_now_iso(),
        "actor": ACTOR,
        "backup": {"path": str(BACKUP_PATH), "sha256": BACKUP_SHA256},
        "database_revision": config.EXPECTED_DATABASE_REVISION,
        "summary": {
            "new_canonical_settings": 7,
            "total_mappings_created": 37,
            "reviewed_menu_contexts": 37,
            "remaining_manual_review": len(remaining),
        },
        "official_manuals": [asdict(pin) for pin in MANUALS],
        "approved_decisions": decisions,
        "intentional_exclusions": [
            {
                "setting_ids": sorted(KNOWN_AMBIGUOUS_IDS),
                "source_name": "Known ambiguous source records",
                "reason": "Previously reviewed official conflicts and insufficient-evidence records remain unmapped.",
            }
        ],
        "remaining_manual_review": remaining,
        "public_knowledge_audit": audit,
    }


def _markdown(payload: dict[str, object]) -> str:
    summary = payload["summary"]
    lines = [
        "# TuneForge v1.12 Common Firmware Controls Wave 7",
        "",
        "## Outcome",
        "",
        f"- New canonical settings: {summary['new_canonical_settings']}",
        f"- New verified mappings: {summary['total_mappings_created']}",
        f"- New reviewed menu contexts: {summary['reviewed_menu_contexts']}",
        f"- Remaining approved unmapped settings: {summary['remaining_manual_review']}",
        "",
        "Every decision uses visually reviewed official ASRock, ASUS, GIGABYTE, and MSI pages. Exact normalized names, function, options, and menu hierarchy agree; no community source was required.",
        "",
        "## Approved identities",
        "",
    ]
    for decision in payload["approved_decisions"]:
        vendors = sorted({source["vendor_id"] for source in decision["sources"]})
        lines.append(
            f"- **{decision['canonical_name']}** - {len(decision['sources'])} mappings across {len(vendors)} vendors: {', '.join(vendors)}. {decision['rationale']}"
        )
    lines.extend(
        [
            "",
            "## Intentional exclusion",
            "",
            "Previously documented ambiguous records remain unmapped. The machine-readable report contains every approval and the complete pending backlog: `data/reports/v1.12-common-firmware-controls-wave7.json`.",
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
    parser.add_argument("--apply", action="store_true", help="Commit the pinned v1.12 Wave 7 batch")
    args = parser.parse_args()
    with SessionLocal() as session:
        _verify_preflight(session)
        print(
            "Preflight passed: backup, revision, counts, 11 manuals, 37 source pins, exclusions, and trust gates match."
        )
        print("Planned outcome: seven new identities and 37 mappings with reviewed menu contexts.")
        if not args.apply:
            print("Dry run only; pass --apply to commit the pinned reviewed batch.")
            return 0
        baseline = {model: _count(session, model) for model in BASELINE_COUNTS}
        json_temp = markdown_temp = None
        try:
            _apply(session)
            session.flush()
            _verify_final(session, baseline)
            payload = _build_payload(session)
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
        print("Applied: new_identities=7, mappings=37, menu_contexts=37.")
        print(f"Remaining approved unmapped: {payload['summary']['remaining_manual_review']}.")
        print(f"JSON report: {REPORT_JSON}")
        print(f"Summary report: {REPORT_MARKDOWN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
