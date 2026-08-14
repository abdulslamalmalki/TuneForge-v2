"""Reusable, read-only integrity checks beyond the public knowledge projection."""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import config
from app.db.models import (
    CanonicalSetting,
    CanonicalSettingAlias,
    EvidenceExtractionRun,
    EvidencePassage,
    EvidenceSource,
    ExtractionRun,
    GuidanceClaim,
    GuidanceClaimApplicabilityTarget,
    GuidanceClaimBiosScope,
    GuidanceClaimEvidence,
    GuidanceClaimSetting,
    GuidanceClaimSourceSetting,
    Manual,
    ManualApplicability,
    ManualApplicabilityTarget,
    MotherboardModel,
    RecommendationClaim,
    RecommendationClaimApplicabilityTarget,
    RecommendationClaimBiosScope,
    RecommendationClaimEvidence,
    RecommendationClaimSourceSetting,
    ResearchEvidenceIntake,
    Setting,
    TrustedWebResearchResult,
    TrustedWebResearchRun,
)
from app.domain import evidence_sources
from app.domain.errors import EvidenceSourceError
from app.normalization.canonical_settings import normalize_alias
from app.research import trusted_web


@dataclass(frozen=True)
class QualityFinding:
    severity: str
    code: str
    entity_type: str
    entity_id: str
    message: str


@dataclass(frozen=True)
class DataQualityReport:
    generated_at: str
    status: str
    summary: dict[str, int]
    findings: tuple[QualityFinding, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "generated_at": self.generated_at,
            "status": self.status,
            "summary": dict(self.summary),
            "findings": [asdict(item) for item in self.findings],
        }


def build_report(
    session: Session,
    *,
    verify_files: bool = True,
    now: datetime | None = None,
) -> DataQualityReport:
    """Audit persisted state without mutating records, files, or review history."""

    findings: list[QualityFinding] = []
    now = now or datetime.now(UTC)
    _database_checks(session, findings)
    _manual_checks(session, findings, verify_files=verify_files)
    _evidence_checks(session, findings, verify_files=verify_files)
    _identity_checks(session, findings)
    _guidance_checks(session, findings)
    _recommendation_checks(session, findings)
    _hardware_checks(session, findings)
    _research_checks(session, findings, now=now)
    findings.sort(key=lambda row: (row.severity, row.code, row.entity_type, row.entity_id))
    counts = Counter(item.severity for item in findings)
    summary = {
        "failures": counts["failure"],
        "warnings": counts["warning"],
        "informational": counts["informational"],
        "total_findings": len(findings),
    }
    return DataQualityReport(
        generated_at=now.isoformat(),
        status="fail" if counts["failure"] else "pass",
        summary=summary,
        findings=tuple(findings),
    )


def _finding(
    findings: list[QualityFinding],
    severity: str,
    code: str,
    entity_type: str,
    entity_id: object,
    message: str,
) -> None:
    findings.append(
        QualityFinding(severity, code, entity_type, str(entity_id), message)
    )


def _database_checks(session: Session, findings: list[QualityFinding]) -> None:
    integrity = session.scalar(text("PRAGMA integrity_check"))
    if integrity != "ok":
        _finding(findings, "failure", "database_integrity", "database", "sqlite", str(integrity))
    foreign_keys = list(session.execute(text("PRAGMA foreign_key_check")))
    if foreign_keys:
        _finding(
            findings,
            "failure",
            "foreign_key_violation",
            "database",
            "sqlite",
            f"SQLite reported {len(foreign_keys)} foreign-key violations.",
        )


