"""Reviewer-controlled guidance claims grounded in accepted evidence passages."""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import config
from app.auth.context import current_actor
from app.db.models import (
    CanonicalSetting,
    Chipset,
    ChipsetFamily,
    CpuArchitecture,
    CpuFamily,
    CpuModel,
    CpuVendor,
    EvidencePassage,
    EvidenceSource,
    GuidanceClaim,
    GuidanceClaimApplicabilityTarget,
    GuidanceClaimBiosScope,
    GuidanceClaimEvent,
    GuidanceClaimEvidence,
    GuidanceClaimSetting,
    GuidanceClaimSourceSetting,
    HardwarePlatform,
    Manual,
    MotherboardFamily,
    MotherboardModel,
    Setting,
    SettingCanonicalMapping,
    Vendor,
)
from app.domain import canonicalization
from app.domain.errors import GuidanceClaimError
from app.domain.extraction_runs import utc_now_iso

ACTOR = "local-reviewer"
STATUS_DRAFT = "draft"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUSES = (STATUS_DRAFT, STATUS_APPROVED, STATUS_REJECTED)
CLAIM_TYPES = ("definition", "behavior", "constraint", "safety")
APPLICABILITY_SOURCE_SCOPE = "source_scope"
APPLICABILITY_CPU_MODEL = "cpu_model"
APPLICABILITY_REVIEWED_HARDWARE = "reviewed_hardware"
BIOS_COMPARISON_MODES = ("opaque", "integer_numeric", "dotted_numeric")
BIOS_CONTEXT_TYPES = ("motherboard_model", "vendor")

TARGET_CPU_MODEL = "cpu_model"
TARGET_CPU_FAMILY = "cpu_family"
TARGET_CPU_ARCHITECTURE = "cpu_architecture"
TARGET_MOTHERBOARD_MODEL = "motherboard_model"
TARGET_CHIPSET = "chipset"
TARGET_CHIPSET_FAMILY = "chipset_family"
TARGET_PLATFORM = "platform"
TARGET_VENDOR = "vendor"
TARGET_CPU_VENDOR = "cpu_vendor"
TARGET_MOTHERBOARD_FAMILY = "motherboard_family"
TARGET_TYPES = (
    TARGET_CPU_MODEL,
    TARGET_CPU_FAMILY,
    TARGET_CPU_ARCHITECTURE,
    TARGET_MOTHERBOARD_MODEL,
    TARGET_CHIPSET,
    TARGET_CHIPSET_FAMILY,
    TARGET_PLATFORM,
    TARGET_VENDOR,
    TARGET_CPU_VENDOR,
    TARGET_MOTHERBOARD_FAMILY,
)

_TARGET_DEFINITIONS = {
    TARGET_CPU_MODEL: (CpuModel, "cpu_model_id", "cpu_model_id", "exact_cpu"),
    TARGET_CPU_FAMILY: (CpuFamily, "family_id", "cpu_family_id", "family"),
    TARGET_CPU_ARCHITECTURE: (
        CpuArchitecture,
        "architecture_id",
        "cpu_architecture_id",
        "architecture",
    ),
    TARGET_MOTHERBOARD_MODEL: (
        MotherboardModel,
        "model_id",
        "motherboard_model_id",
        "exact_model",
    ),
    TARGET_CHIPSET: (Chipset, "chipset_id", "chipset_id", "exact_chipset"),
    TARGET_CHIPSET_FAMILY: (
        ChipsetFamily,
        "family_id",
        "chipset_family_id",
        "family",
    ),
    TARGET_PLATFORM: (HardwarePlatform, "platform_id", "platform_id", "platform"),
    TARGET_VENDOR: (Vendor, "vendor_id", "vendor_id", "vendor"),
    TARGET_CPU_VENDOR: (CpuVendor, "vendor_id", "cpu_vendor_id", "vendor"),
    TARGET_MOTHERBOARD_FAMILY: (
        MotherboardFamily,
        "family_id",
        "motherboard_family_id",
        "family",
    ),
}


@dataclass(frozen=True)
class GuidanceCitation:
    passage: EvidencePassage
    source: EvidenceSource


@dataclass(frozen=True)
class GuidanceSourceSettingCitation:
    setting: Setting
    manual: Manual


@dataclass(frozen=True)
class ApplicabilityTargetInput:
    target_type: str
    target_id: str
    evidence_basis: str


@dataclass(frozen=True)
class GuidanceApplicabilityTarget:
    target_type: str
    target_id: str
    canonical_name: str
    evidence_level: str
    evidence_basis: str


@dataclass(frozen=True)
class BiosScopeInput:
    context_type: str
    context_id: str
    bios_version_exact: str = ""
    valid_from: str = ""
    valid_until: str = ""
    introduced_in: str = ""
    changed_in: str = ""
    deprecated_in: str = ""
    comparison_mode: str = "opaque"
    version_notes: str = ""
    evidence_basis: str = ""


@dataclass(frozen=True)
class GuidanceBiosScope:
    context_type: str
    context_id: str
    context_name: str
    bios_version_exact: str | None
    valid_from: str | None
    valid_until: str | None
    introduced_in: str | None
    changed_in: str | None
    deprecated_in: str | None
    comparison_mode: str
    version_notes: str
    evidence_basis: str


@dataclass(frozen=True)
class GuidanceClaimOverview:
    claim: GuidanceClaim
    evidence_count: int
    setting_count: int


