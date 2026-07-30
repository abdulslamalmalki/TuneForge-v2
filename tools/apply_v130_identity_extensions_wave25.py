# ruff: noqa: E501
"""Apply TuneForge's pinned v1.30 identity-extension Wave 25.

Dry-run is the default. One new identity and sixteen extensions across seven
existing identities were visually verified in seven official vendor manuals.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import asdict
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
from tools.apply_v129_platform_power_wave24 import IdentitySeed, ManualPin, SourcePin, source
from tools.audit_public_bios_knowledge import build_report as build_public_audit

ROOT = Path(__file__).resolve().parents[1]
BACKUP_PATH = ROOT / "data" / "backups" / "tuneforge-v1.30-pre-wave25.zip"
BACKUP_SHA256 = "26ff5d26bdd7af7b2477b62d182e6f9cf8f657c201574084c4bf258f7dc05dd6"
REPORT_JSON = ROOT / "data" / "reports" / "v1.30-identity-extensions-wave25.json"
REPORT_MARKDOWN = ROOT / "docs" / "v1.30-identity-extensions-wave25.md"
ACTOR = "v1.30-identity-extension-official-evidence-reviewer"
RELEASE_VERSION = "1.30.0"
CONFIDENCE_BASIS_POINTS = 9990

MANUALS = (
    ManualPin(
        2,
        "asrock",
        "ec2ca097b878859f5de664449f5ca8d0d51eb41509290588da0fc2dacf711315",
        "https://www.asrock.com/mb/AMD/X870E%20Taichi%20White/index.asp#Manual",
        85,
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


def extension(
    canonical_id: str,
    canonical_name: str,
    rationale: str,
    uncertainty: str,
    sources: tuple[SourcePin, ...],
) -> IdentitySeed:
    return IdentitySeed(
        canonical_id,
        canonical_name,
        "",
        rationale,
        uncertainty,
        "",
        "",
        "",
        sources,
        create_identity=False,
    )


IDENTITIES = (
    IdentitySeed(
        "amd-eco-mode",
        "AMD ECO Mode",
        "Selects a reduced AMD processor power definition by adjusting CPU control limits.",
        "Official ASRock and ASUS AMD manuals independently describe the same ECO Mode control for a 65 W processor power definition and default behavior.",
        "The ASRock guide additionally documents a 105 W mode for eligible 170 W OPNs; available profiles remain processor- and firmware-dependent.",
        "cpu-performance",
        "moderate",
        "ECO Mode changes CPU power limits and can reduce performance, power consumption, and temperature.",
        (
            source(
                490,
                2,
                66,
                "ECO Mode",
                "Advanced > AMD CBS > SMU Common Options > ECO Mode",
                "ASRock adjusts CPU control limits for 65 W ECO operation, default operation, and an eligible 105 W profile.",
            ),
            source(
                6377,
                8,
                74,
                "AMD CBS ECO Mode",
                "Advanced > AMD CBS > SMU Common Options > ECO Mode",
                "ASUS adjusts CPU control limits for 65 W ECO operation or the default processor power definition.",
            ),
        ),
    ),
    extension(
        "restore-ac-power-loss",
        "Restore AC Power Loss",
        "The MSI Intel 700 record independently documents the same Power Off, Power On, and Last State behavior already verified across ASRock, ASUS, and MSI manuals.",
        "Wording uses 'Restore after' rather than the canonical 'Restore'; function and choices are identical.",
        (
            source(
                4596,
                5,
                23,
                "Restore after AC Power Loss",
                "Settings > Advanced > Power Management Setup > Restore after AC Power Loss",
                "MSI selects Power Off, Power On, or Last State after AC power restoration.",
            ),
        ),
    ),
    extension(
        "intel-turbo-boost-max-technology-3-0",
        "Intel(R) Turbo Boost Max Technology 3.0",
        "The GIGABYTE Intel 600 record directly names and describes Intel Turbo Boost Max Technology 3.0, matching the existing ASUS and MSI identity.",
        "The '(Note)' suffix marks chipset/CPU availability and is not part of the function.",
        (
            source(
                6925,
                10,
                11,
                "Intel(R) Turbo Boost Max Technology 3.0 (Note)",
                "Tweaker > Advanced CPU Settings > Intel(R) Turbo Boost Max Technology 3.0 (Note)",
                "GIGABYTE enables or disables Turbo Boost Max Technology 3.0 and describes favored-core workload selection.",
            ),
        ),
    ),
    extension(
        "rc6-render-standby",
        "RC6 (Render Standby)",
        "The GIGABYTE Intel 600 record describes the same onboard-graphics standby power-saving behavior as the reviewed RC6 identity.",
        "The '(Note)' suffix marks hardware availability only.",
        (
            source(
                6982,
                10,
                18,
                "RC6(Render Standby) (Note)",
                "Settings > Platform Power > RC6(Render Standby) (Note)",
                "GIGABYTE controls whether onboard graphics enters standby to reduce power consumption.",
            ),
        ),
    ),
    extension(
        "uma-frame-buffer-size",
        "UMA Frame Buffer Size",
        "The GIGABYTE AMD B550/A520 record configures the system-memory frame buffer dedicated to onboard graphics, matching the existing cross-vendor UMA identity.",
        "The available range and UMA Mode dependency are platform-specific.",
        (
            source(
                6797,
                9,
                10,
                "UMA Frame Buffer Size (Note)",
                "Settings > IO Ports > UMA Frame Buffer Size (Note)",
                "GIGABYTE selects the memory reserved solely for onboard graphics when UMA Mode is UMA Specified.",
            ),
        ),
    ),
    extension(
        "dram-vdd-switching-frequency",
        "DRAM VDD Switching Frequency",
        "Official ASUS Intel 600 and 700 manuals document the same DRAM or memory VDD regulator switching frequency already verified from ASUS and MSI evidence.",
        "Synchronized and per-PMIC variants remain distinct source records under one rail-specific canonical function.",
        (
            source(
                7462,
                11,
                45,
                "Memory VDD Switching Frequency",
                "Ai Tweaker > Memory VDD Switching Frequency",
                "ASUS configures the memory VDD regulator from 0.750 MHz to 1.500 MHz.",
            ),
            source(
                7471,
                11,
                46,
                "PMIC0-3 Memory VDD Switching Frequency",
                "Ai Tweaker > PMIC0-3 Memory VDD Switching Frequency",
                "ASUS configures the same VDD regulator frequency for per-PMIC operation.",
            ),
            source(
                8116,
                12,
                49,
                "DRAM VDD Switching Frequency",
                "Ai Tweaker > DRAM VDD Switching Frequency",
                "ASUS configures the DRAM VDD regulator from 0.750 MHz to 1.500 MHz.",
            ),
            source(
                8125,
                12,
                50,
                "PMIC0-3 DRAM VDD Switching Frequency",
                "Ai Tweaker > PMIC0-3 DRAM VDD Switching Frequency",
                "ASUS configures the same DRAM VDD regulator frequency for per-PMIC operation.",
            ),
        ),
    ),
    extension(
        "dram-vddq-switching-frequency",
        "DRAM VDDQ Switching Frequency",
        "Official ASUS Intel 600 and 700 manuals document the same DRAM or memory VDDQ regulator switching frequency already verified from ASUS and MSI evidence.",
        "Synchronized and per-PMIC variants remain distinct source records under one rail-specific canonical function.",
        (
            source(
                7463,
                11,
                46,
                "Memory VDDQ Switching Frequency",
                "Ai Tweaker > Memory VDDQ Switching Frequency",
                "ASUS configures the memory VDDQ regulator from 0.750 MHz to 1.500 MHz.",
            ),
            source(
                7472,
                11,
                47,
                "PMIC0-3 Memory VDDQ Switching Frequency",
                "Ai Tweaker > PMIC0-3 Memory VDDQ Switching Frequency",
                "ASUS configures the same VDDQ regulator frequency for per-PMIC operation.",
            ),
            source(
                8117,
                12,
                49,
                "DRAM VDDQ Switching Frequency",
                "Ai Tweaker > DRAM VDDQ Switching Frequency",
                "ASUS configures the DRAM VDDQ regulator from 0.750 MHz to 1.500 MHz.",
            ),
            source(
                8126,
                12,
                50,
                "PMIC0-3 DRAM VDDQ Switching Frequency",
                "Ai Tweaker > PMIC0-3 DRAM VDDQ Switching Frequency",
                "ASUS configures the same DRAM VDDQ regulator frequency for per-PMIC operation.",
            ),
        ),
    ),
    extension(
        "dram-vpp-switching-frequency",
        "DRAM VPP Switching Frequency",
        "Official ASUS Intel 600 and 700 manuals document the same DRAM or memory VPP regulator switching frequency already verified from ASUS and MSI evidence.",
        "Synchronized and per-PMIC variants remain distinct source records under one rail-specific canonical function.",
        (
            source(
                7464,
                11,
                46,
                "Memory VPP Switching Frequency",
                "Ai Tweaker > Memory VPP Switching Frequency",
                "ASUS configures the memory VPP regulator from 0.750 MHz to 1.500 MHz.",
            ),
            source(
                7473,
                11,
                47,
                "PMIC0-3 Memory VPP Switching Frequency",
                "Ai Tweaker > PMIC0-3 Memory VPP Switching Frequency",
                "ASUS configures the same VPP regulator frequency for per-PMIC operation.",
            ),
            source(
                8118,
                12,
                49,
                "DRAM VPP Switching Frequency",
                "Ai Tweaker > DRAM VPP Switching Frequency",
                "ASUS configures the DRAM VPP regulator from 0.750 MHz to 1.500 MHz.",
            ),
            source(
                8127,
                12,
                50,
                "PMIC0-3 DRAM VPP Switching Frequency",
                "Ai Tweaker > PMIC0-3 DRAM VPP Switching Frequency",
                "ASUS configures the same DRAM VPP regulator frequency for per-PMIC operation.",
            ),
        ),
    ),
)

EXCLUDED_IDS = {
    36,
    47,
    77,
    283,
    374,
    419,
    435,
    447,
    491,
    572,
    1033,
    1134,
    4741,
    4795,
    4901,
    5137,
    5284,
    5292,
    5323,
    5342,
    5528,
    5657,
    5779,
    6111,
    6158,
    6161,
    6288,
    6306,
    6321,
    6333,
    6378,
    6442,
    6501,
    6580,
    6690,
    7392,
    7395,
    7455,
    7657,
    8107,
    8320,
}
REJECTED_DUPLICATE_IDS = {4901}
BASELINE_COUNTS = {
    CanonicalSetting: 330,
    CanonicalSettingAlias: 366,
    SettingCanonicalMapping: 1160,
    CanonicalMappingEvent: 1160,
    CanonicalIdentityEvent: 345,
    CanonicalSettingClassification: 330,
    CanonicalClassificationEvent: 621,
    SettingMenuContext: 957,
    SettingMenuContextEvent: 957,
    SettingMenuContextCitation: 1031,
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _count(session, model: type) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


def _sources() -> tuple[SourcePin, ...]:
    return tuple(item for seed in IDENTITIES for item in seed.sources)


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


def _verify_preflight(session) -> None:
    if config.APP_VERSION != "1.29.0":
        raise RuntimeError(f"Expected v1.29 baseline, found {config.APP_VERSION}.")
    if not BACKUP_PATH.is_file() or _sha256(BACKUP_PATH) != BACKUP_SHA256:
        raise RuntimeError("The verified pre-v1.30 backup is missing or changed.")
    if inspect_backup(BACKUP_PATH).database_revision != config.EXPECTED_DATABASE_REVISION:
        raise RuntimeError("The pre-v1.30 backup has an unexpected revision.")
    if (
        session.scalar(text("SELECT version_num FROM alembic_version"))
        != config.EXPECTED_DATABASE_REVISION
    ):
        raise RuntimeError("The live schema revision changed.")
    if session.scalar(text("PRAGMA integrity_check")) != "ok" or list(
        session.execute(text("PRAGMA foreign_key_check"))
    ):
        raise RuntimeError("Database integrity failed before Wave 25.")
    if _unmapped_count(session) != 2858:
        raise RuntimeError(f"Expected 2,858 unmapped settings, found {_unmapped_count(session)}.")
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
    if len(IDENTITIES) != 8 or sum(seed.create_identity for seed in IDENTITIES) != 1:
        raise RuntimeError("Wave 25 must contain one new and seven existing identities.")
    if len(sources) != 18 or len({item.setting_id for item in sources}) != 18:
        raise RuntimeError("Wave 25 must contain 18 unique sources.")
    if EXCLUDED_IDS.intersection(item.setting_id for item in sources):
        raise RuntimeError("A known excluded record entered Wave 25.")
    for seed in IDENTITIES:
        existing = session.get(CanonicalSetting, seed.canonical_id)
        if seed.create_identity and existing is not None:
            raise RuntimeError(f"Canonical identity already exists: {seed.canonical_id}.")
        if seed.create_identity:
            if len({pins[item.manual_id].vendor_id for item in seed.sources}) < 2:
                raise RuntimeError(
                    f"New identity lacks cross-vendor evidence: {seed.canonical_id}."
                )
        else:
            detail = library.canonical_detail(session, seed.canonical_id)
            if detail is None or len({item.vendor_id for item in detail.source_settings}) < 2:
                raise RuntimeError(
                    f"Existing identity lacks cross-vendor support: {seed.canonical_id}."
                )
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
                raise RuntimeError(f"Source {item.setting_id} is no longer untouched.")
    for setting_id in EXCLUDED_IDS - REJECTED_DUPLICATE_IDS:
        if session.get(SettingCanonicalMapping, setting_id) is not None:
            raise RuntimeError(f"Known excluded source {setting_id} was unexpectedly mapped.")


def _mapping_note(session, seed: IdentitySeed, item: SourcePin) -> str:
    manual = session.get(Manual, item.manual_id)
    return f"v1.30 Wave 25 reviewer decision; confidence={CONFIDENCE_BASIS_POINTS / 100:.2f}%. {seed.rationale} Official source: {manual.vendor} manual {manual.id}, page {item.page_number}, {manual.source_url}; reviewed menu path: {item.menu_path}. Remaining uncertainty: {seed.uncertainty}"


def _apply(session) -> None:
    for seed in IDENTITIES:
        if seed.create_identity:
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
                note="v1.30 Wave 25 official-manual name, function, options, dependencies, platform, and menu hierarchy visually verified.",
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
        CanonicalSetting: 1,
        CanonicalSettingAlias: 1,
        SettingCanonicalMapping: 18,
        CanonicalMappingEvent: 18,
        CanonicalIdentityEvent: 1,
        CanonicalSettingClassification: 1,
        CanonicalClassificationEvent: 2,
        SettingMenuContext: 18,
        SettingMenuContextEvent: 18,
        SettingMenuContextCitation: 18,
    }
    failures = [
        f"{model.__tablename__} append-only delta mismatch"
        for model, delta in deltas.items()
        if _count(session, model) != baseline[model] + delta
    ]
    if _unmapped_count(session) != 2840:
        failures.append(f"expected 2,840 unmapped, found {_unmapped_count(session)}")
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
        session.get(SettingCanonicalMapping, setting_id) is not None for setting_id in EXCLUDED_IDS
    ):
        failures.append("a known excluded record was mapped")
    if session.scalar(text("PRAGMA integrity_check")) != "ok":
        failures.append("SQLite integrity_check failed")
    foreign_keys = list(session.execute(text("PRAGMA foreign_key_check")))
    if foreign_keys:
        failures.append(f"foreign-key violations: {foreign_keys!r}")
    if failures:
        raise RuntimeError("Wave 25 verification failed:\n- " + "\n- ".join(failures))


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
                "decision": "new_identity"
                if seed.create_identity
                else "existing_identity_extension",
                "canonical_id": seed.canonical_id,
                "canonical_name": seed.canonical_name,
                "description": seed.description or None,
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
            "reason": "Not approved in Wave 25; requires a later evidence disposition.",
        }
        for setting, manual in rows
    ]
    audit = build_public_audit(session)
    if audit["status"] != "pass":
        raise RuntimeError("Public BIOS knowledge audit failed after Wave 25.")
    audit["app_version"] = RELEASE_VERSION
    return {
        "milestone": "v1.30 identity-extension canonicalization Wave 25",
        "generated_at": utc_now_iso(),
        "actor": ACTOR,
        "backup": {"path": str(BACKUP_PATH), "sha256": BACKUP_SHA256},
        "database_revision": config.EXPECTED_DATABASE_REVISION,
        "summary": {
            "new_canonical_settings": 1,
            "existing_canonical_settings_extended": 7,
            "total_mappings_created": 18,
            "reviewed_menu_contexts": 18,
            "remaining_manual_review": len(remaining),
        },
        "official_manuals": [asdict(pin) for pin in MANUALS],
        "approved_decisions": decisions,
        "intentional_exclusions": [
            {
                "setting_ids": sorted(EXCLUDED_IDS),
                "reason": "Preserved ambiguity, collision, rejection, contradiction, or evidence gap.",
            }
        ],
        "remaining_manual_review": remaining,
        "public_knowledge_audit": audit,
    }


def _markdown(payload: dict[str, object]) -> str:
    summary = payload["summary"]
    lines = [
        "# TuneForge v1.30 Identity Extensions Wave 25",
        "",
        "## Outcome",
        "",
        f"- New canonical settings: {summary['new_canonical_settings']}",
        f"- Existing canonical settings extended: {summary['existing_canonical_settings_extended']}",
        f"- New verified mappings: {summary['total_mappings_created']}",
        f"- New reviewed menu contexts: {summary['reviewed_menu_contexts']}",
        f"- Remaining approved unmapped settings: {summary['remaining_manual_review']}",
        "",
        "Every decision uses visually reviewed official ASUS, ASRock, GIGABYTE, and MSI pages. No community source was required.",
        "",
        "## Approved decisions",
        "",
    ]
    for decision in payload["approved_decisions"]:
        vendors = sorted({item["vendor_id"] for item in decision["sources"]})
        kind = (
            "new identity"
            if decision["decision"] == "new_identity"
            else "existing identity extension"
        )
        label = "mapping" if len(decision["sources"]) == 1 else "mappings"
        lines.append(
            f"- **{decision['canonical_name']}** ({kind}) - {len(decision['sources'])} {label} from {', '.join(vendors)}. {decision['rationale']}"
        )
    lines.extend(
        [
            "",
            "The complete machine-readable decision report and pending backlog are stored in `data/reports/v1.30-identity-extensions-wave25.json`.",
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
        "--apply", action="store_true", help="Commit the pinned v1.30 Wave 25 batch"
    )
    args = parser.parse_args()
    with SessionLocal() as session:
        _verify_preflight(session)
        print(
            "Preflight passed: backup, revision, counts, seven manuals, 18 source pins, exclusions, and trust gates match."
        )
        print(
            "Planned outcome: one new identity, seven existing identity extensions, and 18 mappings."
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
        print("Applied: new_identities=1, existing_extensions=7, mappings=18, menu_contexts=18.")
        print(f"Remaining approved unmapped: {payload['summary']['remaining_manual_review']}.")
        print(f"JSON report: {REPORT_JSON}")
        print(f"Summary report: {REPORT_MARKDOWN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