def _manual_checks(
    session: Session,
    findings: list[QualityFinding],
    *,
    verify_files: bool,
) -> None:
    manuals = list(session.scalars(select(Manual).order_by(Manual.id)))
    runs = {row.manual_id: row for row in session.scalars(select(ExtractionRun))}
    applicability = {
        row.manual_id: row for row in session.scalars(select(ManualApplicability))
    }
    referenced: set[str] = set()
    for manual in manuals:
        referenced.add(manual.stored_filename)
        if manual.id not in runs:
            _finding(
                findings,
                "failure",
                "manual_missing_extraction_run",
                "manual",
                manual.id,
                "Every manual must have exactly one extraction run.",
            )
        if manual.id not in applicability:
            _finding(
                findings,
                "failure",
                "manual_missing_applicability",
                "manual",
                manual.id,
                "Every manual must have a normalized applicability projection.",
            )
        if manual.page_count < 1:
            _finding(
                findings, "failure", "manual_page_count", "manual", manual.id, "Invalid page count."
            )
        if manual.source_url and not manual.source_url.startswith("https://"):
            _finding(
                findings,
                "failure",
                "manual_source_url",
                "manual",
                manual.id,
                "Manual source URL is not HTTPS.",
            )
        if verify_files:
            _check_file(
                findings,
                entity_type="manual",
                entity_id=manual.id,
                root=config.UPLOADS_DIR,
                stored_filename=manual.stored_filename,
                expected_size=manual.file_size_bytes,
                expected_sha256=manual.sha256,
            )

    run_by_id = {row.id: row for row in runs.values()}
    manual_by_id = {row.id: row for row in manuals}
    for setting in session.scalars(select(Setting).order_by(Setting.id)):
        manual = manual_by_id.get(setting.manual_id)
        run = run_by_id.get(setting.extraction_run_id)
        if manual is None or run is None or run.manual_id != setting.manual_id:
            _finding(
                findings,
                "failure",
                "setting_extraction_mismatch",
                "setting",
                setting.id,
                "Setting manual and extraction-run ownership do not match.",
            )
            continue
        page = setting.edited_page_number or setting.page_number
        if not 1 <= page <= manual.page_count:
            _finding(
                findings,
                "failure",
                "setting_page_range",
                "setting",
                setting.id,
                f"Page {page} is outside manual range 1-{manual.page_count}.",
            )
    if verify_files:
        _dangling_files(findings, "manual_file", config.UPLOADS_DIR, referenced)


def _evidence_checks(
    session: Session,
    findings: list[QualityFinding],
    *,
    verify_files: bool,
) -> None:
    sources = list(session.scalars(select(EvidenceSource).order_by(EvidenceSource.id)))
    source_by_id = {row.id: row for row in sources}
    runs = {row.source_id: row for row in session.scalars(select(EvidenceExtractionRun))}
    run_by_id = {row.id: row for row in runs.values()}
    referenced: set[str] = set()
    for source in sources:
        referenced.add(source.stored_filename)
        if source.id not in runs:
            _finding(
                findings,
                "failure",
                "evidence_missing_extraction_run",
                "evidence_source",
                source.id,
                "Every evidence source must have exactly one extraction run.",
            )
        try:
            publisher_id, publisher_name, _ = evidence_sources.validate_official_source_url(
                source.publisher_id, source.source_url
            )
        except EvidenceSourceError:
            _finding(
                findings,
                "failure",
                "evidence_official_url",
                "evidence_source",
                source.id,
                "Evidence URL does not satisfy the current official publisher policy.",
            )
        else:
            if publisher_id != source.publisher_id or publisher_name != source.publisher_name:
                _finding(
                    findings,
                    "failure",
                    "evidence_publisher_identity",
                    "evidence_source",
                    source.id,
                    "Stored publisher metadata disagrees with official URL policy.",
                )
        if source.status in {"approved", "rejected"} and not all(
            (source.reviewer, source.reviewed_at, source.review_note)
        ):
            _finding(
                findings,
                "failure",
                "evidence_review_metadata",
                "evidence_source",
                source.id,
                "Reviewed evidence source has incomplete reviewer metadata.",
            )
        if verify_files:
            _check_file(
                findings,
                entity_type="evidence_source",
                entity_id=source.id,
                root=config.EVIDENCE_SOURCES_DIR,
                stored_filename=source.stored_filename,
                expected_size=source.file_size_bytes,
                expected_sha256=source.sha256,
            )

    for passage in session.scalars(select(EvidencePassage).order_by(EvidencePassage.id)):
        source = source_by_id.get(passage.source_id)
        run = run_by_id.get(passage.extraction_run_id)
        if source is None or run is None or run.source_id != passage.source_id:
            _finding(
                findings,
                "failure",
                "passage_extraction_mismatch",
                "evidence_passage",
                passage.id,
                "Passage source and extraction-run ownership do not match.",
            )
            continue
        if not 1 <= passage.page_number <= source.page_count:
            _finding(
                findings,
                "failure",
                "passage_page_range",
                "evidence_passage",
                passage.id,
                f"Page {passage.page_number} is outside source range 1-{source.page_count}.",
            )
        digest = hashlib.sha256(passage.passage_text.encode("utf-8")).hexdigest()
        if digest != passage.text_sha256:
            _finding(
                findings,
                "failure",
                "passage_hash",
                "evidence_passage",
                passage.id,
                "Immutable passage text no longer matches its SHA-256.",
            )
    if verify_files:
        _dangling_files(findings, "evidence_file", config.EVIDENCE_SOURCES_DIR, referenced)