@dataclass(frozen=True)
class GuidanceClaimDetail:
    claim: GuidanceClaim
    evidence: tuple[GuidanceCitation, ...]
    source_settings: tuple[GuidanceSourceSettingCitation, ...]
    settings: tuple[CanonicalSetting, ...]
    cpu_model: CpuModel | None
    applicability_targets: tuple[GuidanceApplicabilityTarget, ...]
    bios_scopes: tuple[GuidanceBiosScope, ...]
    events: tuple[GuidanceClaimEvent, ...]


def create_claim(
    session: Session,
    *,
    claim_text: str,
    claim_type: str,
    passage_ids: tuple[int, ...] | list[int],
    source_setting_ids: tuple[int, ...] | list[int] = (),
    canonical_ids: tuple[str, ...] | list[str],
    applicability_scope: str = APPLICABILITY_SOURCE_SCOPE,
    cpu_model_id: str | None = None,
    applicability_targets: Sequence[ApplicabilityTargetInput] | None = None,
    bios_scopes: Sequence[BiosScopeInput] | None = None,
    actor: str | None = None,
    commit: bool = True,
) -> GuidanceClaim:
    actor = actor or current_actor()
    values = _validate_claim_values(
        session,
        claim_text=claim_text,
        claim_type=claim_type,
        passage_ids=passage_ids,
        source_setting_ids=source_setting_ids,
        canonical_ids=canonical_ids,
        applicability_scope=applicability_scope,
        cpu_model_id=cpu_model_id,
        applicability_targets=applicability_targets,
        bios_scopes=bios_scopes,
    )
    duplicate = session.scalar(
        select(GuidanceClaim.id).where(
            func.lower(GuidanceClaim.claim_text) == values["claim_text"].lower()
        )
    )
    if duplicate is not None:
        raise GuidanceClaimError(
            f"An equivalent guidance claim already exists as claim {duplicate}."
        )
    now = utc_now_iso()
    claim = GuidanceClaim(
        claim_text=values["claim_text"],
        claim_type=values["claim_type"],
        applicability_scope=values["applicability_scope"],
        cpu_model_id=values["cpu_model_id"],
        status=STATUS_DRAFT,
        review_note=None,
        reviewer=None,
        reviewed_at=None,
        created_at=now,
        updated_at=now,
    )
    session.add(claim)
    session.flush()
    _replace_links(
        session,
        claim.id,
        passage_ids=values["passage_ids"],
        source_setting_ids=values["source_setting_ids"],
        canonical_ids=values["canonical_ids"],
    )
    _replace_applicability_targets(session, claim.id, values["applicability_targets"])
    _replace_bios_scopes(session, claim.id, values["bios_scopes"])
    session.add(
        GuidanceClaimEvent(
            claim_id=claim.id,
            action="candidate_created",
            actor=actor,
            detail=json.dumps(_claim_snapshot(claim, values), sort_keys=True),
            created_at=now,
        )
    )
    if commit:
        session.commit()
        session.refresh(claim)
    else:
        session.flush()
    return claim


def list_claims(session: Session, *, status: str = "") -> list[GuidanceClaimOverview]:
    status = status.strip().lower()
    if status and status not in STATUSES:
        raise GuidanceClaimError("Status filter must be draft, approved, or rejected.")
    evidence_counts = (
        select(GuidanceClaimEvidence.claim_id, func.count().label("evidence_count"))
        .group_by(GuidanceClaimEvidence.claim_id)
        .subquery()
    )
    source_setting_counts = (
        select(
            GuidanceClaimSourceSetting.claim_id,
            func.count().label("source_setting_count"),
        )
        .group_by(GuidanceClaimSourceSetting.claim_id)
        .subquery()
    )
    setting_counts = (
        select(GuidanceClaimSetting.claim_id, func.count().label("setting_count"))
        .group_by(GuidanceClaimSetting.claim_id)
        .subquery()
    )
    query = (
        select(
            GuidanceClaim,
            func.coalesce(evidence_counts.c.evidence_count, 0),
            func.coalesce(source_setting_counts.c.source_setting_count, 0),
            func.coalesce(setting_counts.c.setting_count, 0),
        )
        .outerjoin(evidence_counts, evidence_counts.c.claim_id == GuidanceClaim.id)
        .outerjoin(
            source_setting_counts,
            source_setting_counts.c.claim_id == GuidanceClaim.id,
        )
        .outerjoin(setting_counts, setting_counts.c.claim_id == GuidanceClaim.id)
        .order_by(GuidanceClaim.id)
    )
    if status:
        query = query.where(GuidanceClaim.status == status)
    return [
        GuidanceClaimOverview(
            claim=claim,
            evidence_count=evidence_count,
            setting_count=setting_count,
        )
        for claim, evidence_count, source_setting_count, setting_count in session.execute(query)
        for evidence_count in (evidence_count + source_setting_count,)
    ]


def status_counts(session: Session) -> dict[str, int]:
    counts = dict(
        session.execute(
            select(GuidanceClaim.status, func.count()).group_by(GuidanceClaim.status)
        ).all()
    )
    return {status: counts.get(status, 0) for status in STATUSES}


def get_claim(session: Session, claim_id: int) -> GuidanceClaim | None:
    return session.get(GuidanceClaim, claim_id)


