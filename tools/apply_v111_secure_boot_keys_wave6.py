# ruff: noqa: E501
"""Apply TuneForge's pinned v1.11 Secure Boot key-management Wave 6.

Dry-run is the default. The batch contains 13 canonical identities and 66
approved Source Settings visually verified in official ASRock, ASUS, and MSI
manuals. Writes preserve the citation-first and append-only architecture.
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
BACKUP_PATH = ROOT / "data" / "backups" / "tuneforge-v1.11-pre-wave6.zip"
BACKUP_SHA256 = "27ed843899c7e3439286fa04cce5883955d9a212e60a3f5043ac8600eb5db8c1"
REPORT_JSON = ROOT / "data" / "reports" / "v1.11-secure-boot-keys-wave6.json"
REPORT_MARKDOWN = ROOT / "docs" / "v1.11-secure-boot-keys-wave6.md"
ACTOR = "v1.11-secure-boot-key-official-evidence-reviewer"
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
        8,
        "asus",
        "2e7a4b2d504e9f9286b584fcfbe9cba85e0ccd4a061ad545025160408b308213",
        "https://www.asus.com/us/supportonly/tuf%20gaming%20x870-plus%20wifi/helpdesk_manual/",
        116,
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
    if manual_id == 2:
        parent = "Security > Secure Boot > Key Management"
    elif manual_id in {3, 8, 11, 12}:
        parent = "Boot > Secure Boot > Key Management"
    elif manual_id in {5, 6}:
        parent = "Settings > Security > Secure Boot > Key Management"
    else:
        raise ValueError(f"No reviewed Secure Boot hierarchy for manual {manual_id}.")
    if name.startswith("PK "):
        parent += " > PK Management"
    elif name.startswith("KEK "):
        parent += " > KEK Management"
    elif name.startswith("DBX "):
        parent += " > DBX Management"
    elif name.startswith("DB "):
        parent += " > DB Management"
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
    rows: tuple[tuple[int, int, int], ...],
) -> IdentitySeed:
    return IdentitySeed(
        canonical_id, name, description, rationale, uncertainty, _sources(name, rows)
    )


IDENTITIES = (
    identity(
        "db-append-key",
        "DB Append Key",
        "Adds an authorized signature entry to the Secure Boot allowed-signature database (db).",
        "ASUS and MSI use the exact operation name and define the same additive update to the allowed-signature database without replacing existing entries.",
        "Accepted file formats and authentication requirements vary by firmware.",
        (
            (1262, 3, 82),
            (4667, 5, 31),
            (5220, 6, 22),
            (6704, 8, 106),
            (7671, 11, 77),
            (8334, 12, 82),
        ),
    ),
    identity(
        "db-delete-key",
        "DB Delete Key",
        "Deletes the Secure Boot allowed-signature database (db) key data.",
        "ASUS and MSI use the exact operation name under DB Management and describe deletion of the same Secure Boot allowed-signature store.",
        "Deletion consequences depend on the remaining trust configuration and firmware policy.",
        ((4668, 5, 31), (5221, 6, 22), (6705, 8, 106), (7672, 11, 77), (8335, 12, 82)),
    ),
    identity(
        "db-set-new-key",
        "DB Set New Key",
        "Loads new key data into the Secure Boot allowed-signature database (db).",
        "ASUS and MSI use the exact operation name under DB Management and describe loading or replacing the same allowed-signature database from storage.",
        "Whether firmware replaces the database or imports a selected authenticated variable depends on its implementation.",
        ((4666, 5, 31), (5219, 6, 22), (6703, 8, 106), (7670, 11, 77), (8333, 12, 82)),
    ),
    identity(
        "dbx-append-key",
        "DBX Append Key",
        "Adds a revoked signature entry to the Secure Boot forbidden-signature database (dbx).",
        "ASUS and MSI use the exact operation name and define the same additive update to the forbidden or revoked-signature database.",
        "Accepted file formats and authentication requirements vary by firmware.",
        (
            (1264, 3, 82),
            (4671, 5, 31),
            (5224, 6, 22),
            (6709, 8, 107),
            (7676, 11, 78),
            (8339, 12, 83),
        ),
    ),
    identity(
        "dbx-delete-key",
        "DBX Delete Key",
        "Deletes the Secure Boot forbidden-signature database (dbx) key data.",
        "ASUS and MSI use the exact operation name under DBX Management and describe deletion of the same revoked-signature store.",
        "Deleting revocations can weaken blocking of revoked images; behavior depends on firmware policy.",
        ((4672, 5, 31), (5225, 6, 22), (6710, 8, 107), (7677, 11, 78), (8340, 12, 83)),
    ),
    identity(
        "dbx-set-new-key",
        "DBX Set New Key",
        "Loads new key data into the Secure Boot forbidden-signature database (dbx).",
        "ASUS and MSI use the exact operation name under DBX Management and describe loading or replacing the same revoked-signature database from storage.",
        "Replacement behavior and accepted authenticated-variable formats vary by firmware.",
        ((4670, 5, 31), (5223, 6, 22), (6708, 8, 107), (7675, 11, 78), (8338, 12, 83)),
    ),
    identity(
        "install-default-secure-boot-keys",
        "Install Default Secure Boot Keys",
        "Installs the firmware's default Secure Boot Platform Key, Key Exchange Keys, and signature databases.",
        "ASRock and ASUS use the exact operation name and explicitly describe loading the default PK, KEK, db, and dbx key material.",
        "The actual default key set is firmware and platform specific.",
        ((571, 2, 80), (6689, 8, 105), (7656, 11, 76), (8319, 12, 81)),
    ),
    identity(
        "kek-append-key",
        "KEK Append Key",
        "Adds a Key Exchange Key to the Secure Boot KEK store without replacing existing entries.",
        "ASUS and MSI use the exact operation name under KEK Management and define the same additive Key Exchange Key update.",
        "Accepted file formats and authentication requirements vary by firmware.",
        (
            (1260, 3, 81),
            (4663, 5, 31),
            (5216, 6, 22),
            (6699, 8, 106),
            (7666, 11, 77),
            (8329, 12, 82),
        ),
    ),
    identity(
        "kek-delete-key",
        "KEK Delete Key",
        "Deletes Secure Boot Key Exchange Key (KEK) data.",
        "ASUS and MSI use the exact operation name under KEK Management and describe deletion of the same key store.",
        "Deletion can prevent future authenticated updates unless a usable trust path remains.",
        ((4664, 5, 31), (5217, 6, 22), (6700, 8, 106), (7667, 11, 77), (8330, 12, 82)),
    ),
    identity(
        "kek-set-new-key",
        "KEK Set New Key",
        "Loads new key data into the Secure Boot Key Exchange Key (KEK) store.",
        "ASUS and MSI use the exact operation name under KEK Management and describe loading or replacing the same key store from storage.",
        "Replacement behavior and accepted authenticated-variable formats vary by firmware.",
        ((4662, 5, 31), (5215, 6, 22), (6698, 8, 106), (7665, 11, 77), (8328, 12, 82)),
    ),
    identity(
        "pk-delete-key",
        "PK Delete Key",
        "Deletes the Secure Boot Platform Key (PK).",
        "ASUS and MSI use the exact operation name under PK Management and describe deletion of the Platform Key; the manuals also associate this action with changing Secure Boot key state.",
        "The resulting Setup Mode or Secure Boot state is firmware dependent.",
        ((4660, 5, 31), (5213, 6, 22), (6695, 8, 106), (7662, 11, 77), (8325, 12, 82)),
    ),
    identity(
        "pk-set-new-key",
        "PK Set New Key",
        "Loads a new Secure Boot Platform Key (PK).",
        "ASUS and MSI use the exact operation name under PK Management and describe loading the same Platform Key variable from storage.",
        "Accepted file formats and the transition out of Setup Mode depend on firmware implementation.",
        ((4659, 5, 31), (5212, 6, 22), (6694, 8, 105), (7661, 11, 77), (8324, 12, 82)),
    ),
    identity(
        "save-all-secure-boot-variables",
        "Save All Secure Boot Variables",
        "Exports all current Secure Boot variables or keys to storage.",
        "ASUS and MSI use the exact operation name and define the same export of all Secure Boot variables for preservation or transfer.",
        "Destination media, directory layout, and file format vary by firmware.",
        ((4656, 5, 30), (6691, 8, 105), (7658, 11, 76), (8321, 12, 81)),
    ),
)

EXCLUDED_CONFLICT_IDS = {572, 6690, 7657, 8320}

BASELINE_COUNTS = {
    CanonicalSetting: 127,
    CanonicalSettingAlias: 163,
    SettingCanonicalMapping: 601,
    CanonicalMappingEvent: 601,
    CanonicalIdentityEvent: 142,
    CanonicalSettingClassification: 127,
    CanonicalClassificationEvent: 215,
    SettingMenuContext: 398,
    SettingMenuContextEvent: 398,
    SettingMenuContextCitation: 472,
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
        raise RuntimeError("The verified pre-v1.11 backup is missing or changed.")
    if inspect_backup(BACKUP_PATH).database_revision != config.EXPECTED_DATABASE_REVISION:
        raise RuntimeError("The pre-v1.11 backup has an unexpected revision.")
    if (
        session.scalar(text("SELECT version_num FROM alembic_version"))
        != config.EXPECTED_DATABASE_REVISION
    ):
        raise RuntimeError("The live schema revision changed.")
    if session.scalar(text("PRAGMA integrity_check")) != "ok":
        raise RuntimeError("SQLite integrity_check failed before Wave 6.")
    if _unmapped_count(session) != 3417:
        raise RuntimeError(f"Expected 3,417 unmapped settings, found {_unmapped_count(session)}.")
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
        len(IDENTITIES) != 13
        or len(sources) != 66
        or len({source.setting_id for source in sources}) != 66
    ):
        raise RuntimeError("Wave 6 must contain 13 identities and 66 unique records.")
    if EXCLUDED_CONFLICT_IDS.intersection({source.setting_id for source in sources}):
        raise RuntimeError("A known Clear Secure Boot Keys conflict entered Wave 6.")
    for seed in IDENTITIES:
        if session.get(CanonicalSetting, seed.canonical_id) is not None:
            raise RuntimeError(f"Canonical identity already exists: {seed.canonical_id}.")
        vendors = {pins[source.manual_id].vendor_id for source in seed.sources}
        expected_vendors = (
            {"asus", "asrock"}
            if seed.canonical_id == "install-default-secure-boot-keys"
            else {"asus", "msi"}
        )
        if vendors != expected_vendors:
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
    for setting_id in EXCLUDED_CONFLICT_IDS:
        if session.get(SettingCanonicalMapping, setting_id) is not None:
            raise RuntimeError(f"Known ambiguous source {setting_id} was unexpectedly mapped.")


def _mapping_note(session, seed: IdentitySeed, source: SourcePin) -> str:
    manual = session.get(Manual, source.manual_id)
    return f"v1.11 Wave 6 reviewer decision; confidence={CONFIDENCE_BASIS_POINTS / 100:.2f}%. {seed.rationale} Official source: {manual.vendor} manual {manual.id}, page {source.page_number}, {manual.source_url}; reviewed menu path: {source.menu_path}. Remaining uncertainty: {seed.uncertainty}"


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
            category_id="security",
            risk_level="high",
            risk_rationale="Changing Secure Boot trust databases or keys can disable signature validation, prevent trusted software from booting, or require firmware key recovery.",
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
                evidence_excerpt=f"The official manual page defines {effective_name(setting)} as the Secure Boot key-management operation represented by this reviewed hierarchy.",
                note="v1.11 Wave 6 official-manual operation, target key store, and menu hierarchy visually verified.",
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
        CanonicalSetting: 13,
        CanonicalSettingAlias: 13,
        SettingCanonicalMapping: 66,
        CanonicalMappingEvent: 66,
        CanonicalIdentityEvent: 13,
        CanonicalSettingClassification: 13,
        CanonicalClassificationEvent: 26,
        SettingMenuContext: 66,
        SettingMenuContextEvent: 66,
        SettingMenuContextCitation: 66,
    }
    failures = [
        f"{model.__tablename__} append-only delta mismatch"
        for model, delta in deltas.items()
        if _count(session, model) != baseline[model] + delta
    ]
    if _unmapped_count(session) != 3351:
        failures.append(f"expected 3,351 unmapped, found {_unmapped_count(session)}")
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
        for setting_id in EXCLUDED_CONFLICT_IDS
    ):
        failures.append("known Clear Secure Boot Keys conflict was mapped")
    if session.scalar(text("PRAGMA integrity_check")) != "ok":
        failures.append("SQLite integrity_check failed")
    foreign_keys = list(session.execute(text("PRAGMA foreign_key_check")))
    if foreign_keys:
        failures.append(f"foreign-key violations: {foreign_keys!r}")
    if failures:
        raise RuntimeError("Wave 6 verification failed:\n- " + "\n- ".join(failures))


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
            "reason": "Not approved in Wave 6; requires a later one-by-one evidence disposition.",
        }
        for setting, manual in remaining_rows
    ]
    audit = build_public_audit(session)
    if audit["status"] != "pass":
        raise RuntimeError("Public BIOS knowledge audit failed after Wave 6.")
    return {
        "milestone": "v1.11 Secure Boot key-management canonicalization Wave 6",
        "generated_at": utc_now_iso(),
        "actor": ACTOR,
        "backup": {"path": str(BACKUP_PATH), "sha256": BACKUP_SHA256},
        "database_revision": config.EXPECTED_DATABASE_REVISION,
        "summary": {
            "new_canonical_settings": 13,
            "total_mappings_created": 66,
            "reviewed_menu_contexts": 66,
            "remaining_manual_review": len(remaining),
        },
        "official_manuals": [asdict(pin) for pin in MANUALS],
        "approved_decisions": decisions,
        "intentional_exclusions": [
            {
                "setting_ids": sorted(EXCLUDED_CONFLICT_IDS),
                "source_name": "Clear Secure Boot Keys",
                "reason": "Previously reviewed official hierarchy conflict remains unresolved; no mapping was created.",
            }
        ],
        "remaining_manual_review": remaining,
        "public_knowledge_audit": audit,
    }


def _markdown(payload: dict[str, object]) -> str:
    summary = payload["summary"]
    lines = [
        "# TuneForge v1.11 Secure Boot Key Management Wave 6",
        "",
        "## Outcome",
        "",
        f"- New canonical settings: {summary['new_canonical_settings']}",
        f"- New verified mappings: {summary['total_mappings_created']}",
        f"- New reviewed menu contexts: {summary['reviewed_menu_contexts']}",
        f"- Remaining approved unmapped settings: {summary['remaining_manual_review']}",
        "",
        "Every decision uses visually reviewed official ASRock, ASUS, and MSI pages. Exact normalized operation names, target key stores, behavior, and menu hierarchy agree; no community source was required.",
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
            "`Clear Secure Boot Keys` remains unmapped because its official source hierarchy is conflicting. The machine-readable report contains every approval and the complete pending backlog: `data/reports/v1.11-secure-boot-keys-wave6.json`.",
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
    parser.add_argument("--apply", action="store_true", help="Commit the pinned v1.11 Wave 6 batch")
    args = parser.parse_args()
    with SessionLocal() as session:
        _verify_preflight(session)
        print(
            "Preflight passed: backup, revision, counts, seven manuals, 66 source pins, exclusions, and trust gates match."
        )
        print("Planned outcome: 13 new identities and 66 mappings with reviewed menu contexts.")
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
        print("Applied: new_identities=13, mappings=66, menu_contexts=66.")
        print(f"Remaining approved unmapped: {payload['summary']['remaining_manual_review']}.")
        print(f"JSON report: {REPORT_JSON}")
        print(f"Summary report: {REPORT_MARKDOWN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