def _identity_checks(session: Session, findings: list[QualityFinding]) -> None:
    identities = list(session.scalars(select(CanonicalSetting)))
    aliases = list(session.scalars(select(CanonicalSettingAlias)))
    normalized: dict[str, set[str]] = defaultdict(set)
    for identity in identities:
        normalized[normalize_alias(identity.canonical_name)].add(identity.canonical_id)
    for alias in aliases:
        normalized[normalize_alias(alias.display_alias)].add(alias.canonical_id)
    for key, canonical_ids in normalized.items():
        if key and len(canonical_ids) > 1:
            _finding(
                findings,
                "failure",
                "canonical_name_collision",
                "canonical_setting",
                ",".join(sorted(canonical_ids)),
                f"Normalized canonical name/alias {key!r} resolves to multiple identities.",
            )


def _guidance_checks(session: Session, findings: list[QualityFinding]) -> None:
    claims = list(session.scalars(select(GuidanceClaim).order_by(GuidanceClaim.id)))
    evidence = list(session.scalars(select(GuidanceClaimEvidence)))
    source_settings = list(session.scalars(select(GuidanceClaimSourceSetting)))
    settings = list(session.scalars(select(GuidanceClaimSetting)))
    evidence_claims = {row.claim_id for row in evidence}
    source_claims = {row.claim_id for row in source_settings}
    canonical_claims = {row.claim_id for row in settings}
    approved_setting_ids = set(
        session.scalars(select(Setting.id).where(Setting.status == "approved"))
    )
    approved_source_ids = set(
        session.scalars(select(EvidenceSource.id).where(EvidenceSource.status == "approved"))
    )
    passage_sources = dict(
        session.execute(select(EvidencePassage.id, EvidencePassage.source_id)).all()
    )
    signatures: dict[tuple[object, ...], list[int]] = defaultdict(list)
    targets = _target_fingerprints(
        session.scalars(select(GuidanceClaimApplicabilityTarget))
    )
    bios = _bios_fingerprints(session.scalars(select(GuidanceClaimBiosScope)), findings, "guidance")
    canonical_by_claim: dict[int, list[str]] = defaultdict(list)
    for row in settings:
        canonical_by_claim[row.claim_id].append(row.canonical_id)
    for claim in claims:
        if claim.status == "approved":
            if claim.id not in evidence_claims and claim.id not in source_claims:
                _finding(
                    findings,
                    "failure",
                    "guidance_missing_citation",
                    "guidance_claim",
                    claim.id,
                    "Approved guidance has no approved evidence or Source Setting citation.",
                )
            if claim.id not in canonical_claims:
                _finding(
                    findings,
                    "failure",
                    "guidance_missing_setting",
                    "guidance_claim",
                    claim.id,
                    "Approved guidance is not linked to a canonical setting.",
                )
            if not all((claim.reviewer, claim.reviewed_at, claim.review_note)):
                _finding(
                    findings,
                    "failure",
                    "guidance_review_metadata",
                    "guidance_claim",
                    claim.id,
                    "Approved guidance has incomplete reviewer metadata.",
                )
            signature = (
                claim.claim_type,
                normalize_alias(claim.claim_text),
                claim.applicability_scope,
                claim.cpu_model_id,
                tuple(sorted(canonical_by_claim[claim.id])),
                targets.get(claim.id, ()),
                bios.get(claim.id, ()),
            )
            signatures[signature].append(claim.id)
    for row in evidence:
        if passage_sources.get(row.passage_id) not in approved_source_ids:
            _finding(
                findings,
                "failure",
                "guidance_unapproved_evidence",
                "guidance_claim",
                row.claim_id,
                f"Evidence passage {row.passage_id} does not belong to an approved source.",
            )
    for row in source_settings:
        if row.setting_id not in approved_setting_ids:
            _finding(
                findings,
                "failure",
                "guidance_unapproved_source_setting",
                "guidance_claim",
                row.claim_id,
                f"Source Setting {row.setting_id} is not approved.",
            )
    _duplicate_findings(findings, "duplicate_guidance_claim", "guidance_claim", signatures)


