# ruff: noqa: E501
"""Apply TuneForge's pinned v1.10 primary DRAM timing Wave 5.

Dry-run is the default. The batch contains 13 canonical identities and 77
approved Source Settings visually verified in official ASUS and MSI manuals.
All writes preserve the existing reviewer-controlled, citation-first, and
append-only architecture.
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
BACKUP_PATH = ROOT / "data" / "backups" / "tuneforge-v1.10-pre-wave5.zip"
BACKUP_SHA256 = "e04781df2c45f3f084580b95b53d3709a517a453b240f1dceeff5975d414a3ae"
REPORT_JSON = ROOT / "data" / "reports" / "v1.10-primary-dram-timing-wave5.json"
REPORT_MARKDOWN = ROOT / "docs" / "v1.10-primary-dram-timing-wave5.md"
ACTOR = "v1.10-primary-dram-timing-official-evidence-reviewer"
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

MAIN_TIMINGS = {"tCL", "tRCD", "tRP", "tRAS", "tRC", "tWR", "tRFC1", "tRFC2", "tRFCSB"}


def _menu_path(setting_id: int, manual_id: int, page: int, name: str) -> str:
    if manual_id == 5:
        branch = "Main Timing Configuration" if name in MAIN_TIMINGS else "Sub Timing Configuration"
        parent = f"OC > Advanced DRAM Configuration > {branch}"
    elif manual_id == 6 and 5343 <= setting_id <= 5351:
        parent = "OC > DRAM Setting > A-XMP User Profile"
    elif manual_id == 6 and 5363 <= setting_id <= 5371:
        parent = "OC > DRAM Setting > EXPO User Profile"
    elif manual_id == 6:
        branch = "Main Timing Configuration" if name in MAIN_TIMINGS else "Sub Timing Configuration"
        parent = f"OC > Advanced DRAM Configuration > {branch}"
    elif manual_id == 8 and page in {20, 21}:
        parent = "Ai Tweaker > DRAM Timing Control"
    elif manual_id == 8 and page in {62, 63, 64}:
        branch = "DDR SPD Timing" if name in MAIN_TIMINGS else "DDR Non-SPD Timing"
        parent = f"Advanced > AMD CBS > UMC Common Options > DDR Options > DDR Timing Configuration > {branch}"
    elif manual_id == 8 and page in {79, 80, 81, 82}:
        branch = "DDR SPD Timing" if name in MAIN_TIMINGS else "DDR Non-SPD Timing"
        parent = f"Advanced > AMD PBS > DDR and Infinity Fabric Frequency/Timings > DDR Options > DDR Timing Configuration > {branch}"
    else:
        raise ValueError(f"No reviewed menu path for setting {setting_id}.")
    return f"{parent} > {name}"


def s(setting_id: int, manual_id: int, page: int, name: str) -> SourcePin:
    return SourcePin(setting_id, manual_id, page, _menu_path(setting_id, manual_id, page, name))


IDENTITIES = (
    IdentitySeed(
        "tcl",
        "tCL",
        "Sets DRAM CAS (Column Address Strobe) latency.",
        "ASUS and MSI use the exact normalized tCL name and independently define the same CAS latency timing across global, XMP, EXPO, AMD CBS, and AMD PBS branches.",
        "Valid values, stepping, and trained defaults vary by memory generation and profile.",
        (
            s(4857, 5, 48, "tCL"),
            s(5343, 6, 33, "tCL"),
            s(5363, 6, 34, "tCL"),
            s(5373, 6, 35, "tCL"),
            s(5803, 8, 20, "Tcl"),
            s(6228, 8, 62, "Tcl"),
            s(6448, 8, 79, "Tcl"),
        ),
    ),
    IdentitySeed(
        "trcd",
        "tRCD",
        "Sets the DRAM row-address-strobe to column-address-strobe delay.",
        "ASUS and MSI use the exact normalized tRCD name and define the same RAS-to-CAS delay in every reviewed branch.",
        "ASUS describes a read-delay form in its AMD branches; platform-specific write-delay controls may be separate.",
        (
            s(4858, 5, 48, "tRCD"),
            s(5344, 6, 33, "tRCD"),
            s(5364, 6, 34, "tRCD"),
            s(5374, 6, 35, "tRCD"),
            s(5804, 8, 20, "Trcd"),
            s(6230, 8, 62, "Trcd"),
            s(6450, 8, 79, "Trcd"),
        ),
    ),
    IdentitySeed(
        "trp",
        "tRP",
        "Sets the DRAM row precharge delay time.",
        "ASUS and MSI use the exact normalized tRP name and independently define the same row-precharge timing.",
        "Valid values, stepping, and trained defaults vary by memory generation and profile.",
        (
            s(4859, 5, 48, "tRP"),
            s(5345, 6, 33, "tRP"),
            s(5365, 6, 34, "tRP"),
            s(5375, 6, 35, "tRP"),
            s(5805, 8, 20, "Trp"),
            s(6232, 8, 62, "Trp"),
            s(6452, 8, 80, "Trp"),
        ),
    ),
    IdentitySeed(
        "tras",
        "tRAS",
        "Sets the DRAM row active-to-precharge time.",
        "ASUS and MSI use the exact normalized tRAS name and independently define the same row-active or active-to-precharge timing.",
        "Valid values, stepping, and trained defaults vary by memory generation and profile.",
        (
            s(4860, 5, 48, "tRAS"),
            s(5346, 6, 33, "tRAS"),
            s(5366, 6, 35, "tRAS"),
            s(5376, 6, 35, "tRAS"),
            s(5806, 8, 20, "Tras"),
            s(6234, 8, 63, "Tras"),
            s(6454, 8, 80, "Tras"),
        ),
    ),
    IdentitySeed(
        "trc",
        "tRC",
        "Sets the DRAM row-cycle or active-to-active/refresh delay time.",
        "ASUS and MSI use the exact normalized tRC name and define the same row-cycle, active-to-active, or refresh interval.",
        "Valid values and profile behavior vary by memory generation.",
        (
            s(5347, 6, 33, "tRC"),
            s(5367, 6, 35, "tRC"),
            s(5377, 6, 35, "tRC"),
            s(5808, 8, 20, "Trc"),
            s(6236, 8, 63, "Trc"),
            s(6456, 8, 80, "Trc"),
        ),
    ),
    IdentitySeed(
        "twr",
        "tWR",
        "Sets the minimum DRAM write-recovery time before a subsequent precharge.",
        "ASUS and MSI use the exact normalized tWR name and define the same write-recovery timing.",
        "One short ASUS summary labels it as a write-to-read delay, but both detailed ASUS branches and every MSI source define write recovery; the detailed official definitions control this identity.",
        (
            s(4865, 5, 48, "tWR"),
            s(5348, 6, 33, "tWR"),
            s(5368, 6, 35, "tWR"),
            s(5378, 6, 35, "tWR"),
            s(5809, 8, 20, "Twr"),
            s(6238, 8, 63, "Twr"),
            s(6458, 8, 80, "Twr"),
        ),
    ),
    IdentitySeed(
        "trfc1",
        "tRFC1",
        "Sets the primary DRAM refresh-recovery delay time.",
        "ASUS and MSI use the exact normalized tRFC1 name and define refresh-recovery delay in global, profile, AMD CBS, and AMD PBS branches.",
        "Valid values and the memory technologies to which tRFC1 applies vary by platform.",
        (
            s(5349, 6, 34, "tRFC1"),
            s(5369, 6, 35, "tRFC1"),
            s(5379, 6, 35, "tRFC1"),
            s(5811, 8, 20, "Trfc1"),
            s(6240, 8, 63, "Trfc1"),
            s(6460, 8, 80, "Trfc1"),
        ),
    ),
    IdentitySeed(
        "trfc2",
        "tRFC2",
        "Sets the secondary DRAM refresh-recovery delay time.",
        "ASUS and MSI use the exact normalized tRFC2 name and detailed branches independently identify it as refresh-recovery delay.",
        "Valid values and the refresh mode associated with tRFC2 vary by platform.",
        (
            s(5350, 6, 34, "tRFC2"),
            s(5370, 6, 35, "tRFC2"),
            s(5380, 6, 35, "tRFC2"),
            s(5812, 8, 20, "Trfc2"),
            s(6242, 8, 63, "Trfc2"),
            s(6462, 8, 81, "Trfc2"),
        ),
    ),
    IdentitySeed(
        "trfcsb",
        "tRFCSB",
        "Sets the same-bank DRAM refresh-recovery delay time.",
        "ASUS and MSI use the exact normalized tRFCSB name and detailed branches independently define refresh-recovery delay for this same-bank timing.",
        "Valid values and same-bank refresh support vary by memory generation and controller.",
        (
            s(5351, 6, 34, "tRFCSB"),
            s(5371, 6, 35, "tRFCSB"),
            s(5381, 6, 35, "tRFCSB"),
            s(5813, 8, 20, "Trfcsb"),
            s(6244, 8, 64, "TrfcSb"),
            s(6464, 8, 81, "TrfcSb"),
        ),
    ),
    IdentitySeed(
        "trtp",
        "tRTP",
        "Sets the delay from a DRAM read command to a precharge command.",
        "ASUS and MSI use the exact normalized tRTP name and define the same read-to-precharge command delay.",
        "Valid values and trained defaults vary by memory generation and firmware branch.",
        (
            s(4870, 5, 48, "tRTP"),
            s(5383, 6, 35, "tRTP"),
            s(5814, 8, 20, "Trtp"),
            s(6247, 8, 64, "Trtp"),
            s(6466, 8, 81, "Trtp"),
        ),
    ),
    IdentitySeed(
        "trrdl",
        "tRRDL",
        "Sets the activate-to-activate delay for rows in the same bank group.",
        "ASUS and MSI use the exact normalized tRRDL name and independently define the same-bank-group activate-to-activate delay.",
        "The underscore spelling tRRD_L and allowed cycle ranges vary by firmware presentation.",
        (
            s(5384, 6, 36, "tRRDL"),
            s(5815, 8, 21, "TrrdL"),
            s(6249, 8, 64, "TrrdL"),
            s(6468, 8, 81, "TrrdL"),
        ),
    ),
    IdentitySeed(
        "trrds",
        "tRRDS",
        "Sets the activate-to-activate delay for rows in different bank groups.",
        "ASUS and MSI use the exact normalized tRRDS name and independently define the different-bank-group activate-to-activate delay.",
        "The underscore spelling tRRD_S and allowed cycle ranges vary by firmware presentation.",
        (
            s(5385, 6, 36, "tRRDS"),
            s(5816, 8, 21, "TrrdS"),
            s(6251, 8, 64, "TrrdS"),
            s(6470, 8, 81, "TrrdS"),
        ),
    ),
    IdentitySeed(
        "tfaw",
        "tFAW",
        "Sets the four-activate window during which four row activations are permitted within a rank.",
        "ASUS and MSI use the exact normalized tFAW name and independently define the same four-activate timing window.",
        "Valid values and trained defaults vary by memory generation and firmware branch.",
        (
            s(4871, 5, 48, "tFAW"),
            s(5386, 6, 36, "tFAW"),
            s(5817, 8, 21, "Tfaw"),
            s(6253, 8, 64, "Tfaw"),
            s(6472, 8, 82, "Tfaw"),
        ),
    ),
)

BASELINE_COUNTS = {
    CanonicalSetting: 114,
    CanonicalSettingAlias: 150,
    SettingCanonicalMapping: 524,
    CanonicalMappingEvent: 524,
    CanonicalIdentityEvent: 129,
    CanonicalSettingClassification: 114,
    CanonicalClassificationEvent: 189,
    SettingMenuContext: 321,
    SettingMenuContextEvent: 321,
    SettingMenuContextCitation: 395,
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
    return tuple(source for identity in IDENTITIES for source in identity.sources)


def _verify_preflight(session) -> None:
    if not BACKUP_PATH.is_file() or _sha256(BACKUP_PATH) != BACKUP_SHA256:
        raise RuntimeError("The verified pre-v1.10 backup is missing or changed.")
    if inspect_backup(BACKUP_PATH).database_revision != config.EXPECTED_DATABASE_REVISION:
        raise RuntimeError("The pre-v1.10 backup has an unexpected revision.")
    if (
        session.scalar(text("SELECT version_num FROM alembic_version"))
        != config.EXPECTED_DATABASE_REVISION
    ):
        raise RuntimeError("The live schema revision changed.")
    if session.scalar(text("PRAGMA integrity_check")) != "ok":
        raise RuntimeError("SQLite integrity_check failed before Wave 5.")
    if _unmapped_count(session) != 3494:
        raise RuntimeError(f"Expected 3,494 unmapped settings, found {_unmapped_count(session)}.")
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
    if (
        len(IDENTITIES) != 13
        or len(sources) != 77
        or len({source.setting_id for source in sources}) != 77
    ):
        raise RuntimeError("Wave 5 must contain 13 identities and 77 unique records.")
    for identity in IDENTITIES:
        if session.get(CanonicalSetting, identity.canonical_id) is not None:
            raise RuntimeError(f"Canonical identity already exists: {identity.canonical_id}.")
        if {pins[source.manual_id].vendor_id for source in identity.sources} != {"asus", "msi"}:
            raise RuntimeError(f"{identity.canonical_id} must have ASUS and MSI evidence.")
        for source in identity.sources:
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
            expected = (
                source.manual_id,
                source.page_number,
                normalize_alias(identity.canonical_name),
            )
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


def _mapping_note(session, identity: IdentitySeed, source: SourcePin) -> str:
    manual = session.get(Manual, source.manual_id)
    return (
        f"v1.10 Wave 5 reviewer decision; confidence={CONFIDENCE_BASIS_POINTS / 100:.2f}%. "
        f"{identity.rationale} Official source: {manual.vendor} manual {manual.id}, page {source.page_number}, "
        f"{manual.source_url}; reviewed menu path: {source.menu_path}. Remaining uncertainty: {identity.uncertainty}"
    )


def _apply(session) -> None:
    for identity_seed in IDENTITIES:
        identity = canonical_settings.create_identity(
            session,
            name=identity_seed.canonical_name,
            canonical_id=identity_seed.canonical_id,
            description=identity_seed.description,
            actor=ACTOR,
            commit=False,
        )
        canonical_settings.classify_identity(
            session,
            identity,
            category_id="memory",
            risk_level="high",
            risk_rationale="Manual DRAM timing changes can prevent memory training, cause instability, or require firmware recovery.",
            actor=ACTOR,
            commit=False,
        )
        for source in identity_seed.sources:
            setting = session.get(Setting, source.setting_id)
            canonical_identity_review.review_menu_context(
                session,
                setting.id,
                menu_path=source.menu_path,
                source_page_number=source.page_number,
                evidence_excerpt=f"The official manual page defines {effective_name(setting)} with the primary or secondary DRAM timing behavior represented by this reviewed hierarchy.",
                note="v1.10 Wave 5 official-manual page, behavior, profile scope, and ordered timing hierarchy visually verified.",
                actor=ACTOR,
                commit=False,
            )
            canonical_settings.map_setting(
                session,
                setting,
                canonical_id=identity_seed.canonical_id,
                note=_mapping_note(session, identity_seed, source),
                actor=ACTOR,
                mapping_method=canonical_settings.MAPPING_REVIEWER,
                commit=False,
            )


def _verify_final(session, baseline: dict[type, int]) -> None:
    deltas = {
        CanonicalSetting: 13,
        CanonicalSettingAlias: 13,
        SettingCanonicalMapping: 77,
        CanonicalMappingEvent: 77,
        CanonicalIdentityEvent: 13,
        CanonicalSettingClassification: 13,
        CanonicalClassificationEvent: 26,
        SettingMenuContext: 77,
        SettingMenuContextEvent: 77,
        SettingMenuContextCitation: 77,
    }
    failures = []
    for model, delta in deltas.items():
        if _count(session, model) != baseline[model] + delta:
            failures.append(f"{model.__tablename__} append-only delta mismatch")
    if _unmapped_count(session) != 3417:
        failures.append(f"expected 3,417 unmapped, found {_unmapped_count(session)}")
    for identity in IDENTITIES:
        expected = {source.setting_id for source in identity.sources}
        detail = library.canonical_detail(session, identity.canonical_id)
        public = {item.setting_id for item in detail.source_settings} if detail else set()
        if not expected.issubset(public):
            failures.append(f"{identity.canonical_id} public projection mismatch")
        elif any(
            not item.menu_path for item in detail.source_settings if item.setting_id in expected
        ):
            failures.append(f"{identity.canonical_id} public menu path missing")
    if session.scalar(text("PRAGMA integrity_check")) != "ok":
        failures.append("SQLite integrity_check failed")
    foreign_keys = list(session.execute(text("PRAGMA foreign_key_check")))
    if foreign_keys:
        failures.append(f"foreign-key violations: {foreign_keys!r}")
    if failures:
        raise RuntimeError("Wave 5 verification failed:\n- " + "\n- ".join(failures))


def _build_payload(session) -> dict[str, object]:
    decisions = []
    for identity in IDENTITIES:
        sources = []
        for source in identity.sources:
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
                "canonical_id": identity.canonical_id,
                "canonical_name": identity.canonical_name,
                "description": identity.description,
                "rationale": identity.rationale,
                "remaining_uncertainty": identity.uncertainty,
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
            "reason": "Not approved in Wave 5; requires a later one-by-one evidence disposition.",
        }
        for setting, manual in remaining_rows
    ]
    audit = build_public_audit(session)
    if audit["status"] != "pass":
        raise RuntimeError("Public BIOS knowledge audit failed after Wave 5.")
    return {
        "milestone": "v1.10 primary DRAM timing canonicalization Wave 5",
        "generated_at": utc_now_iso(),
        "actor": ACTOR,
        "backup": {"path": str(BACKUP_PATH), "sha256": BACKUP_SHA256},
        "database_revision": config.EXPECTED_DATABASE_REVISION,
        "summary": {
            "new_canonical_settings": 13,
            "total_mappings_created": 77,
            "reviewed_menu_contexts": 77,
            "remaining_manual_review": len(remaining),
        },
        "official_manuals": [asdict(pin) for pin in MANUALS],
        "approved_decisions": decisions,
        "remaining_manual_review": remaining,
        "public_knowledge_audit": audit,
    }


def _markdown(payload: dict[str, object]) -> str:
    summary = payload["summary"]
    lines = [
        "# TuneForge v1.10 Primary DRAM Timing Wave 5",
        "",
        "## Outcome",
        "",
        f"- New canonical settings: {summary['new_canonical_settings']}",
        f"- New verified mappings: {summary['total_mappings_created']}",
        f"- New reviewed menu contexts: {summary['reviewed_menu_contexts']}",
        f"- Remaining approved unmapped settings: {summary['remaining_manual_review']}",
        "",
        "Every decision uses visually reviewed official ASUS and MSI pages. Exact normalized names, functional descriptions, timing semantics, profile scope, and menu hierarchy agree; no community source was required.",
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
            "The machine-readable report contains every approval and the complete pending backlog: `data/reports/v1.10-primary-dram-timing-wave5.json`.",
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
    parser.add_argument("--apply", action="store_true", help="Commit the pinned v1.10 Wave 5 batch")
    args = parser.parse_args()
    with SessionLocal() as session:
        _verify_preflight(session)
        print(
            "Preflight passed: backup, revision, counts, three manuals, 77 source pins, and trust gates match."
        )
        print("Planned outcome: 13 new identities and 77 mappings with reviewed menu contexts.")
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
        print("Applied: new_identities=13, mappings=77, menu_contexts=77.")
        print(f"Remaining approved unmapped: {payload['summary']['remaining_manual_review']}.")
        print(f"JSON report: {REPORT_JSON}")
        print(f"Summary report: {REPORT_MARKDOWN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
