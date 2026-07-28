# ruff: noqa: E501
"""Apply TuneForge's pinned v1.17 DRAM electrical-controls Wave 12.

Dry-run is the default. The batch contains 25 canonical identities and 50
approved Source Settings visually verified in official ASRock, ASUS, and MSI
manuals. P-state-scoped controls remain separate from unscoped controls.
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
BACKUP_PATH = ROOT / "data" / "backups" / "tuneforge-v1.17-pre-wave12.zip"
BACKUP_SHA256 = "5fd52e6ed380f95c9ed84bb630d6b6575c5c18398d7298c2ed2b73dc162533cd"
REPORT_JSON = ROOT / "data" / "reports" / "v1.17-dram-electrical-controls-wave12.json"
REPORT_MARKDOWN = ROOT / "docs" / "v1.17-dram-electrical-controls-wave12.md"
ACTOR = "v1.17-dram-electrical-controls-official-evidence-reviewer"
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

ASROCK_PARENT = "Advanced > AMD CBS > UMC Common Options > DDR Options > DDR Bus Configuration"
ASUS_SIGNAL_PARENT = "Ai Tweaker > DRAM Timing Control > DRAM Signal Control"
MSI_PARENT = "OC > Advanced DRAM Configuration > DDR Bus Configuration"
ASUS_AMD_CBS_PARENT = (
    "Advanced > AMD CBS > UMC Common Options > DDR Options > DDR BUS Configuration"
)


def source(
    setting_id: int,
    manual_id: int,
    page_number: int,
    expected_name: str,
    parent: str,
    evidence_excerpt: str,
) -> SourcePin:
    return SourcePin(
        setting_id,
        manual_id,
        page_number,
        expected_name,
        f"{parent} > {expected_name}",
        evidence_excerpt,
    )


def scoped_identity(
    parameter: str,
    scope: str,
    asrock_id: int,
    asrock_page: int,
    asus_id: int,
    asus_page: int,
) -> IdentitySeed:
    name = f"Dram ODT Impedance {parameter} {scope}"
    canonical_id = f"dram-odt-impedance-{parameter.casefold().replace('_', '-')}-{scope.casefold()}"
    asus_parent = f"{ASUS_SIGNAL_PARENT} > Processor ODT {scope} Page"
    return IdentitySeed(
        canonical_id,
        name,
        f"Sets the DRAM on-die termination impedance for {parameter} at {scope}.",
        f"ASRock and ASUS use the exact normalized {name} label in their DDR bus/signal-control hierarchy and preserve the same {parameter} and {scope} scope.",
        "The allowed impedance choices and trained default may vary by memory controller, DIMM, and firmware.",
        (
            source(
                asrock_id,
                2,
                asrock_page,
                name,
                ASROCK_PARENT,
                f"The ASRock manual defines the {parameter} DRAM ODT impedance specifically for {scope}.",
            ),
            source(
                asus_id,
                8,
                asus_page,
                name,
                asus_parent,
                f"The ASUS manual defines the {parameter} DRAM ODT impedance specifically for {scope} and lists impedance choices.",
            ),
        ),
    )


def drive_identity(
    direction: str,
    scope: str,
    asrock_id: int,
    asrock_page: int,
    asus_id: int,
    asus_page: int,
) -> IdentitySeed:
    name = f"Dram DQ drive strengths Pull {direction} {scope}"
    canonical_id = f"dram-dq-drive-strengths-pull-{direction.casefold()}-{scope.casefold()}"
    asus_parent = f"{ASUS_SIGNAL_PARENT} > Processor ODT {scope} Page"
    return IdentitySeed(
        canonical_id,
        name,
        f"Sets the DRAM DQ pull-{direction.casefold()} drive strength at {scope}.",
        f"ASRock and ASUS use the exact normalized {name} label in their DDR bus/signal-control hierarchy and preserve the same direction and {scope} scope.",
        "The allowed impedance choices and trained default may vary by memory controller, DIMM, and firmware.",
        (
            source(
                asrock_id,
                2,
                asrock_page,
                name,
                ASROCK_PARENT,
                f"The ASRock manual defines DRAM DQ pull-{direction.casefold()} drive strength specifically for {scope}.",
            ),
            source(
                asus_id,
                8,
                asus_page,
                name,
                asus_parent,
                f"The ASUS manual defines DRAM DQ pull-{direction.casefold()} drive strength specifically for {scope} and lists impedance choices.",
            ),
        ),
    )


def unscoped_identity(
    parameter: str,
    msi_id: int,
    msi_page: int,
    asus_id: int,
) -> IdentitySeed:
    name = f"Dram ODT Impedance {parameter}"
    canonical_id = f"dram-odt-impedance-{parameter.casefold().replace('_', '-')}"
    return IdentitySeed(
        canonical_id,
        name,
        f"Sets the DRAM on-die termination impedance for {parameter}.",
        f"MSI and ASUS use the exact normalized {name} label in DDR bus configuration and both describe selecting DRAM on-die termination impedance for {parameter}; no P-state suffix is present.",
        "The allowed impedance choices and trained default may vary by memory controller, DIMM, and firmware; this identity is intentionally not merged with P0, P1, or P3 controls.",
        (
            source(
                msi_id,
                6,
                msi_page,
                f"DRAM ODT Impedance {parameter}",
                MSI_PARENT,
                f"The MSI manual selects DRAM on-die termination impedance for unscoped {parameter}.",
            ),
            source(
                asus_id,
                8,
                85,
                name,
                ASUS_AMD_CBS_PARENT,
                f"The ASUS manual selects DRAM on-die termination impedance for unscoped {parameter} and lists impedance choices.",
            ),
        ),
    )


SCOPED_ROWS = (
    ("RTT_NOM_WR", "P0", 386, 57, 5869, 23),
    ("RTT_NOM_WR", "P1", 397, 58, 5881, 24),
    ("RTT_NOM_WR", "P3", 408, 58, 5905, 26),
    ("RTT_NOM_RD", "P0", 387, 57, 5870, 23),
    ("RTT_NOM_RD", "P1", 398, 58, 5882, 24),
    ("RTT_NOM_RD", "P3", 409, 59, 5906, 26),
    ("RTT_WR", "P0", 388, 57, 5871, 24),
    ("RTT_WR", "P1", 399, 58, 5883, 25),
    ("RTT_WR", "P3", 410, 59, 5907, 27),
    ("RTT_PARK", "P0", 389, 57, 5872, 24),
    ("RTT_PARK", "P1", 400, 58, 5884, 25),
    ("RTT_PARK", "P3", 411, 59, 5908, 27),
    ("DQS_RTT_PARK", "P0", 390, 57, 5873, 24),
    ("DQS_RTT_PARK", "P1", 401, 58, 5885, 25),
    ("DQS_RTT_PARK", "P3", 412, 59, 5909, 27),
)
DRIVE_ROWS = (
    ("Up", "P0", 391, 57, 5874, 24),
    ("Up", "P1", 402, 58, 5886, 25),
    ("Up", "P3", 413, 59, 5910, 27),
    ("Down", "P0", 392, 57, 5875, 24),
    ("Down", "P1", 403, 58, 5887, 25),
    ("Down", "P3", 414, 59, 5911, 27),
)
UNSCOPED_ROWS = (
    ("RTT_NOM_WR", 5415, 37, 6508),
    ("RTT_NOM_RD", 5416, 37, 6509),
    ("RTT_WR", 5417, 37, 6510),
    ("RTT_PARK", 5418, 38, 6511),
)
IDENTITIES = (
    tuple(scoped_identity(*row) for row in SCOPED_ROWS)
    + tuple(drive_identity(*row) for row in DRIVE_ROWS)
    + tuple(unscoped_identity(*row) for row in UNSCOPED_ROWS)
)

KNOWN_AMBIGUOUS_IDS = {572, 6111, 6690, 7392, 7395, 7657, 8320}
KNOWN_REJECTED_DUPLICATE_IDS = {4901}
BASELINE_COUNTS = {
    CanonicalSetting: 174,
    CanonicalSettingAlias: 210,
    SettingCanonicalMapping: 789,
    CanonicalMappingEvent: 789,
    CanonicalIdentityEvent: 189,
    CanonicalSettingClassification: 174,
    CanonicalClassificationEvent: 309,
    SettingMenuContext: 586,
    SettingMenuContextEvent: 586,
    SettingMenuContextCitation: 660,
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
    return tuple(item for seed in IDENTITIES for item in seed.sources)


def _verify_preflight(session) -> None:
    if not BACKUP_PATH.is_file() or _sha256(BACKUP_PATH) != BACKUP_SHA256:
        raise RuntimeError("The verified pre-v1.17 backup is missing or changed.")
    if inspect_backup(BACKUP_PATH).database_revision != config.EXPECTED_DATABASE_REVISION:
        raise RuntimeError("The pre-v1.17 backup has an unexpected revision.")
    if (
        session.scalar(text("SELECT version_num FROM alembic_version"))
        != config.EXPECTED_DATABASE_REVISION
    ):
        raise RuntimeError("The live schema revision changed.")
    if session.scalar(text("PRAGMA integrity_check")) != "ok":
        raise RuntimeError("SQLite integrity_check failed before Wave 12.")
    if _unmapped_count(session) != 3229:
        raise RuntimeError(f"Expected 3,229 unmapped settings, found {_unmapped_count(session)}.")
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
    if len(IDENTITIES) != 25 or len(sources) != 50 or len({x.setting_id for x in sources}) != 50:
        raise RuntimeError("Wave 12 must contain 25 identities and 50 unique records.")
    excluded = KNOWN_AMBIGUOUS_IDS | KNOWN_REJECTED_DUPLICATE_IDS
    if excluded.intersection({item.setting_id for item in sources}):
        raise RuntimeError("A known excluded record entered Wave 12.")
    if len({seed.canonical_id for seed in IDENTITIES}) != 25:
        raise RuntimeError("Wave 12 canonical IDs are not unique.")

    for seed in IDENTITIES:
        if session.get(CanonicalSetting, seed.canonical_id) is not None:
            raise RuntimeError(f"Canonical identity already exists: {seed.canonical_id}.")
        vendors = {pins[item.manual_id].vendor_id for item in seed.sources}
        if len(vendors) != 2:
            raise RuntimeError(f"{seed.canonical_id} lacks two-vendor evidence.")
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
            if session.get(SettingCanonicalMapping, item.setting_id) is not None:
                raise RuntimeError(f"Source {item.setting_id} is already mapped.")
            if session.get(SettingMenuContext, item.setting_id) is not None:
                raise RuntimeError(f"Source {item.setting_id} already has reviewed menu context.")
    for setting_id in KNOWN_AMBIGUOUS_IDS:
        if session.get(SettingCanonicalMapping, setting_id) is not None:
            raise RuntimeError(f"Known ambiguous source {setting_id} was unexpectedly mapped.")
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
    return f"v1.17 Wave 12 reviewer decision; confidence={CONFIDENCE_BASIS_POINTS / 100:.2f}%. {seed.rationale} Official source: {manual.vendor} manual {manual.id}, page {item.page_number}, {manual.source_url}; reviewed menu path: {item.menu_path}. Remaining uncertainty: {seed.uncertainty}"


def _apply(session) -> None:
    risk = "Manual DRAM electrical changes can prevent memory training, cause instability, corrupt computation, or require firmware recovery."
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
            category_id="memory",
            risk_level="high",
            risk_rationale=risk,
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
                note="v1.17 Wave 12 official-manual label, electrical parameter, P-state scope, option family, and menu hierarchy visually verified.",
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
        CanonicalSetting: 25,
        CanonicalSettingAlias: 25,
        SettingCanonicalMapping: 50,
        CanonicalMappingEvent: 50,
        CanonicalIdentityEvent: 25,
        CanonicalSettingClassification: 25,
        CanonicalClassificationEvent: 50,
        SettingMenuContext: 50,
        SettingMenuContextEvent: 50,
        SettingMenuContextCitation: 50,
    }
    failures = [
        f"{model.__tablename__} append-only delta mismatch"
        for model, delta in deltas.items()
        if _count(session, model) != baseline[model] + delta
    ]
    if _unmapped_count(session) != 3179:
        failures.append(f"expected 3,179 unmapped, found {_unmapped_count(session)}")
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
    excluded = KNOWN_AMBIGUOUS_IDS | KNOWN_REJECTED_DUPLICATE_IDS
    if any(session.get(SettingCanonicalMapping, setting_id) is not None for setting_id in excluded):
        failures.append("a known excluded record was mapped")
    if session.scalar(text("PRAGMA integrity_check")) != "ok":
        failures.append("SQLite integrity_check failed")
    foreign_keys = list(session.execute(text("PRAGMA foreign_key_check")))
    if foreign_keys:
        failures.append(f"foreign-key violations: {foreign_keys!r}")
    if failures:
        raise RuntimeError("Wave 12 verification failed:\n- " + "\n- ".join(failures))


def _build_payload(session) -> dict[str, object]:
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
            "reason": "Not approved in Wave 12; requires a later one-by-one evidence disposition.",
        }
        for setting, manual in remaining_rows
    ]
    audit = build_public_audit(session)
    if audit["status"] != "pass":
        raise RuntimeError("Public BIOS knowledge audit failed after Wave 12.")
    return {
        "milestone": "v1.17 DRAM electrical-controls canonicalization Wave 12",
        "generated_at": utc_now_iso(),
        "actor": ACTOR,
        "backup": {"path": str(BACKUP_PATH), "sha256": BACKUP_SHA256},
        "database_revision": config.EXPECTED_DATABASE_REVISION,
        "summary": {
            "new_canonical_settings": 25,
            "total_mappings_created": 50,
            "reviewed_menu_contexts": 50,
            "remaining_manual_review": len(remaining),
        },
        "official_manuals": [asdict(pin) for pin in MANUALS],
        "approved_decisions": decisions,
        "intentional_exclusions": [
            {
                "setting_ids": sorted(KNOWN_AMBIGUOUS_IDS),
                "reason": "Previously reviewed conflicts and insufficient-evidence records remain unmapped.",
            },
            {
                "setting_ids": sorted(KNOWN_REJECTED_DUPLICATE_IDS),
                "reason": "The duplicate tXPDLL extraction remains rejected and unpublished.",
            },
        ],
        "remaining_manual_review": remaining,
        "public_knowledge_audit": audit,
    }


def _markdown(payload: dict[str, object]) -> str:
    summary = payload["summary"]
    lines = [
        "# TuneForge v1.17 DRAM Electrical Controls Wave 12",
        "",
        "## Outcome",
        "",
        f"- New canonical settings: {summary['new_canonical_settings']}",
        f"- New verified mappings: {summary['total_mappings_created']}",
        f"- New reviewed menu contexts: {summary['reviewed_menu_contexts']}",
        f"- Remaining approved unmapped settings: {summary['remaining_manual_review']}",
        "",
        "Every decision uses visually reviewed official ASRock, ASUS, and MSI manual pages. Electrical parameter, P-state scope, option family, and menu hierarchy agree; no community source was required.",
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
            "P0, P1, and P3 controls remain distinct, and unscoped controls were not merged into any P-state identity. Previously documented ambiguous records and all candidates lacking equivalent official evidence remain unmapped. The complete report is stored in `data/reports/v1.17-dram-electrical-controls-wave12.json`.",
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
        "--apply", action="store_true", help="Commit the pinned v1.17 Wave 12 batch"
    )
    args = parser.parse_args()
    with SessionLocal() as session:
        _verify_preflight(session)
        print(
            "Preflight passed: backup, revision, counts, three manuals, 50 source pins, exclusions, scope separation, and trust gates match."
        )
        print("Planned outcome: 25 new identities and 50 mappings with reviewed menu contexts.")
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
        print("Applied: new_identities=25, mappings=50, menu_contexts=50.")
        print(f"Remaining approved unmapped: {payload['summary']['remaining_manual_review']}.")
        print(f"JSON report: {REPORT_JSON}")
        print(f"Summary report: {REPORT_MARKDOWN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