def _recommendation_checks(session: Session, findings: list[QualityFinding]) -> None:
    claims = list(session.scalars(select(RecommendationClaim).order_by(RecommendationClaim.id)))
    evidence_claims = set(
        session.scalars(select(RecommendationClaimEvidence.claim_id))
    )
    source_claims = set(
        session.scalars(select(RecommendationClaimSourceSetting.claim_id))
    )
    target_claims = set(
        session.scalars(select(RecommendationClaimApplicabilityTarget.claim_id))
    )
    targets = _target_fingerprints(
        session.scalars(select(RecommendationClaimApplicabilityTarget))
    )
    bios = _bios_fingerprints(
        session.scalars(select(RecommendationClaimBiosScope)), findings, "recommendation"
    )
    signatures: dict[tuple[object, ...], list[int]] = defaultdict(list)
    for claim in claims:
        if claim.status == "approved":
            if claim.id not in evidence_claims and claim.id not in source_claims:
                _finding(
                    findings,
                    "failure",
                    "recommendation_missing_citation",
                    "recommendation_claim",
                    claim.id,
                    "Approved recommendation has no supporting citation.",
                )
            if claim.id not in target_claims:
                _finding(
                    findings,
                    "failure",
                    "recommendation_missing_hardware_scope",
                    "recommendation_claim",
                    claim.id,
                    "Approved recommendation has no explicit hardware target.",
                )
            if not all((claim.reviewer, claim.reviewed_at, claim.review_note)):
                _finding(
                    findings,
                    "failure",
                    "recommendation_review_metadata",
                    "recommendation_claim",
                    claim.id,
                    "Approved recommendation has incomplete reviewer metadata.",
                )
            if claim.conflict_state not in {"none", "resolved"}:
                _finding(
                    findings,
                    "failure",
                    "recommendation_conflict_state",
                    "recommendation_claim",
                    claim.id,
                    "Approved recommendation has an unresolved conflict state.",
                )
            signatures[
                (
                    claim.canonical_id,
                    claim.use_case,
                    claim.recommended_action,
                    claim.value_mode,
                    claim.exact_value,
                    claim.minimum_value,
                    claim.maximum_value,
                    claim.value_unit,
                    normalize_alias(claim.recommendation_text),
                    targets.get(claim.id, ()),
                    bios.get(claim.id, ()),
                )
            ].append(claim.id)
    _duplicate_findings(
        findings,
        "duplicate_recommendation_claim",
        "recommendation_claim",
        signatures,
    )


def _hardware_checks(session: Session, findings: list[QualityFinding]) -> None:
    applicability = {
        row.manual_id: row for row in session.scalars(select(ManualApplicability))
    }
    targets: dict[int, list[ManualApplicabilityTarget]] = defaultdict(list)
    for row in session.scalars(select(ManualApplicabilityTarget)):
        targets[row.manual_id].append(row)
    models = {row.model_id: row for row in session.scalars(select(MotherboardModel))}
    manuals = {row.id: row for row in session.scalars(select(Manual))}
    for manual_id, projection in applicability.items():
        rows = targets.get(manual_id, [])
        if projection.scope != "unknown" and not rows:
            _finding(
                findings,
                "failure",
                "applicability_missing_target",
                "manual",
                manual_id,
                "Resolved manual applicability has no normalized target.",
            )
        manual = manuals.get(manual_id)
        for row in rows:
            if row.motherboard_model_id:
                model = models.get(row.motherboard_model_id)
                if model and manual and manual.vendor_id and model.vendor_id != manual.vendor_id:
                    _finding(
                        findings,
                        "failure",
                        "motherboard_vendor_mismatch",
                        "manual",
                        manual_id,
                        f"Target model {model.model_id} belongs to another vendor.",
                    )


def _research_checks(
    session: Session,
    findings: list[QualityFinding],
    *,
    now: datetime,
) -> None:
    results_by_run = Counter(
        session.scalars(select(TrustedWebResearchResult.run_id))
    )
    stale_before = now - timedelta(hours=24)
    for run in session.scalars(select(TrustedWebResearchRun).order_by(TrustedWebResearchRun.id)):
        if run.result_count != results_by_run[run.id]:
            _finding(
                findings,
                "failure",
                "research_result_count",
                "research_run",
                run.id,
                f"Stored result_count={run.result_count}, actual={results_by_run[run.id]}.",
            )
        created = _parse_datetime(run.created_at)
        if run.status == "running" and created and created < stale_before:
            _finding(
                findings,
                "warning",
                "stale_research_run",
                "research_run",
                run.id,
                "Research run has remained running for more than 24 hours.",
            )
    for result in session.scalars(select(TrustedWebResearchResult)):
        if not trusted_web.is_allowlisted_official_url(result.source_url):
            _finding(
                findings,
                "failure",
                "research_official_url",
                "research_result",
                result.id,
                "Stored research URL no longer satisfies the official allowlist.",
            )
    intake_ids = set(session.scalars(select(ResearchEvidenceIntake.result_id)))
    unreviewed = set(session.scalars(select(TrustedWebResearchResult.id))) - intake_ids
    if unreviewed:
        _finding(
            findings,
            "informational",
            "research_awaiting_triage",
            "research_result",
            ",".join(str(value) for value in sorted(unreviewed)),
            f"{len(unreviewed)} official research result(s) await reviewer triage.",
        )


