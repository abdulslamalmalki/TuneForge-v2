# ruff: noqa: E501
"""Apply TuneForge's pinned v1.15 TPM and boot-trust Wave 10.

Dry-run is the default. The batch contains four canonical identities and nine
approved Source Settings visually verified in official ASRock, ASUS, and MSI
manuals. Writes preserve citation-first, append-only, and reversible behavior.
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
BACKUP_PATH = ROOT / "data" / "backups" / "tuneforge-v1.15-pre-wave10.zip"
BACKUP_SHA256 = "21d7b1acb501da039101a61c55abd5072587b4741bd3ade2352acde391093b94"
REPORT_JSON = ROOT / "data" / "reports" / "v1.15-tpm-boot-trust-wave10.json"
REPORT_MARKDOWN = ROOT / "docs" / "v1.15-tpm-boot-trust-wave10.md"
ACTOR = "v1.15-tpm-boot-trust-official-evidence-reviewer"
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
        12,
        "asus",
        "49d55cb253e29b523cb69a78934d1df0f8160dd7feb9c68a76ceecb42927da37",
        "https://www.asus.com/motherboards-components/motherboards/tuf-gaming/tuf-gaming-b760m-plus/helpdesk_manual?model2Name=TUF-GAMING-B760M-PLUS",
        94,
    ),
)


def source(
    setting_id: int,
    manual_id: int,
    page_number: int,
    expected_name: str,
    menu_path: str,
    evidence_excerpt: str,
) -> SourcePin:
    return SourcePin(setting_id, manual_id, page_number, expected_name, menu_path, evidence_excerpt)


IDENTITIES = (
    IdentitySeed(
        "amd-ftpm-switch",
        "AMD fTPM Switch",
        "Selects the AMD CPU firmware TPM or a discrete TPM path, with disable behavior where the firmware provides it.",
        "ASRock and MSI use the same normalized setting name and both official descriptions select AMD CPU firmware TPM versus discrete TPM; ASRock additionally documents Disabled and Route to SPI TPM options.",
        "The exact option labels and whether a fully disabled state is available vary by firmware.",
        "security",
        "high",
        "Changing the active TPM path can make TPM-bound keys, credentials, or operating-system protection unavailable until the expected TPM is restored.",
        (
            source(
                303,
                2,
                43,
                "AMD fTPM Switch",
                "Advanced > CPU Configuration > AMD fTPM Switch",
                "The ASRock manual defines AMD CPU fTPM, Route to SPI TPM, and Disabled choices.",
            ),
            source(
                5187,
                6,
                20,
                "AMD fTPM switch",
                "Settings > Security > Trusted Computing > AMD fTPM switch",
                "The MSI manual selects AMD Firmware TPM or Discrete TPM when Security Device Support is enabled.",
            ),
        ),
    ),
    IdentitySeed(
        "tpm-device-selection",
        "TPM Device Selection",
        "Selects between the platform firmware TPM and a discrete TPM device.",
        "Two ASUS manuals and one MSI manual use the exact normalized name and consistently describe mutually exclusive firmware-TPM and discrete/hardware-TPM selection.",
        "Option labels differ between vendors (for example, dTPM/fTPM 2.0 versus Enable Discrete/Firmware TPM).",
        "security",
        "high",
        "Selecting a different TPM can make TPM-bound keys, credentials, or operating-system protection unavailable until the expected TPM is restored.",
        (
            source(
                1110,
                3,
                61,
                "TPM Device Selection",
                "Advanced > PCH-FW Configuration > TPM Device Selection",
                "The ASUS manual selects Firmware TPM or Discrete TPM and documents that enabling one disables the other.",
            ),
            source(
                4636,
                5,
                29,
                "TPM Device Selection",
                "Settings > Security > Trusted Computing > TPM Device Selection",
                "The MSI manual selects dTPM hardware or fTPM 2.0.",
            ),
            source(
                8203,
                12,
                61,
                "TPM Device Selection",
                "Advanced > PCH-FW Configuration > TPM Device Selection",
                "The ASUS manual selects Firmware TPM or Discrete TPM and documents that enabling one disables the other.",
            ),
        ),
    ),
    IdentitySeed(
        "sha-1-pcr-bank",
        "SHA-1 PCR Bank",
        "Enables or disables the TPM SHA-1 Platform Configuration Register bank.",
        "ASUS and MSI use the exact normalized name in Trusted Computing and both official descriptions define the same enable/disable control for the SHA-1 PCR bank.",
        "The manuals do not establish operating-system use or attestation-policy consequences for every platform.",
        "security",
        "high",
        "Changing available PCR banks can affect measured-boot and attestation policies that depend on a particular TPM bank.",
        (
            source(
                5190,
                6,
                20,
                "SHA-1 PCR Bank",
                "Settings > Security > Trusted Computing > SHA-1 PCR Bank",
                "The MSI manual documents enabling or disabling the SHA-1 160-bit PCR format.",
            ),
            source(
                6099,
                8,
                48,
                "SHA-1 PCR Bank",
                "Advanced > Trusted Computing > SHA-1 PCR Bank",
                "The ASUS manual documents enabling or disabling the SHA-1 PCR Bank.",
            ),
        ),
    ),
    IdentitySeed(
        "enroll-efi-image",
        "Enroll Efi Image",
        "Enrolls the SHA-256 hash of an EFI or PE image into the Secure Boot authorized-signature database (db).",
        "ASRock and MSI use the exact normalized name under Secure Boot Key Management and both official descriptions enroll an image SHA-256 value into the authorized signature database so the image can run in Secure Boot mode.",
        "The manuals differ in whether they call the selected object a binary, EFI image, or PE image.",
        "security",
        "high",
        "Enrollment changes the Secure Boot trust database and can authorize execution of the selected image.",
        (
            source(
                578,
                2,
                80,
                "Enroll Efi Image",
                "Security > Secure Boot > Key Management > Enroll Efi Image",
                "The ASRock manual enrolls the binary SHA256 hash into the Authorized Signature Database (db).",
            ),
            source(
                4657,
                5,
                31,
                "Enroll Efi Image",
                "Settings > Security > Secure Boot > Key Management > Enroll Efi Image",
                "The MSI manual enrolls the SHA256 hash certificate of a PE image into the authorized signature database (db).",
            ),
        ),
    ),
)

KNOWN_AMBIGUOUS_IDS = {572, 6111, 6690, 7392, 7395, 7657, 8320}
KNOWN_REJECTED_DUPLICATE_IDS = {4901}

BASELINE_COUNTS = {
    CanonicalSetting: 162,
    CanonicalSettingAlias: 198,
    SettingCanonicalMapping: 747,
    CanonicalMappingEvent: 747,
    CanonicalIdentityEvent: 177,
    CanonicalSettingClassification: 162,
    CanonicalClassificationEvent: 285,
    SettingMenuContext: 544,
    SettingMenuContextEvent: 544,
    SettingMenuContextCitation: 618,
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
        raise RuntimeError("The verified pre-v1.15 backup is missing or changed.")
    if inspect_backup(BACKUP_PATH).database_revision != config.EXPECTED_DATABASE_REVISION:
        raise RuntimeError("The pre-v1.15 backup has an unexpected revision.")
    revision = session.scalar(text("SELECT version_num FROM alembic_version"))
    if revision != config.EXPECTED_DATABASE_REVISION:
        raise RuntimeError(f"Live schema revision changed: {revision!r}.")
    if session.scalar(text("PRAGMA integrity_check")) != "ok":
        raise RuntimeError("SQLite integrity_check failed before Wave 10.")
    if _unmapped_count(session) != 3271:
        raise RuntimeError(f"Expected 3,271 unmapped settings, found {_unmapped_count(session)}.")
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
        expected = (pin.vendor_id, pin.sha256, pin.source_url, pin.page_count)
        if actual != expected:
            raise RuntimeError(f"Pinned manual {pin.manual_id} changed: {actual!r}.")

    sources = _all_sources()
    if len(IDENTITIES) != 4 or len(sources) != 9 or len({x.setting_id for x in sources}) != 9:
        raise RuntimeError("Wave 10 must contain four identities and nine unique records.")
    excluded = KNOWN_AMBIGUOUS_IDS | KNOWN_REJECTED_DUPLICATE_IDS
    if excluded.intersection({item.setting_id for item in sources}):
        raise RuntimeError("A known excluded record entered Wave 10.")

    for seed in IDENTITIES:
        if session.get(CanonicalSetting, seed.canonical_id) is not None:
            raise RuntimeError(f"Canonical identity already exists: {seed.canonical_id}.")
        vendors = {pins[item.manual_id].vendor_id for item in seed.sources}
        if len(vendors) < 2:
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
                raise RuntimeError(f"Pinned source {item.setting_id} changed: {actual!r}.")
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
        if setting is None or setting.status != "rejected":
            raise RuntimeError(f"Known rejected duplicate {setting_id} changed review state.")
        if session.get(SettingCanonicalMapping, setting_id) is not None:
            raise RuntimeError(f"Known rejected duplicate {setting_id} was unexpectedly mapped.")


def _mapping_note(session, seed: IdentitySeed, item: SourcePin) -> str:
    manual = session.get(Manual, item.manual_id)
    return f"v1.15 Wave 10 reviewer decision; confidence={CONFIDENCE_BASIS_POINTS / 100:.2f}%. {seed.rationale} Official source: {manual.vendor} manual {manual.id}, page {item.page_number}, {manual.source_url}; reviewed menu path: {item.menu_path}. Remaining uncertainty: {seed.uncertainty}"


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
                note="v1.15 Wave 10 official-manual name, behavior, options, and menu hierarchy visually verified.",
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
        CanonicalSetting: 4,
        CanonicalSettingAlias: 4,
        SettingCanonicalMapping: 9,
        CanonicalMappingEvent: 9,
        CanonicalIdentityEvent: 4,
        CanonicalSettingClassification: 4,
        CanonicalClassificationEvent: 8,
        SettingMenuContext: 9,
        SettingMenuContextEvent: 9,
        SettingMenuContextCitation: 9,
    }
    failures = [
        f"{model.__tablename__} append-only delta mismatch"
        for model, delta in deltas.items()
        if _count(session, model) != baseline[model] + delta
    ]
    if _unmapped_count(session) != 3262:
        failures.append(f"expected 3,262 unmapped, found {_unmapped_count(session)}")
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
        raise RuntimeError("Wave 10 verification failed:\n- " + "\n- ".join(failures))


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
            "reason": "Not approved in Wave 10; requires a later one-by-one evidence disposition.",
        }
        for setting, manual in remaining_rows
    ]
    audit = build_public_audit(session)
    if audit["status"] != "pass":
        raise RuntimeError("Public BIOS knowledge audit failed after Wave 10.")
    return {
        "milestone": "v1.15 TPM and boot-trust canonicalization Wave 10",
        "generated_at": utc_now_iso(),
        "actor": ACTOR,
        "backup": {"path": str(BACKUP_PATH), "sha256": BACKUP_SHA256},
        "database_revision": config.EXPECTED_DATABASE_REVISION,
        "summary": {
            "new_canonical_settings": 4,
            "total_mappings_created": 9,
            "reviewed_menu_contexts": 9,
            "remaining_manual_review": len(remaining),
        },
        "official_manuals": [asdict(pin) for pin in MANUALS],
        "approved_decisions": decisions,
        "intentional_exclusions": [
            {
                "setting_ids": sorted(KNOWN_AMBIGUOUS_IDS),
                "reason": "Previously reviewed official conflicts and insufficient-evidence records remain unmapped.",
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
        "# TuneForge v1.15 TPM and Boot-Trust Wave 10",
        "",
        "## Outcome",
        "",
        f"- New canonical settings: {summary['new_canonical_settings']}",
        f"- New verified mappings: {summary['total_mappings_created']}",
        f"- New reviewed menu contexts: {summary['reviewed_menu_contexts']}",
        f"- Remaining approved unmapped settings: {summary['remaining_manual_review']}",
        "",
        "Every decision uses visually reviewed official ASRock, ASUS, and MSI manual pages. Names, documented behavior, options, and security-menu context agree; no community source was required.",
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
            "Previously documented ambiguous records and all candidates lacking equivalent official evidence remain unmapped. The complete machine-readable decision report and pending backlog are stored in `data/reports/v1.15-tpm-boot-trust-wave10.json`.",
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
        "--apply", action="store_true", help="Commit the pinned v1.15 Wave 10 batch"
    )
    args = parser.parse_args()
    with SessionLocal() as session:
        _verify_preflight(session)
        print(
            "Preflight passed: backup, revision, counts, six manuals, nine source pins, exclusions, and trust gates match."
        )
        print("Planned outcome: four new identities and nine mappings with reviewed menu contexts.")
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
        print("Applied: new_identities=4, mappings=9, menu_contexts=9.")
        print(f"Remaining approved unmapped: {payload['summary']['remaining_manual_review']}.")
        print(f"JSON report: {REPORT_JSON}")
        print(f"Summary report: {REPORT_MARKDOWN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
