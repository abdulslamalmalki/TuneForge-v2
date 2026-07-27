"""Audit trusted BIOS-setting knowledge and complete public Library visibility.

This command is read-only. It verifies the live database, official-manual trust
gates, canonical mappings, cross-vendor equivalence projections, and exact
coverage of every approved source and active canonical identity in the public
Library read model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from sqlalchemy import select, text

from app import config
from app.db.models import (
    CanonicalMappingEvent,
    CanonicalSetting,
    CanonicalSettingClassification,
    Manual,
    Setting,
    SettingCanonicalMapping,
)
from app.db.session import SessionLocal
from app.domain import canonical_settings, canonicalization, library
from app.domain.extraction_runs import utc_now_iso
from app.domain.review import effective_description, effective_name
from app.normalization.canonical_settings import normalize_alias

# Repeated labels that were visually reviewed against the cited official PDF.
# These are distinct menu occurrences or controls, not duplicate extractions.
# Keeping this manifest explicit makes the disposition auditable and prevents
# a generic duplicate heuristic from silently suppressing future collisions.
_REVIEWED_REPEATED_LABELS = {
    frozenset({4650, 4654}): (
        "MSI documents the action once in Secure Boot and again inside Key Management."
    ),
    frozenset({4651, 4655}): (
        "MSI documents the action once in Secure Boot and again inside Key Management."
    ),
    frozenset({5366, 5376}): "MSI documents EXPO-profile and main-timing tRAS controls.",
    frozenset({5367, 5377}): "MSI documents EXPO-profile and main-timing tRC controls.",
    frozenset({5368, 5378}): "MSI documents EXPO-profile and main-timing tWR controls.",
    frozenset({5369, 5379}): "MSI documents EXPO-profile and main-timing tRFC1 controls.",
    frozenset({5370, 5380}): "MSI documents EXPO-profile and main-timing tRFC2 controls.",
    frozenset({5371, 5381}): "MSI documents EXPO-profile and main-timing tRFCSB controls.",
    frozenset({5790, 5792}): "ASUS documents one CCX0 Ratio under each CCD section.",
    frozenset({5915, 5917}): (
        "ASUS documents two controls with different supported impedance values."
    ),
    frozenset({6052, 6053}): (
        "ASUS uses the same label for an enable control and its numeric voltage value."
    ),
    frozenset({7393, 7396}): (
        "ASUS documents one Ratio Offset child under each Temperature A/B branch."
    ),
    frozenset({8104, 8114}): (
        "ASUS documents DRAM VDDQ in both the parent voltage list and PMIC Voltages."
    ),
}

_KNOWN_OFFICIAL_DOCUMENT_CONFLICTS = {
    frozenset({7392, 7395}): (
        "The ASUS manual repeats the heading 'Negative Ratio Offset A' in the "
        "Temperature B branch, while that record's body and dependency text say "
        "'Temperature B' and 'Negative Ratio Offset B'. The source label is preserved "
        "verbatim and both records remain unmapped."
    )
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _record(
    code: str,
    message: str,
    *,
    setting_id: int | None = None,
    manual_id: int | None = None,
    canonical_id: str | None = None,
) -> dict[str, object]:
    return {
        "code": code,
        "message": message,
        "setting_id": setting_id,
        "manual_id": manual_id,
        "canonical_id": canonical_id,
    }


def build_report(session) -> dict[str, object]:
    failures: list[dict[str, object]] = []
    warnings: list[dict[str, object]] = []
    reviewed_repeated_labels: list[dict[str, object]] = []
    revision = session.scalar(text("SELECT version_num FROM alembic_version"))
    integrity = session.scalar(text("PRAGMA integrity_check"))
    foreign_key_violations = [
        tuple(row) for row in session.execute(text("PRAGMA foreign_key_check"))
    ]
    if revision != config.EXPECTED_DATABASE_REVISION:
        failures.append(
            _record(
                "database_revision",
                f"Expected {config.EXPECTED_DATABASE_REVISION}, found {revision}.",
            )
        )
    if integrity != "ok":
        failures.append(_record("database_integrity", f"SQLite reported {integrity}."))
    if foreign_key_violations:
        failures.append(
            _record(
                "foreign_key_violations",
                f"SQLite reported {len(foreign_key_violations)} foreign-key violations.",
            )
        )

    approved_rows = list(
        session.execute(
            select(Setting, Manual)
            .join(Manual, Manual.id == Setting.manual_id)
            .where(Setting.status == "approved")
            .order_by(Setting.id)
        )
    )
    approved_ids = {setting.id for setting, _ in approved_rows}
    approved_manuals = {manual.id: manual for _, manual in approved_rows}
    manual_results = []
    for manual in sorted(approved_manuals.values(), key=lambda value: value.id):
        source = config.UPLOADS_DIR / manual.stored_filename
        issues = []
        if not canonicalization.official_manual(manual):
            issues.append("official provenance gate failed")
        if not source.is_file():
            issues.append("stored PDF is missing")
        else:
            if source.stat().st_size != manual.file_size_bytes:
                issues.append("stored PDF size differs from provenance metadata")
            if _sha256(source) != manual.sha256:
                issues.append("stored PDF SHA-256 differs from provenance metadata")
        if manual.page_count < 1:
            issues.append("manual page count is invalid")
        if issues:
            failures.append(
                _record(
                    "manual_trust",
                    "; ".join(issues),
                    manual_id=manual.id,
                )
            )
        manual_results.append(
            {
                "manual_id": manual.id,
                "vendor_id": manual.vendor_id,
                "title": manual.document_title,
                "source_url": manual.source_url,
                "stored_filename": manual.stored_filename,
                "sha256": manual.sha256,
                "approved_setting_count": sum(
                    1 for setting, _ in approved_rows if setting.manual_id == manual.id
                ),
                "trust_status": "pass" if not issues else "fail",
                "issues": issues,
            }
        )

    duplicate_groups: dict[tuple[int, str, int], list[int]] = defaultdict(list)
    for setting, manual in approved_rows:
        trust_reasons = canonicalization.source_trust_reasons(setting, manual)
        if trust_reasons:
            failures.append(
                _record(
                    "source_trust",
                    " ".join(trust_reasons),
                    setting_id=setting.id,
                    manual_id=manual.id,
                )
            )
        page_number = setting.edited_page_number or setting.page_number
        if not 1 <= page_number <= manual.page_count:
            failures.append(
                _record(
                    "source_page",
                    f"Page {page_number} is outside manual range 1-{manual.page_count}.",
                    setting_id=setting.id,
                    manual_id=manual.id,
                )
            )
        if not effective_name(setting).strip():
            failures.append(
                _record(
                    "source_name",
                    "Approved source setting has no effective name.",
                    setting_id=setting.id,
                    manual_id=manual.id,
                )
            )
        if not effective_description(setting).strip():
            failures.append(
                _record(
                    "source_description",
                    "Approved source setting has no effective description.",
                    setting_id=setting.id,
                    manual_id=manual.id,
                )
            )
        duplicate_groups[(manual.id, normalize_alias(effective_name(setting)), page_number)].append(
            setting.id
        )
    for (manual_id, normalized_name, page_number), setting_ids in duplicate_groups.items():
        if len(setting_ids) > 1:
            key = frozenset(setting_ids)
            reviewed_reason = _REVIEWED_REPEATED_LABELS.get(key)
            if reviewed_reason is not None:
                reviewed_repeated_labels.append(
                    {
                        "manual_id": manual_id,
                        "page_number": page_number,
                        "normalized_name": normalized_name,
                        "setting_ids": sorted(setting_ids),
                        "disposition": "distinct_official_occurrences",
                        "reason": reviewed_reason,
                    }
                )
                continue
            conflict_reason = _KNOWN_OFFICIAL_DOCUMENT_CONFLICTS.get(key)
            if conflict_reason is not None:
                warnings.append(
                    {
                        "code": "known_official_document_conflict",
                        "message": conflict_reason,
                        "setting_ids": sorted(setting_ids),
                        "manual_id": manual_id,
                        "page_number": page_number,
                        "canonical_id": None,
                    }
                )
                continue
            warnings.append(
                _record(
                    "possible_source_duplicate",
                    (
                        f"Manual {manual_id} page {page_number} contains {len(setting_ids)} "
                        f"approved records normalized as {normalized_name!r}: {setting_ids}."
                    ),
                    manual_id=manual_id,
                )
            )

    mappings = list(
        session.execute(
            select(SettingCanonicalMapping, Setting, Manual, CanonicalSetting)
            .join(Setting, Setting.id == SettingCanonicalMapping.setting_id)
            .join(Manual, Manual.id == Setting.manual_id)
            .join(
                CanonicalSetting,
                CanonicalSetting.canonical_id == SettingCanonicalMapping.canonical_id,
            )
            .order_by(SettingCanonicalMapping.canonical_id, Setting.id)
        )
    )
    mapped_ids = {setting.id for _, setting, _, _ in mappings}
    name_targets: dict[str, set[str]] = defaultdict(set)
    for mapping, setting, manual, identity in mappings:
        if setting.status != "approved":
            failures.append(
                _record(
                    "mapped_unapproved_source",
                    "Canonical mapping points to a non-approved source setting.",
                    setting_id=setting.id,
                    canonical_id=mapping.canonical_id,
                )
            )
        if identity.status != canonical_settings.IDENTITY_ACTIVE:
            failures.append(
                _record(
                    "mapped_inactive_identity",
                    "Canonical mapping points to an inactive identity.",
                    setting_id=setting.id,
                    canonical_id=mapping.canonical_id,
                )
            )
        trust_reasons = canonicalization.source_trust_reasons(setting, manual)
        if trust_reasons:
            failures.append(
                _record(
                    "mapping_source_trust",
                    " ".join(trust_reasons),
                    setting_id=setting.id,
                    manual_id=manual.id,
                    canonical_id=mapping.canonical_id,
                )
            )
        events = tuple(
            session.scalars(
                select(CanonicalMappingEvent)
                .where(CanonicalMappingEvent.setting_id == setting.id)
                .order_by(CanonicalMappingEvent.id)
            )
        )
        event_actions = tuple(event.action for event in events)
        if not events or events[-1].action not in {"mapped", "remapped"}:
            failures.append(
                _record(
                    "mapping_audit_history",
                    f"Current mapping has invalid final audit action: {event_actions}.",
                    setting_id=setting.id,
                    canonical_id=mapping.canonical_id,
                )
            )
        else:
            try:
                detail = json.loads(events[-1].detail or "")
                raw_note = detail["after"]["note"]
                decision_note = raw_note.strip() if isinstance(raw_note, str) else ""
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                decision_note = ""
            if not decision_note:
                failures.append(
                    _record(
                        "mapping_decision_note",
                        "Current canonical mapping has no audited reviewer decision note.",
                        setting_id=setting.id,
                        canonical_id=mapping.canonical_id,
                    )
                )
        name_targets[normalize_alias(effective_name(setting))].add(mapping.canonical_id)
    for normalized_name, canonical_ids in sorted(name_targets.items()):
        if len(canonical_ids) > 1:
            failures.append(
                _record(
                    "cross_canonical_name_conflict",
                    (
                        f"Mapped source name {normalized_name!r} resolves to multiple active "
                        f"identities: {sorted(canonical_ids)}."
                    ),
                )
            )

    identities = list(
        session.scalars(
            select(CanonicalSetting)
            .where(CanonicalSetting.status == canonical_settings.IDENTITY_ACTIVE)
            .order_by(CanonicalSetting.canonical_id)
        )
    )
    active_ids = {identity.canonical_id for identity in identities}
    canonical_results = []
    cross_vendor_count = 0
    single_vendor_count = 0
    for identity in identities:
        identity_rows = [row for row in mappings if row[0].canonical_id == identity.canonical_id]
        vendor_ids = sorted({manual.vendor_id for _, _, manual, _ in identity_rows})
        if len(vendor_ids) >= 2:
            cross_vendor_count += 1
        else:
            single_vendor_count += 1
        if not identity.description or not identity.description.strip():
            failures.append(
                _record(
                    "canonical_description",
                    "Active canonical identity has no reviewed description.",
                    canonical_id=identity.canonical_id,
                )
            )
        if not identity_rows:
            failures.append(
                _record(
                    "canonical_without_sources",
                    "Active canonical identity has no approved source mappings.",
                    canonical_id=identity.canonical_id,
                )
            )
        if session.get(CanonicalSettingClassification, identity.canonical_id) is None:
            failures.append(
                _record(
                    "canonical_classification",
                    "Active canonical identity has no classification projection.",
                    canonical_id=identity.canonical_id,
                )
            )
        detail = library.canonical_detail(session, identity.canonical_id)
        if detail is None:
            failures.append(
                _record(
                    "canonical_public_detail",
                    "Active canonical identity has no public detail page projection.",
                    canonical_id=identity.canonical_id,
                )
            )
            continue
        citation_count = sum(
            len(equivalence.citations) for equivalence in detail.setting.equivalent_vendor_names
        )
        if citation_count != len(identity_rows):
            failures.append(
                _record(
                    "equivalence_citations",
                    (
                        f"Expected {len(identity_rows)} mapped-source citations, found "
                        f"{citation_count}."
                    ),
                    canonical_id=identity.canonical_id,
                )
            )
        if {item.setting_id for item in detail.source_settings} != {
            setting.id for _, setting, _, _ in identity_rows
        }:
            failures.append(
                _record(
                    "canonical_public_sources",
                    "Public canonical detail does not expose the exact mapped source set.",
                    canonical_id=identity.canonical_id,
                )
            )
        canonical_results.append(
            {
                "canonical_id": identity.canonical_id,
                "canonical_name": identity.canonical_name,
                "mapped_source_count": len(identity_rows),
                "vendor_ids": vendor_ids,
                "vendor_count": len(vendor_ids),
                "cross_vendor_verified": len(vendor_ids) >= 2,
                "public_equivalence_citation_count": citation_count,
            }
        )

    source_public_ids: set[int] = set()
    first_source_page = library.browse(session, view=library.VIEW_SOURCE, page_size=100)
    for page_number in range(1, first_source_page.total_pages + 1):
        page = library.browse(
            session,
            view=library.VIEW_SOURCE,
            page=page_number,
            page_size=100,
        )
        source_public_ids.update(item.setting_id for item in page.items)
    if source_public_ids != approved_ids:
        failures.append(
            _record(
                "public_source_coverage",
                (
                    f"Public Library differs from approved settings: missing "
                    f"{sorted(approved_ids - source_public_ids)}, unexpected "
                    f"{sorted(source_public_ids - approved_ids)}."
                ),
            )
        )

    canonical_public_ids: set[str] = set()
    first_canonical_page = library.browse(
        session,
        view=library.VIEW_CANONICAL,
        page_size=100,
    )
    for page_number in range(1, first_canonical_page.total_pages + 1):
        page = library.browse(
            session,
            view=library.VIEW_CANONICAL,
            page=page_number,
            page_size=100,
        )
        canonical_public_ids.update(item.canonical_id for item in page.items)
    if canonical_public_ids != active_ids:
        failures.append(
            _record(
                "public_canonical_coverage",
                (
                    f"Public Canonical Library differs from active identities: missing "
                    f"{sorted(active_ids - canonical_public_ids)}, unexpected "
                    f"{sorted(canonical_public_ids - active_ids)}."
                ),
            )
        )

    return {
        "generated_at": utc_now_iso(),
        "app_version": config.APP_VERSION,
        "database_revision": revision,
        "status": "pass" if not failures else "fail",
        "summary": {
            "manuals_with_approved_settings": len(approved_manuals),
            "approved_source_settings": len(approved_ids),
            "trusted_approved_source_settings": (
                len(approved_ids)
                - sum(1 for failure in failures if failure["code"] == "source_trust")
            ),
            "public_source_settings": len(source_public_ids),
            "active_canonical_settings": len(active_ids),
            "public_canonical_settings": len(canonical_public_ids),
            "mapped_source_settings": len(mapped_ids),
            "unmapped_approved_source_settings": len(approved_ids - mapped_ids),
            "cross_vendor_canonical_settings": cross_vendor_count,
            "single_vendor_canonical_settings": single_vendor_count,
            "failures": len(failures),
            "warnings": len(warnings),
            "reviewed_repeated_labels": len(reviewed_repeated_labels),
        },
        "database": {
            "integrity": integrity,
            "foreign_key_violations": foreign_key_violations,
        },
        "official_manuals": manual_results,
        "canonical_settings": canonical_results,
        "reviewed_repeated_labels": reviewed_repeated_labels,
        "failures": failures,
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    with SessionLocal() as session:
        report = build_report(session)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, indent=2, ensure_ascii=True, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"Report: {args.output.resolve()}")
    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    if report["failures"]:
        print(json.dumps(report["failures"], indent=2, sort_keys=True))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