def _target_fingerprints(rows) -> dict[int, tuple[tuple[object, ...], ...]]:
    values: dict[int, list[tuple[object, ...]]] = defaultdict(list)
    for row in rows:
        values[row.claim_id].append(
            (
                row.cpu_model_id,
                row.cpu_family_id,
                row.cpu_architecture_id,
                row.motherboard_model_id,
                row.chipset_id,
                row.chipset_family_id,
                row.platform_id,
                row.vendor_id,
                row.cpu_vendor_id,
                row.motherboard_family_id,
                row.evidence_level,
            )
        )
    return {key: tuple(sorted(items, key=str)) for key, items in values.items()}


def _bios_fingerprints(rows, findings, prefix: str) -> dict[int, tuple[tuple[object, ...], ...]]:
    values: dict[int, list[tuple[object, ...]]] = defaultdict(list)
    for row in rows:
        issue = _bios_range_issue(row)
        if issue:
            _finding(
                findings,
                "failure",
                f"{prefix}_bios_range",
                f"{prefix}_claim",
                row.claim_id,
                issue,
            )
        values[row.claim_id].append(
            (
                row.motherboard_model_id,
                row.vendor_id,
                row.bios_version_exact,
                row.valid_from,
                row.valid_until,
                row.introduced_in,
                row.changed_in,
                row.deprecated_in,
                row.comparison_mode,
            )
        )
    return {key: tuple(sorted(items, key=str)) for key, items in values.items()}


def _bios_range_issue(row) -> str | None:
    if not row.valid_from or not row.valid_until:
        return None
    if row.comparison_mode == "integer_numeric":
        try:
            start = (int(row.valid_from),)
            end = (int(row.valid_until),)
        except ValueError:
            return "Integer BIOS range contains a non-integer version."
    elif row.comparison_mode == "dotted_numeric":
        try:
            start = tuple(int(item) for item in row.valid_from.split("."))
            end = tuple(int(item) for item in row.valid_until.split("."))
        except ValueError:
            return "Dotted BIOS range contains a non-numeric component."
    else:
        return "Opaque BIOS versions cannot define an ordered range."
    if start > end:
        return f"BIOS range starts at {row.valid_from} after ending at {row.valid_until}."
    return None


def _duplicate_findings(findings, code: str, entity_type: str, signatures) -> None:
    for ids in signatures.values():
        if len(ids) > 1:
            _finding(
                findings,
                "warning",
                code,
                entity_type,
                ",".join(str(value) for value in sorted(ids)),
                "Exact normalized duplicate requires reviewer disposition; no record was changed.",
            )


def _check_file(
    findings: list[QualityFinding],
    *,
    entity_type: str,
    entity_id: object,
    root: Path,
    stored_filename: str,
    expected_size: int,
    expected_sha256: str,
) -> None:
    root = root.resolve()
    source = (root / stored_filename).resolve()
    if source.parent != root:
        _finding(
            findings,
            "failure",
            "unsafe_stored_filename",
            entity_type,
            entity_id,
            "Stored filename resolves outside its managed data directory.",
        )
        return
    if not source.is_file():
        _finding(
            findings,
            "failure",
            "missing_stored_file",
            entity_type,
            entity_id,
            f"Stored file is missing: {stored_filename}.",
        )
        return
    if source.stat().st_size != expected_size:
        _finding(
            findings,
            "failure",
            "stored_file_size",
            entity_type,
            entity_id,
            "Stored file size differs from provenance metadata.",
        )
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != expected_sha256:
        _finding(
            findings,
            "failure",
            "stored_file_hash",
            entity_type,
            entity_id,
            "Stored file SHA-256 differs from provenance metadata.",
        )


def _dangling_files(
    findings: list[QualityFinding],
    entity_type: str,
    root: Path,
    referenced: set[str],
) -> None:
    if not root.is_dir():
        return
    extras = sorted(
        path.name
        for path in root.iterdir()
        if path.is_file() and path.name not in referenced
    )
    if extras:
        _finding(
            findings,
            "warning",
            "dangling_stored_files",
            entity_type,
            ",".join(extras),
            f"{len(extras)} unreferenced file(s) require operator review; none were deleted.",
        )


def _parse_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