def list_applicability_targets(
    session: Session, claim_id: int
) -> tuple[GuidanceApplicabilityTarget, ...]:
    return tuple(
        _applicability_target_view(session, row)
        for row in session.scalars(
            select(GuidanceClaimApplicabilityTarget)
            .where(GuidanceClaimApplicabilityTarget.claim_id == claim_id)
            .order_by(GuidanceClaimApplicabilityTarget.ordinal)
        )
    )


def list_bios_scopes(
    session: Session, claim_id: int
) -> tuple[GuidanceBiosScope, ...]:
    return tuple(
        _bios_scope_view(session, row)
        for row in session.scalars(
            select(GuidanceClaimBiosScope)
            .where(GuidanceClaimBiosScope.claim_id == claim_id)
            .order_by(GuidanceClaimBiosScope.ordinal)
        )
    )


def get_detail(session: Session, claim_id: int) -> GuidanceClaimDetail | None:
    claim = session.get(GuidanceClaim, claim_id)
    if claim is None:
        return None
    evidence = tuple(
        GuidanceCitation(passage=passage, source=source)
        for passage, source in session.execute(
            select(EvidencePassage, EvidenceSource)
            .join(
                GuidanceClaimEvidence,
                GuidanceClaimEvidence.passage_id == EvidencePassage.id,
            )
            .join(EvidenceSource, EvidenceSource.id == EvidencePassage.source_id)
            .where(GuidanceClaimEvidence.claim_id == claim_id)
            .order_by(EvidencePassage.page_number, EvidencePassage.ordinal)
        )
    )
    source_settings = tuple(
        GuidanceSourceSettingCitation(setting=setting, manual=manual)
        for setting, manual in session.execute(
            select(Setting, Manual)
            .join(
                GuidanceClaimSourceSetting,
                GuidanceClaimSourceSetting.setting_id == Setting.id,
            )
            .join(Manual, Manual.id == Setting.manual_id)
            .where(GuidanceClaimSourceSetting.claim_id == claim_id)
            .order_by(Manual.id, Setting.page_number, Setting.id)
        )
    )
    settings = tuple(
        session.scalars(
            select(CanonicalSetting)
            .join(
                GuidanceClaimSetting,
                GuidanceClaimSetting.canonical_id == CanonicalSetting.canonical_id,
            )
            .where(GuidanceClaimSetting.claim_id == claim_id)
            .order_by(CanonicalSetting.canonical_name)
        )
    )
    cpu_model = session.get(CpuModel, claim.cpu_model_id) if claim.cpu_model_id else None
    applicability_targets = list_applicability_targets(session, claim_id)
    bios_scopes = list_bios_scopes(session, claim_id)
    events = tuple(
        session.scalars(
            select(GuidanceClaimEvent)
            .where(GuidanceClaimEvent.claim_id == claim_id)
            .order_by(GuidanceClaimEvent.id)
        )
    )
    return GuidanceClaimDetail(
        claim=claim,
        evidence=evidence,
        source_settings=source_settings,
        settings=settings,
        cpu_model=cpu_model,
        applicability_targets=applicability_targets,
        bios_scopes=bios_scopes,
        events=events,
    )


def edit_claim(
    session: Session,
    claim: GuidanceClaim,
    *,
    claim_text: str,
    claim_type: str,
    passage_ids: tuple[int, ...] | list[int],
    source_setting_ids: tuple[int, ...] | list[int] = (),
    canonical_ids: tuple[str, ...] | list[str],
    applicability_scope: str = APPLICABILITY_SOURCE_SCOPE,
    cpu_model_id: str | None = None,
    applicability_targets: Sequence[ApplicabilityTargetInput] | None = None,
    bios_scopes: Sequence[BiosScopeInput] | None = None,
) -> bool:
    if claim.status != STATUS_DRAFT:
        raise GuidanceClaimError("Only draft guidance claims can be edited.")
    values = _validate_claim_values(
        session,
        claim_text=claim_text,
        claim_type=claim_type,
        passage_ids=passage_ids,
        source_setting_ids=source_setting_ids,
        canonical_ids=canonical_ids,
        applicability_scope=applicability_scope,
        cpu_model_id=cpu_model_id,
        applicability_targets=applicability_targets,
        bios_scopes=bios_scopes,
    )
    detail = get_detail(session, claim.id)
    if detail is None:
        raise RuntimeError(f"Guidance claim {claim.id} could not be reloaded.")
    before = _detail_snapshot(detail)
    after = {
        "claim_text": values["claim_text"],
        "claim_type": values["claim_type"],
        "applicability_scope": values["applicability_scope"],
        "cpu_model_id": values["cpu_model_id"],
        "passage_ids": list(values["passage_ids"]),
        "source_setting_ids": list(values["source_setting_ids"]),
        "canonical_ids": list(values["canonical_ids"]),
        "applicability_targets": list(values["applicability_targets"]),
        "bios_scopes": list(values["bios_scopes"]),
    }
    if before == after:
        return False
    duplicate = session.scalar(
        select(GuidanceClaim.id).where(
            GuidanceClaim.id != claim.id,
            func.lower(GuidanceClaim.claim_text) == values["claim_text"].lower(),
        )
    )
    if duplicate is not None:
        raise GuidanceClaimError(
            f"An equivalent guidance claim already exists as claim {duplicate}."
        )
    now = utc_now_iso()
    claim.claim_text = values["claim_text"]
    claim.claim_type = values["claim_type"]
    claim.applicability_scope = values["applicability_scope"]
    claim.cpu_model_id = values["cpu_model_id"]
    claim.updated_at = now
    _replace_links(
        session,
        claim.id,
        passage_ids=values["passage_ids"],
        source_setting_ids=values["source_setting_ids"],
        canonical_ids=values["canonical_ids"],
    )
    _replace_applicability_targets(session, claim.id, values["applicability_targets"])
    _replace_bios_scopes(session, claim.id, values["bios_scopes"])
    session.add(
        GuidanceClaimEvent(
            claim_id=claim.id,
            action="candidate_edited",
            actor=current_actor(),
            detail=json.dumps({"before": before, "after": after}, sort_keys=True),
            created_at=now,
        )
    )
    session.commit()
    return True


