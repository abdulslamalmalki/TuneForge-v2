# ruff: noqa: E501
"""Apply TuneForge's pinned v1.31 literal-extension Wave 26.

Dry-run is the default. Twenty-eight source records were visually verified in
nine official vendor manuals. Existing cross-vendor identities are extended;
no new identity is created and no equivalence is inferred.
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
BACKUP_PATH = ROOT / "data" / "backups" / "tuneforge-v1.31-pre-wave26.zip"
BACKUP_SHA256 = "eb484b63b4159cb32654378045f943ef09a7ee3581c7f85da25fd80cbdd074b4"
REPORT_JSON = ROOT / "data" / "reports" / "v1.31-literal-extensions-wave26.json"
REPORT_MARKDOWN = ROOT / "docs" / "v1.31-literal-extensions-wave26.md"
ACTOR = "v1.31-literal-extension-official-evidence-reviewer"
RELEASE_VERSION = "1.31.0"
CONFIDENCE_BASIS_POINTS = 9990

MANUALS = (
    ManualPin(1, "gigabyte", "389d5b7ab9330ef95523bccad85afae578fbf91bd97572eff9b438511d14e154", "https://www.gigabyte.com/Motherboard/B650M-AORUS-ELITE-AX-rev-14/support#support-manual", 25),
    ManualPin(2, "asrock", "ec2ca097b878859f5de664449f5ca8d0d51eb41509290588da0fc2dacf711315", "https://www.asrock.com/mb/AMD/X870E%20Taichi%20White/index.asp#Manual", 85),
    ManualPin(3, "asus", "586c9e7fc4a78e22afc5234b3d5f13511b9e923131a76f4af963bd4a50293054", "https://www.asus.com/us/supportonly/prime%20b860m-ks/helpdesk_manual/", 92),
    ManualPin(6, "msi", "5ad148e9ec850442778a3cc090df281660712919299a1c4127d012f780ae622c", "https://www.msi.com/Motherboard/MPG-X870E-CARBON-WIFI/support", 49),
    ManualPin(7, "gigabyte", "b46c52231334bfe914491cc3d6aa9265631914ba9c1de6bb6aa11841b46a2fe5", "https://www.gigabyte.com/Motherboard/Z890-AORUS-ELITE-WIFI7/support#support-manual", 30),
    ManualPin(8, "asus", "2e7a4b2d504e9f9286b584fcfbe9cba85e0ccd4a061ad545025160408b308213", "https://www.asus.com/us/supportonly/tuf%20gaming%20x870-plus%20wifi/helpdesk_manual/", 116),
    ManualPin(9, "gigabyte", "14814b731f11f56522bea71ea506b4182de44e3ad729169b81a075fc84f8bb90", "https://www.gigabyte.com/Motherboard/B550-AORUS-ELITE-AX-V2-rev-10/support#support-manual", 19),
    ManualPin(11, "asus", "4e3f39d81ae255a6dc8d48a52a31d0be2e496d457e3c6a17f7c689eb54cb57cc", "https://www.asus.com/us/motherboards-components/motherboards/prime/prime-b660-plus-d4/helpdesk_manual?model2Name=PRIME-B660-PLUS-D4", 88),
    ManualPin(12, "asus", "49d55cb253e29b523cb69a78934d1df0f8160dd7feb9c68a76ceecb42927da37", "https://www.asus.com/motherboards-components/motherboards/tuf-gaming/tuf-gaming-b760m-plus/helpdesk_manual?model2Name=TUF-GAMING-B760M-PLUS", 94),
)


def extension(canonical_id: str, canonical_name: str, rationale: str, uncertainty: str, sources: tuple[SourcePin, ...]) -> IdentitySeed:
    return IdentitySeed(canonical_id, canonical_name, "", rationale, uncertainty, "", "", "", sources, create_identity=False)


IDENTITIES = (
    extension(
        "power-down-enable", "Power Down Enable",
        "The ASRock heading contains the documented 'Eanble' transposition, but its official description explicitly enables or disables DDR power-down mode, matching the existing ASUS, GIGABYTE, and MSI identity.",
        "The source spelling error is retained as the vendor-manual alias.",
        (source(417, 2, 59, "Power Down Eanble", "Advanced > AMD CBS > UMC Common Options > DDR Options > DDR Controller Configuration > DDR Power Options > Power Down Eanble", "ASRock explicitly enables or disables DDR power-down mode."),),
    ),
    extension(
        "hd-audio-controller", "HD Audio Controller",
        "The GIGABYTE Intel 800 Audio Controller record has the same onboard-audio enable/disable function and wording as the already mapped GIGABYTE record.",
        "The shorter heading omits 'HD' but does not change the documented function.",
        (source(5620, 7, 20, "Audio Controller", "Settings > IO Ports > Audio Controller", "GIGABYTE enables or disables the onboard audio function."),),
    ),
    extension(
        "dmi-aspm", "DMI ASPM",
        "ASUS DMI ASPM Control explicitly configures DMI ASPM support with Disabled, Auto, and ASPM L1 choices, matching the existing DMI-link identity.",
        "DMI Gen3 ASPM remains separate and unmapped because the same page exposes it as a distinct control.",
        (source(7482, 11, 50, "DMI ASPM Control", "Advanced > System Agent (SA) Configuration > SA - PCI Express > DMI ASPM Control", "ASUS configures DMI ASPM support independently of the adjacent DMI Gen3 ASPM control."),),
    ),
    extension(
        "amd-eco-mode", "AMD ECO Mode",
        "The ASUS Ai Tweaker record adjusts AMD CPU control limits through explicit cTDP profiles, matching the reviewed AMD ECO Mode function.",
        "Available cTDP choices remain processor-, cooling-, and firmware-dependent.",
        (source(5955, 8, 30, "AMD ECO Mode", "Ai Tweaker > Precision Boost Overdrive > AMD ECO Mode", "ASUS selects AMD ECO cTDP profiles and documents their PPT, TDC, and EDC limits."),),
    ),
    extension(
        "tx-burst-length", "TX Burst Length",
        "The ASUS Nitro TX Burst Length record selects the same 1x, 2x, 4x, or 8x DDR write-training pattern length as the existing cross-vendor identity.",
        "The Nitro prefix identifies the ASUS menu presentation; the documented training function is unchanged.",
        (source(6535, 8, 87, "Nitro TX Burst Length", "Advanced > AMD Overclocking > DDR and Infinity Fabric Frequency/Timings > DDR Options > DDR Training Options > Nitro TX Burst Length", "ASUS selects the DQ write-training pattern length and documents the robustness/runtime tradeoff."),),
    ),
    extension(
        "read-drift-adjustment", "Read Drift Adjustment",
        "ASUS exposes the same read-drift choices for P0 and P0-3 scopes alongside the already mapped generic control.",
        "The aliases preserve their P0 and P0-3 applicability scopes.",
        (
            source(5841, 8, 22, "Read Drift Adjustment P0", "Ai Tweaker > DRAM Timing Control > DRAM Signal Control > DDR Turnaround Times > Read Drift Adjustment P0", "ASUS selects read-drift adjustment minus 4 through plus 4 for P0."),
            source(6351, 8, 72, "Read Drift Adjustment P0-3", "Advanced > AMD CBS > UMC Common Options > DDR Options > DDR Memory Features > DDR Turnaround Times > Read Drift Adjustment P0-3", "ASUS selects the same read-drift adjustment range for P0-3."),
        ),
    ),
    extension(
        "write-drift-adjustment", "Write Drift Adjustment",
        "ASUS exposes the same write-drift choices for P0 and P0-3 scopes alongside the already mapped generic control.",
        "The aliases preserve their P0 and P0-3 applicability scopes.",
        (
            source(5842, 8, 22, "Write Drift Adjustment P0", "Ai Tweaker > DRAM Timing Control > DRAM Signal Control > DDR Turnaround Times > Write Drift Adjustment P0", "ASUS selects write-drift adjustment minus 4 through plus 4 for P0."),
            source(6353, 8, 72, "Write Drift Adjustment P0-3", "Advanced > AMD CBS > UMC Common Options > DDR Options > DDR Memory Features > DDR Turnaround Times > Write Drift Adjustment P0-3", "ASUS selects the same write-drift adjustment range for P0-3."),
        ),
    ),
    extension(
        "dram-vdd-voltage", "DRAM VDD Voltage",
        "Four ASUS records explicitly control the DRAM IC VDD portion in synchronized or per-PMIC presentations, matching the reviewed primary DRAM-supply identity.",
        "PMIC scope and available ranges remain source-specific.",
        (
            source(6093, 8, 47, "Memory VDD Voltage", "Ai Tweaker > PMIC Voltages > Memory VDD Voltage", "ASUS controls the DRAM IC VDD power portion."),
            source(7459, 11, 45, "Memory VDD Voltage", "Ai Tweaker > Advanced Memory Voltages > PMIC Voltages > Memory VDD Voltage", "ASUS controls synchronized memory VDD from 0.800 V to 1.435 V."),
            source(7468, 11, 46, "PMIC0-3 Memory VDD Voltage", "Ai Tweaker > Advanced Memory Voltages > PMIC Voltages > PMIC0-3 Memory VDD Voltage", "ASUS controls the same VDD rail per PMIC."),
            source(8122, 12, 49, "PMIC0-3 DRAM VDD Voltage", "Ai Tweaker > Advanced Memory Voltages > PMIC Voltages > PMIC0-3 DRAM VDD Voltage", "ASUS controls the same DRAM VDD rail per PMIC."),
        ),
    ),
    extension(
        "dram-vddq-voltage", "DRAM VDDQ Voltage",
        "Four ASUS records explicitly control the DRAM IC data-portion VDDQ rail in synchronized or per-PMIC presentations, matching the reviewed DRAM I/O-supply identity.",
        "PMIC scope and available ranges remain source-specific.",
        (
            source(6094, 8, 47, "Memory VDDQ Voltage", "Ai Tweaker > PMIC Voltages > Memory VDDQ Voltage", "ASUS controls the DRAM IC data-portion supply."),
            source(7460, 11, 45, "Memory VDDQ Voltage", "Ai Tweaker > Advanced Memory Voltages > PMIC Voltages > Memory VDDQ Voltage", "ASUS controls synchronized memory VDDQ from 0.800 V to 1.435 V."),
            source(7469, 11, 46, "PMIC0-3 Memory VDDQ Voltage", "Ai Tweaker > Advanced Memory Voltages > PMIC Voltages > PMIC0-3 Memory VDDQ Voltage", "ASUS controls the same VDDQ rail per PMIC."),
            source(8123, 12, 49, "PMIC0-3 DRAM VDDQ Voltage", "Ai Tweaker > Advanced Memory Voltages > PMIC Voltages > PMIC0-3 DRAM VDDQ Voltage", "ASUS controls the same DRAM VDDQ rail per PMIC."),
        ),
    ),
    extension(
        "dram-vpp-voltage", "DRAM VPP Voltage",
        "Four ASUS records explicitly control the DRAM activating-power VPP rail in synchronized or per-PMIC presentations, matching the reviewed DRAM VPP identity.",
        "PMIC scope and available ranges remain source-specific.",
        (
            source(1059, 3, 54, "PMIC0-1 DRAM VPP Voltage", "Ai Tweaker > PMIC Voltages > PMIC0-1 DRAM VPP Voltage", "ASUS identifies the per-PMIC DRAM activating-power supply."),
            source(7461, 11, 45, "Memory VPP Voltage", "Ai Tweaker > Advanced Memory Voltages > PMIC Voltages > Memory VPP Voltage", "ASUS controls synchronized memory VPP from 1.500 V to 2.135 V."),
            source(7470, 11, 46, "PMIC0-3 Memory VPP Voltage", "Ai Tweaker > Advanced Memory Voltages > PMIC Voltages > PMIC0-3 Memory VPP Voltage", "ASUS controls the same activating-power rail per PMIC."),
            source(8124, 12, 50, "PMIC0-3 DRAM VPP Voltage", "Ai Tweaker > Advanced Memory Voltages > PMIC Voltages > PMIC0-3 DRAM VPP Voltage", "ASUS controls the same DRAM VPP rail per PMIC."),
        ),
    ),
    extension(
        "save-all-secure-boot-variables", "Save All Secure Boot Variables",
        "The MSI contextual source record saves PK, KEK, db, dbt, and dbx under Key Management, matching the existing ASUS and MSI Secure Boot export identity.",
        "The 'Key Management' prefix preserves extraction context and is not a different action.",
        (source(5209, 6, 21, "Key Management Save All Secure Boot Variables", "Settings > Security > Secure Boot > Key Management > Save All Secure Boot Variables", "MSI saves all listed Secure Boot variables."),),
    ),
    extension(
        "enroll-efi-image", "Enroll Efi Image",
        "The MSI contextual source record enrolls a PE image SHA-256 certificate into db, matching the existing ASRock and MSI identity.",
        "The 'Key Management' prefix preserves extraction context and is not a different action.",
        (source(5210, 6, 21, "Key Management Enroll EFI Image", "Settings > Security > Secure Boot > Key Management > Enroll Efi Image", "MSI enrolls a PE image SHA-256 certificate into the authorized signature database."),),
    ),
    extension(
        "ppc-adjustment", "PPC Adjustment",
        "The GIGABYTE B550 record differs only by an availability-note suffix and explicitly fixes the CPU P-state, matching the reviewed PPC Adjustment identity.",
        "Availability remains CPU-dependent as Note 1 states.",
        (source(6764, 9, 7, "PPC Adjustment (Note 1)", "Tweaker > Advanced CPU Settings > PPC Adjustment (Note 1)", "GIGABYTE fixes the CPU P-state."),),
    ),
    extension(
        "power-supply-idle-control", "Power Supply Idle Control",
        "The GIGABYTE B550 record differs only by an availability-note suffix and exposes the same Typical Current Idle, Low Current Idle, and Auto behavior.",
        "Availability remains CPU-dependent as Note 1 states.",
        (source(6766, 9, 7, "Power Supply Idle Control (Note 1)", "Tweaker > Advanced CPU Settings > Power Supply Idle Control (Note 1)", "GIGABYTE selects Package C6 low-current or typical-current idle behavior."),),
    ),
    extension(
        "sata-controllers", "SATA Controller(s)",
        "Two GIGABYTE AMD manuals describe Chipset SATA Port Enable as enabling or disabling the integrated SATA controllers, exactly matching the reviewed SATA-controller identity.",
        "The alias is chipset-oriented wording; individual SATA-port hot-plug controls remain separate.",
        (
            source(98, 1, 16, "Chipset SATA Port Enable", "Settings > IO Ports > SATA Configuration > Chipset SATA Port Enable", "GIGABYTE enables or disables the integrated SATA controllers."),
            source(6817, 9, 12, "Chipset SATA Port Enable", "Settings > IO Ports > SATA Configuration > Chipset SATA Port Enable", "GIGABYTE enables or disables the integrated SATA controllers."),
        ),
    ),
    extension(
        "pch-1-05v-voltage", "PCH 1.05V Voltage",
        "ASUS PCH 1.05V Primary Voltage explicitly configures the PCH 1.05 V rail with the same range as the existing ASUS record.",
        "The 'Primary' qualifier is preserved in the source alias.",
        (source(8068, 12, 42, "PCH 1.05V Primary Voltage", "Ai Tweaker > Tweaker's Paradise > PCH 1.05V Primary Voltage", "ASUS configures the PCH 1.05 V primary rail from 0.800 V to 1.600 V."),),
    ),
)

EXCLUDED_IDS = {
    36, 47, 77, 283, 374, 419, 435, 447, 491, 572, 1033, 1134, 4741,
    4795, 4901, 5137, 5284, 5292, 5323, 5342, 5528, 5657, 5779, 6111,
    6158, 6161, 6288, 6306, 6321, 6333, 6378, 6442, 6501, 6580, 6690,
    7392, 7395, 7455, 7562, 7657, 8107, 8320,
}
REJECTED_DUPLICATE_IDS = {4901}
BASELINE_COUNTS = {
    CanonicalSetting: 331,
    CanonicalSettingAlias: 367,
    SettingCanonicalMapping: 1178,
    CanonicalMappingEvent: 1178,
    CanonicalIdentityEvent: 346,
    CanonicalSettingClassification: 331,
    CanonicalClassificationEvent: 623,
    SettingMenuContext: 975,
    SettingMenuContextEvent: 975,
    SettingMenuContextCitation: 1049,
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
    return int(session.scalar(select(func.count()).select_from(Setting).outerjoin(SettingCanonicalMapping).where(Setting.status == "approved", SettingCanonicalMapping.setting_id.is_(None))) or 0)


def _verify_preflight(session) -> None:
    if config.APP_VERSION != "1.30.0":
        raise RuntimeError(f"Expected v1.30 baseline, found {config.APP_VERSION}.")
    if not BACKUP_PATH.is_file() or _sha256(BACKUP_PATH) != BACKUP_SHA256:
        raise RuntimeError("The verified pre-v1.31 backup is missing or changed.")
    if inspect_backup(BACKUP_PATH).database_revision != config.EXPECTED_DATABASE_REVISION:
        raise RuntimeError("The pre-v1.31 backup has an unexpected revision.")
    if session.scalar(text("SELECT version_num FROM alembic_version")) != config.EXPECTED_DATABASE_REVISION:
        raise RuntimeError("The live schema revision changed.")
    if session.scalar(text("PRAGMA integrity_check")) != "ok" or list(session.execute(text("PRAGMA foreign_key_check"))):
        raise RuntimeError("Database integrity failed before Wave 26.")
    if _unmapped_count(session) != 2840:
        raise RuntimeError(f"Expected 2,840 unmapped settings, found {_unmapped_count(session)}.")
    for model, expected in BASELINE_COUNTS.items():
        if _count(session, model) != expected:
            raise RuntimeError(f"{model.__tablename__} baseline changed.")
    for pin in MANUALS:
        manual = session.get(Manual, pin.manual_id)
        actual = ((manual.vendor_id, manual.sha256, manual.source_url, manual.page_count) if manual else None)
        if actual != (pin.vendor_id, pin.sha256, pin.source_url, pin.page_count):
            raise RuntimeError(f"Pinned manual {pin.manual_id} changed: {actual!r}.")
    sources = _sources()
    if len(IDENTITIES) != 16 or any(seed.create_identity for seed in IDENTITIES):
        raise RuntimeError("Wave 26 must extend exactly 16 existing identities.")
    if len(sources) != 28 or len({item.setting_id for item in sources}) != 28:
        raise RuntimeError("Wave 26 must contain 28 unique source records.")
    if EXCLUDED_IDS.intersection(item.setting_id for item in sources):
        raise RuntimeError("A known excluded record entered Wave 26.")
    for seed in IDENTITIES:
        detail = library.canonical_detail(session, seed.canonical_id)
        if detail is None or len({item.vendor_id for item in detail.source_settings}) < 2:
            raise RuntimeError(f"Existing identity lacks cross-vendor support: {seed.canonical_id}.")
        for item in seed.sources:
            setting = session.get(Setting, item.setting_id)
            manual = session.get(Manual, item.manual_id)
            actual = ((setting.manual_id, setting.edited_page_number or setting.page_number, normalize_alias(effective_name(setting))) if setting else None)
            expected = (item.manual_id, item.page_number, normalize_alias(item.expected_name))
            if actual != expected:
                raise RuntimeError(f"Pinned source {item.setting_id} changed: {actual!r} != {expected!r}.")
            reasons = canonicalization.source_trust_reasons(setting, manual)
            if reasons:
                raise RuntimeError(f"Source {item.setting_id} failed trust gates: {' '.join(reasons)}")
            if session.get(SettingCanonicalMapping, item.setting_id) is not None or session.get(SettingMenuContext, item.setting_id) is not None:
                raise RuntimeError(f"Source {item.setting_id} is no longer untouched.")
            alias = session.get(CanonicalSettingAlias, normalize_alias(effective_name(setting)))
            if alias is not None and alias.canonical_id != seed.canonical_id:
                raise RuntimeError(f"Alias collision for source {item.setting_id}: {alias.canonical_id}.")
    for setting_id in EXCLUDED_IDS - REJECTED_DUPLICATE_IDS:
        if session.get(SettingCanonicalMapping, setting_id) is not None:
            raise RuntimeError(f"Known excluded source {setting_id} was unexpectedly mapped.")


def _mapping_note(session, seed: IdentitySeed, item: SourcePin) -> str:
    manual = session.get(Manual, item.manual_id)
    return f"v1.31 Wave 26 reviewer decision; confidence={CONFIDENCE_BASIS_POINTS / 100:.2f}%. {seed.rationale} Official source: {manual.vendor} manual {manual.id}, page {item.page_number}, {manual.source_url}; reviewed menu path: {item.menu_path}. Remaining uncertainty: {seed.uncertainty}"


def _apply(session) -> None:
    for seed in IDENTITIES:
        for item in seed.sources:
            setting = session.get(Setting, item.setting_id)
            canonical_identity_review.review_menu_context(
                session, setting.id, menu_path=item.menu_path,
                source_page_number=item.page_number,
                evidence_excerpt=item.evidence_excerpt,
                note="v1.31 Wave 26 official-manual name, function, options, scope, and menu hierarchy visually verified.",
                actor=ACTOR, commit=False,
            )
            canonical_settings.map_setting(
                session, setting, canonical_id=seed.canonical_id,
                note=_mapping_note(session, seed, item), actor=ACTOR,
                mapping_method=canonical_settings.MAPPING_REVIEWER, commit=False,
            )


def _verify_final(session, baseline: dict[type, int]) -> None:
    deltas = {
        CanonicalSetting: 0,
        CanonicalSettingAlias: 0,
        SettingCanonicalMapping: 28,
        CanonicalMappingEvent: 28,
        CanonicalIdentityEvent: 0,
        CanonicalSettingClassification: 0,
        CanonicalClassificationEvent: 0,
        SettingMenuContext: 28,
        SettingMenuContextEvent: 28,
        SettingMenuContextCitation: 28,
    }
    failures = [f"{model.__tablename__} append-only delta mismatch" for model, delta in deltas.items() if _count(session, model) != baseline[model] + delta]
    if _unmapped_count(session) != 2812:
        failures.append(f"expected 2,812 unmapped, found {_unmapped_count(session)}")
    for seed in IDENTITIES:
        expected = {item.setting_id for item in seed.sources}
        detail = library.canonical_detail(session, seed.canonical_id)
        public = {item.setting_id for item in detail.source_settings} if detail else set()
        if not expected.issubset(public):
            failures.append(f"{seed.canonical_id} public projection mismatch")
        elif any(not item.menu_path for item in detail.source_settings if item.setting_id in expected):
            failures.append(f"{seed.canonical_id} public menu path missing")
    if any(session.get(SettingCanonicalMapping, setting_id) is not None for setting_id in EXCLUDED_IDS):
        failures.append("a known excluded record was mapped")
    if session.scalar(text("PRAGMA integrity_check")) != "ok":
        failures.append("SQLite integrity_check failed")
    foreign_keys = list(session.execute(text("PRAGMA foreign_key_check")))
    if foreign_keys:
        failures.append(f"foreign-key violations: {foreign_keys!r}")
    if failures:
        raise RuntimeError("Wave 26 verification failed:\n- " + "\n- ".join(failures))


def _payload(session) -> dict[str, object]:
    decisions = []
    for seed in IDENTITIES:
        sources = []
        for item in seed.sources:
            setting = session.get(Setting, item.setting_id)
            manual = session.get(Manual, item.manual_id)
            sources.append({
                "setting_id": setting.id,
                "vendor_id": manual.vendor_id,
                "manual_id": manual.id,
                "source_name": effective_name(setting),
                "source_description": effective_description(setting),
                "page_number": item.page_number,
                "menu_path": item.menu_path,
                "official_url": manual.source_url,
                "confidence_percent": CONFIDENCE_BASIS_POINTS / 100,
            })
        decisions.append({
            "decision": "existing_identity_extension",
            "canonical_id": seed.canonical_id,
            "canonical_name": seed.canonical_name,
            "rationale": seed.rationale,
            "remaining_uncertainty": seed.uncertainty,
            "sources": sources,
        })
    rows = session.execute(select(Setting, Manual).join(Manual).outerjoin(SettingCanonicalMapping).where(Setting.status == "approved", SettingCanonicalMapping.setting_id.is_(None)).order_by(Setting.id))
    remaining = [{
        "setting_id": setting.id,
        "vendor_id": manual.vendor_id,
        "manual_id": manual.id,
        "source_name": effective_name(setting),
        "page_number": setting.edited_page_number or setting.page_number,
        "review_status": "pending_evidence_review",
        "reason": "Not approved in Wave 26; requires a later evidence disposition.",
    } for setting, manual in rows]
    audit = build_public_audit(session)
    if audit["status"] != "pass":
        raise RuntimeError("Public BIOS knowledge audit failed after Wave 26.")
    audit["app_version"] = RELEASE_VERSION
    return {
        "milestone": "v1.31 literal-extension canonicalization Wave 26",
        "generated_at": utc_now_iso(),
        "actor": ACTOR,
        "backup": {"path": str(BACKUP_PATH), "sha256": BACKUP_SHA256},
        "database_revision": config.EXPECTED_DATABASE_REVISION,
        "summary": {
            "new_canonical_settings": 0,
            "existing_canonical_settings_extended": len(IDENTITIES),
            "total_mappings_created": len(_sources()),
            "reviewed_menu_contexts": len(_sources()),
            "remaining_manual_review": len(remaining),
        },
        "official_manuals": [asdict(pin) for pin in MANUALS],
        "approved_decisions": decisions,
        "intentional_exclusions": [
            {"setting_ids": sorted(EXCLUDED_IDS), "reason": "Preserved ambiguity, collision, rejection, contradiction, source-display error, or evidence gap."},
            {"setting_ids": [7562], "reason": "Official page spells DTBT Controller 0 correctly, but the approved source display contains an OCR error; correct and recertify before mapping."},
            {"setting_ids": [7483, 8138], "reason": "DMI Gen3 ASPM is visibly separate from DMI ASPM Control and cannot be merged into the generic DMI identity."},
            {"setting_ids": [5619], "reason": "The #2 record controls a distinct onboard LAN instance and remains separate from the primary controller."},
        ],
        "remaining_manual_review": remaining,
        "public_knowledge_audit": audit,
    }


def _markdown(payload: dict[str, object]) -> str:
    summary = payload["summary"]
    lines = [
        "# TuneForge v1.31 Literal Extensions Wave 26",
        "",
        "## Outcome",
        "",
        f"- New canonical settings: {summary['new_canonical_settings']}",
        f"- Existing canonical settings extended: {summary['existing_canonical_settings_extended']}",
        f"- New verified mappings: {summary['total_mappings_created']}",
        f"- New reviewed menu contexts: {summary['reviewed_menu_contexts']}",
        f"- Remaining approved unmapped settings: {summary['remaining_manual_review']}",
        "",
        "Every approved decision uses visually reviewed official ASUS, ASRock, GIGABYTE, or MSI manual pages. No community source or model-memory claim was used.",
        "",
        "## Approved decisions",
        "",
    ]
    for decision in payload["approved_decisions"]:
        vendors = sorted({item["vendor_id"] for item in decision["sources"]})
        label = "mapping" if len(decision["sources"]) == 1 else "mappings"
        lines.append(f"- **{decision['canonical_name']}** - {len(decision['sources'])} {label} from {', '.join(vendors)}. {decision['rationale']}")
    lines.extend([
        "",
        "## Deliberate refusals",
        "",
        "- DTBT source 7562 remains unmapped because its approved display name contains an OCR error even though the official page is correct.",
        "- DMI Gen3 ASPM sources 7483 and 8138 remain separate from generic DMI ASPM.",
        "- Onboard LAN Controller #2 source 5619 remains distinct from the primary controller instance.",
        "",
        "The complete decision report and pending backlog are stored in `data/reports/v1.31-literal-extensions-wave26.json`.",
        "",
    ])
    return "\n".join(lines)


def _write_temp(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(content, encoding="utf-8")
    return temporary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Commit the pinned v1.31 Wave 26 batch")
    args = parser.parse_args()
    with SessionLocal() as session:
        _verify_preflight(session)
        print("Preflight passed: backup, revision, counts, nine manuals, 28 source pins, exclusions, and trust gates match.")
        print("Planned outcome: 16 existing identity extensions and 28 mappings; no new identity.")
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
            json_temp = _write_temp(REPORT_JSON, json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True) + "\n")
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
        print("Applied: new_identities=0, existing_extensions=16, mappings=28, menu_contexts=28.")
        print(f"Remaining approved unmapped: {payload['summary']['remaining_manual_review']}.")
        print(f"JSON report: {REPORT_JSON}")
        print(f"Summary report: {REPORT_MARKDOWN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
