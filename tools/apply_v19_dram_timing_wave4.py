# ruff: noqa: E501
"""Apply TuneForge's pinned v1.9 DRAM turnaround-timing Wave 4.

Dry-run is the default. The batch contains 10 canonical timing identities and
52 approved Source Settings visually verified across official ASRock, ASUS,
and MSI manuals. Every write is append-only, audited, and transactionally
rolled back if any preflight or post-apply invariant fails.
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
BACKUP_PATH = ROOT / "data" / "backups" / "tuneforge-v1.9-pre-wave4.zip"
BACKUP_SHA256 = "f956085ab4411f728f844b04f760afa86e7609fca8f105dbd78e5b0cf138a190"
REPORT_JSON = ROOT / "data" / "reports" / "v1.9-dram-timing-wave4.json"
REPORT_MARKDOWN = ROOT / "docs" / "v1.9-dram-timing-wave4.md"
ACTOR = "v1.9-dram-timing-official-evidence-reviewer"
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


def _path(manual_id: int, page: int, name: str) -> str:
    if manual_id == 2:
        parent = "OC Tweaker > DRAM Profile Configuration > DRAM Timing Configuration > Turn Around Timing Configuration"
    elif manual_id in {5, 6}:
        parent = "OC > Advanced DRAM Configuration > Turn Around Timing Configuration"
    elif manual_id == 8 and page == 21:
        parent = "Ai Tweaker > DRAM Timing Control > Turn Around Timing"
    elif manual_id == 8 and page in {65, 66, 67}:
        parent = "Advanced > AMD CBS > UMC Common Options > DDR Options > DDR Timing Configuration > DDR Non-SPD Timing"
    elif manual_id == 8 and page in {82, 83, 84}:
        parent = "Advanced > AMD PBS > DDR and Infinity Fabric Frequency/Timings > DDR Options > DDR Timing Configuration > DDR Non-SPD Timing"
    else:
        raise ValueError(f"No reviewed menu path for manual {manual_id}, page {page}.")
    return f"{parent} > {name}"


def s(setting_id: int, manual_id: int, page: int, name: str) -> SourcePin:
    return SourcePin(setting_id, manual_id, page, _path(manual_id, page, name))


IDENTITIES = (
    IdentitySeed(
        "trdrddd",
        "tRDRDDD",
        "Sets the read-to-read turnaround delay between accesses to different DIMMs.",
        "ASRock, ASUS, and MSI use the same normalized timing name and independently define read-to-read turnaround between different DIMMs or modules.",
        "Allowed cycle ranges and training defaults vary by memory generation and firmware branch.",
        (
            s(269, 2, 37, "TrdrdDd"),
            s(4881, 5, 49, "tRDRDDD"),
            s(5393, 6, 36, "tRDRDDD"),
            s(5823, 8, 21, "Trdrddd"),
            s(6265, 8, 66, "TrdrdDd"),
            s(6485, 8, 83, "TrdrdDd"),
        ),
    ),
    IdentitySeed(
        "trdrdsc",
        "tRDRDSC",
        "Sets the read-to-read turnaround delay within the same chip select.",
        "ASRock, ASUS, and MSI use the same normalized timing name and define read-to-read turnaround in the same chip select.",
        "Allowed cycle ranges and training defaults vary by memory generation and firmware branch.",
        (
            s(267, 2, 37, "TrdrdSc"),
            s(5391, 6, 36, "tRDRDSC"),
            s(5821, 8, 21, "TrdrdSc"),
            s(6261, 8, 65, "TrdrdSc"),
            s(6481, 8, 82, "TrdrdSc"),
        ),
    ),
    IdentitySeed(
        "trdrdscl",
        "tRDRDSCL",
        "Sets the read-to-read CAS-to-CAS delay in the same chip select and bank group.",
        "ASRock, ASUS, and MSI use the same normalized timing name and define the same-bank-group read-to-read CAS delay.",
        "Allowed cycle ranges and training defaults vary by memory generation and firmware branch.",
        (
            s(266, 2, 36, "TrdrdScL"),
            s(5390, 6, 36, "tRDRDSCL"),
            s(5820, 8, 21, "TrdrdScl"),
            s(6259, 8, 65, "TrdrdScL"),
            s(6479, 8, 82, "TrdrdScL"),
        ),
    ),
    IdentitySeed(
        "trdrdsd",
        "tRDRDSD",
        "Sets the read-to-read turnaround delay within the same DIMM.",
        "ASRock, ASUS, and MSI use the same normalized timing name and define read-to-read turnaround in the same DIMM.",
        "Allowed cycle ranges and training defaults vary by memory generation and firmware branch.",
        (
            s(268, 2, 37, "TrdrdSd"),
            s(5392, 6, 36, "tRDRDSD"),
            s(5822, 8, 21, "TrdrdSd"),
            s(6263, 8, 65, "TrdrdSd"),
            s(6483, 8, 83, "TrdrdSd"),
        ),
    ),
    IdentitySeed(
        "trdwr",
        "tRDWR",
        "Sets the read-to-write turnaround delay between a read burst and a following write burst.",
        "ASRock, ASUS, and MSI use the same normalized timing name and define the same read-to-write turnaround interval.",
        "Allowed cycle ranges and training defaults vary by memory generation and firmware branch.",
        (
            s(275, 2, 38, "Trdwr"),
            s(5399, 6, 36, "tRDWR"),
            s(5829, 8, 21, "Trdwr"),
            s(6277, 8, 67, "Trdwr"),
            s(6497, 8, 84, "Trdwr"),
        ),
    ),
    IdentitySeed(
        "twrrd",
        "tWRRD",
        "Sets the write-to-read turnaround delay between a write burst and a following read burst.",
        "ASRock, ASUS, and MSI use the same normalized timing name and define the same write-to-read turnaround interval.",
        "Allowed cycle ranges and training defaults vary by memory generation and firmware branch.",
        (
            s(274, 2, 37, "Twrrd"),
            s(5398, 6, 36, "tWRRD"),
            s(5828, 8, 21, "Twrrd"),
            s(6275, 8, 67, "Twrrd"),
            s(6495, 8, 84, "Twrrd"),
        ),
    ),
    IdentitySeed(
        "twrwrdd",
        "tWRWRDD",
        "Sets the write-to-write turnaround delay between accesses to different DIMMs.",
        "ASRock, ASUS, and MSI use the same normalized timing name and independently define write-to-write turnaround between different DIMMs or modules.",
        "Allowed cycle ranges and training defaults vary by memory generation and firmware branch.",
        (
            s(273, 2, 37, "TwrwrDd"),
            s(4885, 5, 49, "tWRWRDD"),
            s(5397, 6, 36, "tWRWRDD"),
            s(5827, 8, 21, "TwrwrDd"),
            s(6273, 8, 66, "TwrwrDd"),
            s(6493, 8, 84, "TwrwrDd"),
        ),
    ),
    IdentitySeed(
        "twrwrsc",
        "tWRWRSC",
        "Sets the write-to-write turnaround delay within the same chip select.",
        "ASRock, ASUS, and MSI use the same normalized timing name and define write-to-write turnaround in the same chip select.",
        "Allowed cycle ranges and training defaults vary by memory generation and firmware branch.",
        (
            s(271, 2, 37, "TwrwrSc"),
            s(5395, 6, 36, "tWRWRSC"),
            s(5825, 8, 21, "TwrwrSc"),
            s(6269, 8, 66, "TwrwrSc"),
            s(6489, 8, 83, "TwrwrSc"),
        ),
    ),
    IdentitySeed(
        "twrwrscl",
        "tWRWRSCL",
        "Sets the write-to-write CAS-to-CAS delay in the same chip select and bank group.",
        "ASRock, ASUS, and MSI use the same normalized timing name and define the same-bank-group write-to-write CAS delay.",
        "Allowed cycle ranges and training defaults vary by memory generation and firmware branch.",
        (
            s(270, 2, 37, "TwrwrScL"),
            s(5394, 6, 36, "tWRWRSCL"),
            s(5824, 8, 21, "TwrwrScl"),
            s(6267, 8, 66, "TwrwrScL"),
            s(6487, 8, 83, "TwrwrScL"),
        ),
    ),
    IdentitySeed(
        "twrwrsd",
        "tWRWRSD",
        "Sets the write-to-write turnaround delay within the same DIMM.",
        "ASRock, ASUS, and MSI use the same normalized timing name and define write-to-write turnaround in the same DIMM.",
        "Allowed cycle ranges and training defaults vary by memory generation and firmware branch.",
        (
            s(272, 2, 37, "TwrwrSd"),
            s(5396, 6, 36, "tWRWRSD"),
            s(5826, 8, 21, "TwrwrSd"),
            s(6271, 8, 66, "TwrwrSd"),
            s(6491, 8, 83, "TwrwrSd"),
        ),
    ),
)

BASELINE_COUNTS = {
    CanonicalSetting: 104,
    CanonicalSettingAlias: 140,
    SettingCanonicalMapping: 472,
    CanonicalMappingEvent: 472,
    CanonicalIdentityEvent: 119,
    CanonicalSettingClassification: 104,
    CanonicalClassificationEvent: 169,
    SettingMenuContext: 269,
    SettingMenuContextEvent: 269,
    SettingMenuContextCitation: 343,
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
        raise RuntimeError("The verified pre-v1.9 backup is missing or changed.")
    if inspect_backup(BACKUP_PATH).database_revision != config.EXPECTED_DATABASE_REVISION:
        raise RuntimeError("The pre-v1.9 backup has an unexpected revision.")
    if (
        session.scalar(text("SELECT version_num FROM alembic_version"))
        != config.EXPECTED_DATABASE_REVISION
    ):
        raise RuntimeError("The live schema revision changed.")
    if session.scalar(text("PRAGMA integrity_check")) != "ok":
        raise RuntimeError("SQLite integrity_check failed before Wave 4.")
    if _unmapped_count(session) != 3546:
        raise RuntimeError(f"Expected 3,546 unmapped settings, found {_unmapped_count(session)}.")
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
        len(IDENTITIES) != 10
        or len(sources) != 52
        or len({source.setting_id for source in sources}) != 52
    ):
        raise RuntimeError("Wave 4 must contain 10 identities and 52 unique source records.")
    for identity in IDENTITIES:
        if session.get(CanonicalSetting, identity.canonical_id) is not None:
            raise RuntimeError(f"Canonical identity already exists: {identity.canonical_id}.")
        if len({pins[source.manual_id].vendor_id for source in identity.sources}) != 3:
            raise RuntimeError(f"{identity.canonical_id} must have exactly three-vendor evidence.")
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
        f"v1.9 Wave 4 reviewer decision; confidence={CONFIDENCE_BASIS_POINTS / 100:.2f}%. "
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
                evidence_excerpt=f"The official manual page lists {effective_name(setting)} with the turnaround-timing behavior represented by this reviewed hierarchy.",
                note="v1.9 Wave 4 official-manual page, behavior, and ordered DRAM timing hierarchy visually verified.",
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
        CanonicalSetting: 10,
        CanonicalSettingAlias: 10,
        SettingCanonicalMapping: 52,
        CanonicalMappingEvent: 52,
        CanonicalIdentityEvent: 10,
        CanonicalSettingClassification: 10,
        CanonicalClassificationEvent: 20,
        SettingMenuContext: 52,
        SettingMenuContextEvent: 52,
        SettingMenuContextCitation: 52,
    }
    failures = []
    for model, delta in deltas.items():
        if _count(session, model) != baseline[model] + delta:
            failures.append(f"{model.__tablename__} append-only delta mismatch")
    if _unmapped_count(session) != 3494:
        failures.append(f"expected 3,494 unmapped, found {_unmapped_count(session)}")
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
        raise RuntimeError("Wave 4 verification failed:\n- " + "\n- ".join(failures))


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
            "reason": "Not approved in Wave 4; requires a later one-by-one evidence disposition.",
        }
        for setting, manual in remaining_rows
    ]
    audit = build_public_audit(session)
    if audit["status"] != "pass":
        raise RuntimeError("Public BIOS knowledge audit failed after Wave 4.")
    return {
        "milestone": "v1.9 DRAM turnaround-timing canonicalization Wave 4",
        "generated_at": utc_now_iso(),
        "actor": ACTOR,
        "backup": {"path": str(BACKUP_PATH), "sha256": BACKUP_SHA256},
        "database_revision": config.EXPECTED_DATABASE_REVISION,
        "summary": {
            "new_canonical_settings": 10,
            "total_mappings_created": 52,
            "reviewed_menu_contexts": 52,
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
        "# TuneForge v1.9 DRAM Turnaround-Timing Wave 4",
        "",
        "## Outcome",
        "",
        f"- New canonical settings: {summary['new_canonical_settings']}",
        f"- New verified mappings: {summary['total_mappings_created']}",
        f"- New reviewed menu contexts: {summary['reviewed_menu_contexts']}",
        f"- Remaining approved unmapped settings: {summary['remaining_manual_review']}",
        "",
        "Every decision uses visually reviewed official ASRock, ASUS, and MSI manual pages. Exact normalized names, functional descriptions, turnaround direction, DIMM/chip-select scope, and menu context all agree.",
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
            "The machine-readable report contains every approval and the complete pending backlog: `data/reports/v1.9-dram-timing-wave4.json`.",
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
    parser.add_argument("--apply", action="store_true", help="Commit the pinned v1.9 Wave 4 batch")
    args = parser.parse_args()
    with SessionLocal() as session:
        _verify_preflight(session)
        print(
            "Preflight passed: backup, revision, counts, four manuals, 52 source pins, and trust gates match."
        )
        print("Planned outcome: 10 new identities and 52 mappings with reviewed menu contexts.")
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
        print("Applied: new_identities=10, mappings=52, menu_contexts=52.")
        print(f"Remaining approved unmapped: {payload['summary']['remaining_manual_review']}.")
        print(f"JSON report: {REPORT_JSON}")
        print(f"Summary report: {REPORT_MARKDOWN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