def review_claim(
    session: Session,
    claim: GuidanceClaim,
    *,
    decision: str,
    note: str,
) -> GuidanceClaim:
    if claim.status != STATUS_DRAFT:
        raise GuidanceClaimError("Only draft guidance claims can be reviewed.")
    decision = decision.strip().lower()
    if decision not in {STATUS_APPROVED, STATUS_REJECTED}:
        raise GuidanceClaimError("Decision must be approved or rejected.")
    note = " ".join(note.split())
    if not note:
        raise GuidanceClaimError("Record a reviewer note for this decision.")
    if len(note) > config.MAX_GUIDANCE_REVIEW_NOTE_LENGTH:
        raise GuidanceClaimError(
            f"Reviewer note must be {config.MAX_GUIDANCE_REVIEW_NOTE_LENGTH} characters or fewer."
        )
    detail = get_detail(session, claim.id)
    if detail is None:
        raise RuntimeError(f"Guidance claim {claim.id} could not be reloaded.")
    if decision == STATUS_APPROVED:
        if not detail.evidence and not detail.source_settings:
            raise GuidanceClaimError("An approved claim must have supporting evidence.")
        if not detail.settings:
            raise GuidanceClaimError("An approved claim must link to a canonical setting.")
        if any(citation.source.status != "approved" for citation in detail.evidence):
            raise GuidanceClaimError("All supporting evidence sources must be approved.")
        for citation in detail.source_settings:
            reasons = canonicalization.source_trust_reasons(citation.setting, citation.manual)
            if reasons:
                raise GuidanceClaimError(
                    "All supporting manual settings must remain trusted and approved."
                )
        if claim.applicability_scope == APPLICABILITY_REVIEWED_HARDWARE and not (
            detail.applicability_targets
        ):
            raise GuidanceClaimError(
                "Reviewed-hardware applicability requires at least one reviewed target."
            )
        if claim.applicability_scope == APPLICABILITY_CPU_MODEL and not (
            detail.applicability_targets
        ):
            raise GuidanceClaimError("CPU-model applicability has no reviewed target.")
    now = utc_now_iso()
    claim.status = decision
    claim.review_note = note
    actor = current_actor()
    claim.reviewer = actor
    claim.reviewed_at = now
    claim.updated_at = now
    session.add(
        GuidanceClaimEvent(
            claim_id=claim.id,
            action=f"claim_{decision}",
            actor=actor,
            detail=json.dumps({"note": note}, sort_keys=True),
            created_at=now,
        )
    )
    session.commit()
    session.refresh(claim)
    return claim


def validate_applicability_targets(
    session: Session,
    targets: Sequence[ApplicabilityTargetInput],
) -> tuple[dict[str, str], ...]:
    """Shared normalized hardware-scope validator for reviewed claim types."""

    return _validate_applicability_targets(session, targets)


def validate_bios_scopes(
    session: Session,
    scopes: Sequence[BiosScopeInput],
) -> tuple[dict[str, str | None], ...]:
    """Shared BIOS-scope validator for reviewed claim types."""

    return _validate_bios_scopes(session, scopes)


def validate_bios_scope_coherence(
    session: Session,
    targets: Sequence[dict[str, str]],
    scopes: Sequence[dict[str, str | None]],
) -> None:
    """Reject BIOS contexts that conflict with normalized hardware scope."""

    _validate_bios_scope_coherence(session, targets, scopes)


def applicability_target_view(session: Session, row) -> GuidanceApplicabilityTarget:
    """Project a validated hardware-target row from any reviewed claim table."""

    return _applicability_target_view(session, row)


def bios_scope_view(session: Session, row) -> GuidanceBiosScope:
    """Project a validated BIOS-scope row from any reviewed claim table."""

    return _bios_scope_view(session, row)


def _validate_claim_values(
    session: Session,
    *,
    claim_text: str,
    claim_type: str,
    passage_ids: tuple[int, ...] | list[int],
    source_setting_ids: tuple[int, ...] | list[int],
    canonical_ids: tuple[str, ...] | list[str],
    applicability_scope: str,
    cpu_model_id: str | None,
    applicability_targets: Sequence[ApplicabilityTargetInput] | None,
    bios_scopes: Sequence[BiosScopeInput] | None,
) -> dict[str, object]:
    claim_text = " ".join(claim_text.split())
    if not claim_text:
        raise GuidanceClaimError("Claim text is required.")
    if len(claim_text) > config.MAX_GUIDANCE_CLAIM_CHARS:
        raise GuidanceClaimError(
            f"Claim text must be {config.MAX_GUIDANCE_CLAIM_CHARS} characters or fewer."
        )
    claim_type = claim_type.strip().lower()
    if claim_type not in CLAIM_TYPES:
        raise GuidanceClaimError("Claim type must be definition, behavior, constraint, or safety.")
    applicability_scope = applicability_scope.strip().lower()
    cpu_model_id = (cpu_model_id or "").strip() or None
    if applicability_scope == APPLICABILITY_CPU_MODEL:
        if cpu_model_id is None or session.get(CpuModel, cpu_model_id) is None:
            raise GuidanceClaimError("Choose a known CPU model for CPU-model applicability.")
        raise GuidanceClaimError(
            "Legacy CPU-model scope is not accepted without an evidence basis; use "
            "reviewed_hardware with an explicit cpu_model target."
        )
    elif applicability_scope == APPLICABILITY_REVIEWED_HARDWARE:
        if cpu_model_id is not None:
            raise GuidanceClaimError(
                "Reviewed-hardware scope uses normalized targets, not the legacy CPU field."
            )
        if not applicability_targets:
            raise GuidanceClaimError(
                "Reviewed-hardware scope requires at least one normalized target."
            )
    elif applicability_scope != APPLICABILITY_SOURCE_SCOPE or cpu_model_id is not None:
        raise GuidanceClaimError(
            "Choose source_scope, cpu_model, or reviewed_hardware applicability."
        )
    elif applicability_targets:
        raise GuidanceClaimError(
            "Source-scope claims cannot carry exact reviewed hardware targets."
        )
    clean_applicability_targets = _validate_applicability_targets(
        session, applicability_targets or ()
    )
    clean_bios_scopes = _validate_bios_scopes(session, bios_scopes or ())
    _validate_bios_scope_coherence(
        session, clean_applicability_targets, clean_bios_scopes
    )
    clean_passage_ids = tuple(sorted(dict.fromkeys(int(value) for value in passage_ids)))
    evidence_rows = session.execute(
        select(EvidencePassage.id, EvidenceSource.status)
        .join(EvidenceSource, EvidenceSource.id == EvidencePassage.source_id)
        .where(EvidencePassage.id.in_(clean_passage_ids))
    ).all()
    if len(evidence_rows) != len(clean_passage_ids):
        raise GuidanceClaimError("One or more evidence passages were not found.")
    if any(source_status != "approved" for _, source_status in evidence_rows):
        raise GuidanceClaimError("Guidance claims require approved evidence sources.")
    clean_source_setting_ids = tuple(
        sorted(dict.fromkeys(int(value) for value in source_setting_ids))
    )
    source_rows = list(
        session.execute(
            select(Setting, Manual, SettingCanonicalMapping.canonical_id)
            .join(Manual, Manual.id == Setting.manual_id)
            .outerjoin(
                SettingCanonicalMapping,
                SettingCanonicalMapping.setting_id == Setting.id,
            )
            .where(Setting.id.in_(clean_source_setting_ids))
        )
    )
    if len(source_rows) != len(clean_source_setting_ids):
        raise GuidanceClaimError("One or more source settings were not found.")
    if not clean_passage_ids and not clean_source_setting_ids:
        raise GuidanceClaimError("Select at least one evidence passage or approved source setting.")
    clean_canonical_ids = tuple(
        sorted(dict.fromkeys(value.strip() for value in canonical_ids if value.strip()))
    )
    if not clean_canonical_ids:
        raise GuidanceClaimError("Link the claim to at least one canonical setting.")
    found_canonical_ids = set(
        session.scalars(
            select(CanonicalSetting.canonical_id).where(
                CanonicalSetting.canonical_id.in_(clean_canonical_ids)
            )
        )
    )
    if found_canonical_ids != set(clean_canonical_ids):
        raise GuidanceClaimError("One or more canonical settings were not found.")
    for setting, manual, mapped_canonical_id in source_rows:
        if canonicalization.source_trust_reasons(setting, manual):
            raise GuidanceClaimError(
                "Guidance claims require trusted, page-reviewed source settings."
            )
        if mapped_canonical_id not in clean_canonical_ids:
            raise GuidanceClaimError(
                "Every source setting must map to a selected canonical setting."
            )
    return {
        "claim_text": claim_text,
        "claim_type": claim_type,
        "applicability_scope": applicability_scope,
        "cpu_model_id": cpu_model_id,
        "passage_ids": clean_passage_ids,
        "source_setting_ids": clean_source_setting_ids,
        "canonical_ids": clean_canonical_ids,
        "applicability_targets": clean_applicability_targets,
        "bios_scopes": clean_bios_scopes,
    }


def _replace_links(
    session: Session,
    claim_id: int,
    *,
    passage_ids: tuple[int, ...],
    source_setting_ids: tuple[int, ...],
    canonical_ids: tuple[str, ...],
) -> None:
    session.execute(delete(GuidanceClaimEvidence).where(GuidanceClaimEvidence.claim_id == claim_id))
    session.execute(
        delete(GuidanceClaimSourceSetting).where(GuidanceClaimSourceSetting.claim_id == claim_id)
    )
    session.execute(delete(GuidanceClaimSetting).where(GuidanceClaimSetting.claim_id == claim_id))
    session.add_all(
        GuidanceClaimEvidence(claim_id=claim_id, passage_id=value, relation="supports")
        for value in passage_ids
    )
    session.add_all(
        GuidanceClaimSourceSetting(claim_id=claim_id, setting_id=value, relation="supports")
        for value in source_setting_ids
    )
    session.add_all(
        GuidanceClaimSetting(claim_id=claim_id, canonical_id=value) for value in canonical_ids
    )


def _validate_applicability_targets(
    session: Session,
    targets: Sequence[ApplicabilityTargetInput],
) -> tuple[dict[str, str], ...]:
    clean: list[dict[str, str]] = []
    identities: list[tuple[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for target in targets:
        target_type = target.target_type.strip().casefold()
        target_id = target.target_id.strip().casefold()
        evidence_basis = " ".join(target.evidence_basis.split())
        definition = _TARGET_DEFINITIONS.get(target_type)
        if definition is None:
            raise GuidanceClaimError(
                f"Applicability target type must be one of: {', '.join(TARGET_TYPES)}."
            )
        if not target_id:
            raise GuidanceClaimError("Every applicability target requires an ID.")
        model, _identity_column, _row_column, evidence_level = definition
        identity = session.get(model, target_id)
        if identity is None:
            raise GuidanceClaimError(
                f"Unknown {target_type.replace('_', ' ')} applicability target: {target_id}."
            )
        if not evidence_basis:
            raise GuidanceClaimError(
                "Every reviewed applicability target requires an evidence basis."
            )
        if len(evidence_basis) > config.MAX_GUIDANCE_REVIEW_NOTE_LENGTH:
            raise GuidanceClaimError("Applicability evidence basis is too long.")
        key = (target_type, target_id)
        if key in seen:
            continue
        seen.add(key)
        identities.append((target_type, identity))
        clean.append(
            {
                "target_type": target_type,
                "target_id": target_id,
                "evidence_level": evidence_level,
                "evidence_basis": evidence_basis,
            }
        )
    _validate_target_coherence(identities)
    return tuple(clean)


def _validate_bios_scopes(
    session: Session,
    scopes: Sequence[BiosScopeInput],
) -> tuple[dict[str, str | None], ...]:
    clean: list[dict[str, str | None]] = []
    seen: set[tuple[object, ...]] = set()
    for scope in scopes:
        context_type = scope.context_type.strip().casefold()
        context_id = scope.context_id.strip().casefold()
        if context_type not in BIOS_CONTEXT_TYPES:
            raise GuidanceClaimError(
                "BIOS scope context must be motherboard_model or vendor."
            )
        context_model = MotherboardModel if context_type == "motherboard_model" else Vendor
        if not context_id or session.get(context_model, context_id) is None:
            raise GuidanceClaimError(f"Unknown BIOS scope {context_type}: {context_id}.")
        versions = {
            "bios_version_exact": _clean_version(scope.bios_version_exact),
            "valid_from": _clean_version(scope.valid_from),
            "valid_until": _clean_version(scope.valid_until),
            "introduced_in": _clean_version(scope.introduced_in),
            "changed_in": _clean_version(scope.changed_in),
            "deprecated_in": _clean_version(scope.deprecated_in),
        }
        if not any(versions.values()):
            raise GuidanceClaimError("A BIOS scope requires at least one version marker.")
        if versions["bios_version_exact"] and (
            versions["valid_from"] or versions["valid_until"]
        ):
            raise GuidanceClaimError("Use either an exact BIOS version or a valid range.")
        comparison_mode = scope.comparison_mode.strip().casefold()
        if comparison_mode not in BIOS_COMPARISON_MODES:
            raise GuidanceClaimError(
                "BIOS comparison mode must be opaque, integer_numeric, or dotted_numeric."
            )
        if comparison_mode == "opaque" and (
            versions["valid_from"] or versions["valid_until"]
        ):
            raise GuidanceClaimError(
                "Opaque BIOS versions cannot use ranges; choose an explicit numeric mode."
            )
        if comparison_mode != "opaque":
            for value in versions.values():
                if value:
                    _version_key(value, comparison_mode)
            if versions["valid_from"] and versions["valid_until"]:
                if _version_key(versions["valid_from"], comparison_mode) > _version_key(
                    versions["valid_until"], comparison_mode
                ):
                    raise GuidanceClaimError(
                        "BIOS valid_from must not be later than valid_until."
                    )
        version_notes = _required_scope_text(scope.version_notes, "BIOS version notes")
        evidence_basis = _required_scope_text(
            scope.evidence_basis, "BIOS scope evidence basis"
        )
        value = {
            "context_type": context_type,
            "context_id": context_id,
            **versions,
            "comparison_mode": comparison_mode,
            "version_notes": version_notes,
            "evidence_basis": evidence_basis,
        }
        key = tuple(value.items())
        if key in seen:
            continue
        seen.add(key)
        clean.append(value)
    return tuple(clean)


def _clean_version(value: str) -> str | None:
    value = " ".join(value.split())
    if not value:
        return None
    if len(value) > 64:
        raise GuidanceClaimError("BIOS version identifiers must be 64 characters or fewer.")
    return value


def _validate_bios_scope_coherence(
    session: Session,
    targets: Sequence[dict[str, str]],
    scopes: Sequence[dict[str, str | None]],
) -> None:
    exact_models = {
        target["target_id"]
        for target in targets
        if target["target_type"] == TARGET_MOTHERBOARD_MODEL
    }
    target_vendors = {
        target["target_id"]
        for target in targets
        if target["target_type"] == TARGET_VENDOR
    }
    for model_id in exact_models:
        model = session.get(MotherboardModel, model_id)
        if model is None:
            raise RuntimeError("Validated motherboard applicability target disappeared.")
        target_vendors.add(model.vendor_id)
    for target in targets:
        if target["target_type"] != TARGET_MOTHERBOARD_FAMILY:
            continue
        family = session.get(MotherboardFamily, target["target_id"])
        if family is None:
            raise RuntimeError("Validated motherboard family target disappeared.")
        target_vendors.add(family.vendor_id)

    for scope in scopes:
        if exact_models and (
            scope["context_type"] != "motherboard_model"
            or scope["context_id"] not in exact_models
        ):
            raise GuidanceClaimError(
                "A claim with exact motherboard targets requires BIOS scopes for those "
                "exact motherboard identities."
            )
        if not target_vendors:
            continue
        scope_vendor = scope["context_id"]
        if scope["context_type"] == "motherboard_model":
            model = session.get(MotherboardModel, scope["context_id"])
            if model is None:
                raise RuntimeError("Validated BIOS motherboard context disappeared.")
            scope_vendor = model.vendor_id
        if scope_vendor not in target_vendors:
            raise GuidanceClaimError(
                "BIOS scope vendor conflicts with reviewed hardware applicability."
            )


def _required_scope_text(value: str, label: str) -> str:
    value = " ".join(value.split())
    if not value:
        raise GuidanceClaimError(f"{label} is required.")
    if len(value) > 1000:
        raise GuidanceClaimError(f"{label} must be 1000 characters or fewer.")
    return value


def _version_key(value: str, mode: str) -> tuple[int, ...]:
    if mode == "integer_numeric":
        if not re.fullmatch(r"\d+", value):
            raise GuidanceClaimError(
                f"BIOS version {value!r} is not an integer_numeric identifier."
            )
        return (int(value),)
    if mode == "dotted_numeric":
        if not re.fullmatch(r"\d+(?:\.\d+)+", value):
            raise GuidanceClaimError(
                f"BIOS version {value!r} is not a dotted_numeric identifier."
            )
        return tuple(int(part) for part in value.split("."))
    raise GuidanceClaimError("Opaque BIOS versions cannot be ordered.")


def _validate_target_coherence(identities: Sequence[tuple[str, object]]) -> None:
    platform_ids: set[str] = set()
    architecture_ids: set[str] = set()
    vendor_ids: set[str] = set()
    cpu_vendor_ids: set[str] = set()
    for target_type, identity in identities:
        if target_type == TARGET_PLATFORM:
            platform_ids.add(identity.platform_id)
        elif getattr(identity, "platform_id", None):
            platform_ids.add(identity.platform_id)
        if target_type == TARGET_CPU_ARCHITECTURE:
            architecture_ids.add(identity.architecture_id)
        elif getattr(identity, "architecture_id", None):
            architecture_ids.add(identity.architecture_id)
        if target_type == TARGET_CPU_VENDOR:
            cpu_vendor_ids.add(identity.vendor_id)
        elif target_type == TARGET_CPU_ARCHITECTURE:
            cpu_vendor_ids.add(identity.vendor_name.casefold())
        elif target_type in {TARGET_CPU_MODEL, TARGET_CPU_FAMILY, TARGET_PLATFORM}:
            platform_id = identity.platform_id
            if platform_id.startswith("amd-"):
                cpu_vendor_ids.add("amd")
            elif platform_id.startswith("intel-"):
                cpu_vendor_ids.add("intel")
        if target_type == TARGET_VENDOR:
            vendor_ids.add(identity.vendor_id)
        elif target_type in {TARGET_MOTHERBOARD_MODEL, TARGET_MOTHERBOARD_FAMILY}:
            vendor_ids.add(identity.vendor_id)
    if len(platform_ids) > 1:
        raise GuidanceClaimError(
            "Reviewed applicability targets span incompatible hardware platforms."
        )
    if len(architecture_ids) > 1:
        raise GuidanceClaimError(
            "Reviewed applicability targets span incompatible CPU architectures."
        )
    if len(vendor_ids) > 1:
        raise GuidanceClaimError(
            "Reviewed applicability targets span incompatible motherboard vendors."
        )
    if len(cpu_vendor_ids) > 1:
        raise GuidanceClaimError("Reviewed applicability targets span incompatible CPU vendors.")


def _replace_applicability_targets(
    session: Session,
    claim_id: int,
    targets: tuple[dict[str, str], ...],
) -> None:
    session.execute(
        delete(GuidanceClaimApplicabilityTarget).where(
            GuidanceClaimApplicabilityTarget.claim_id == claim_id
        )
    )
    rows = []
    for ordinal, target in enumerate(targets):
        _model, _identity_column, column, _evidence_level = _TARGET_DEFINITIONS[
            target["target_type"]
        ]
        values = {
            "claim_id": claim_id,
            "ordinal": ordinal,
            "cpu_model_id": None,
            "cpu_family_id": None,
            "cpu_architecture_id": None,
            "motherboard_model_id": None,
            "chipset_id": None,
            "chipset_family_id": None,
            "platform_id": None,
            "vendor_id": None,
            "cpu_vendor_id": None,
            "motherboard_family_id": None,
            "evidence_level": target["evidence_level"],
            "evidence_basis": target["evidence_basis"],
        }
        values[column] = target["target_id"]
        rows.append(GuidanceClaimApplicabilityTarget(**values))
    session.add_all(rows)


def _replace_bios_scopes(
    session: Session,
    claim_id: int,
    scopes: tuple[dict[str, str | None], ...],
) -> None:
    session.execute(
        delete(GuidanceClaimBiosScope).where(GuidanceClaimBiosScope.claim_id == claim_id)
    )
    rows = []
    for ordinal, scope in enumerate(scopes):
        rows.append(
            GuidanceClaimBiosScope(
                claim_id=claim_id,
                ordinal=ordinal,
                motherboard_model_id=(
                    scope["context_id"]
                    if scope["context_type"] == "motherboard_model"
                    else None
                ),
                vendor_id=(
                    scope["context_id"] if scope["context_type"] == "vendor" else None
                ),
                bios_version_exact=scope["bios_version_exact"],
                valid_from=scope["valid_from"],
                valid_until=scope["valid_until"],
                introduced_in=scope["introduced_in"],
                changed_in=scope["changed_in"],
                deprecated_in=scope["deprecated_in"],
                comparison_mode=scope["comparison_mode"],
                version_notes=scope["version_notes"],
                evidence_basis=scope["evidence_basis"],
            )
        )
    session.add_all(rows)


def _bios_scope_view(session: Session, row: GuidanceClaimBiosScope) -> GuidanceBiosScope:
    if row.motherboard_model_id:
        context_type = "motherboard_model"
        context_id = row.motherboard_model_id
        identity = session.get(MotherboardModel, context_id)
    else:
        context_type = "vendor"
        context_id = row.vendor_id or ""
        identity = session.get(Vendor, context_id)
    if identity is None:
        raise RuntimeError(f"Guidance BIOS scope {row.claim_id}/{row.ordinal} is orphaned.")
    return GuidanceBiosScope(
        context_type=context_type,
        context_id=context_id,
        context_name=identity.canonical_name,
        bios_version_exact=row.bios_version_exact,
        valid_from=row.valid_from,
        valid_until=row.valid_until,
        introduced_in=row.introduced_in,
        changed_in=row.changed_in,
        deprecated_in=row.deprecated_in,
        comparison_mode=row.comparison_mode,
        version_notes=row.version_notes,
        evidence_basis=row.evidence_basis,
    )


def _applicability_target_view(
    session: Session,
    row: GuidanceClaimApplicabilityTarget,
) -> GuidanceApplicabilityTarget:
    for target_type, (
        model,
        _identity_column,
        column,
        _evidence_level,
    ) in _TARGET_DEFINITIONS.items():
        target_id = getattr(row, column)
        if target_id is None:
            continue
        identity = session.get(model, target_id)
        if identity is None:
            raise RuntimeError(
                f"Guidance applicability target {row.claim_id}/{row.ordinal} is orphaned."
            )
        return GuidanceApplicabilityTarget(
            target_type=target_type,
            target_id=target_id,
            canonical_name=identity.canonical_name,
            evidence_level=row.evidence_level,
            evidence_basis=row.evidence_basis,
        )
    raise RuntimeError(
        f"Guidance applicability target {row.claim_id}/{row.ordinal} has no identity."
    )


def _claim_snapshot(claim: GuidanceClaim, values: dict[str, object]) -> dict[str, object]:
    return {
        "claim_text": claim.claim_text,
        "claim_type": claim.claim_type,
        "applicability_scope": claim.applicability_scope,
        "cpu_model_id": claim.cpu_model_id,
        "passage_ids": list(values["passage_ids"]),
        "source_setting_ids": list(values["source_setting_ids"]),
        "canonical_ids": list(values["canonical_ids"]),
        "applicability_targets": list(values["applicability_targets"]),
        "bios_scopes": list(values["bios_scopes"]),
    }


def _detail_snapshot(detail: GuidanceClaimDetail) -> dict[str, object]:
    return {
        "claim_text": detail.claim.claim_text,
        "claim_type": detail.claim.claim_type,
        "applicability_scope": detail.claim.applicability_scope,
        "cpu_model_id": detail.claim.cpu_model_id,
        "passage_ids": [citation.passage.id for citation in detail.evidence],
        "source_setting_ids": [citation.setting.id for citation in detail.source_settings],
        "canonical_ids": [setting.canonical_id for setting in detail.settings],
        "applicability_targets": [
            {
                "target_type": target.target_type,
                "target_id": target.target_id,
                "evidence_level": target.evidence_level,
                "evidence_basis": target.evidence_basis,
            }
            for target in detail.applicability_targets
        ],
        "bios_scopes": [
            {
                "context_type": scope.context_type,
                "context_id": scope.context_id,
                "bios_version_exact": scope.bios_version_exact,
                "valid_from": scope.valid_from,
                "valid_until": scope.valid_until,
                "introduced_in": scope.introduced_in,
                "changed_in": scope.changed_in,
                "deprecated_in": scope.deprecated_in,
                "comparison_mode": scope.comparison_mode,
                "version_notes": scope.version_notes,
                "evidence_basis": scope.evidence_basis,
            }
            for scope in detail.bios_scopes
        ],
    }
