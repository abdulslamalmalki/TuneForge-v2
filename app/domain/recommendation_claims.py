"""Reviewer-controlled recommendation claims with strict evidence and scope gates."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import config
from app.auth.context import current_actor
from app.db.models import (
    CanonicalSetting,
    EvidencePassage,
    EvidenceSource,
    Manual,
    RecommendationClaim,
    RecommendationClaimApplicabilityTarget,
    RecommendationClaimBiosScope,
    RecommendationClaimEvent,
    RecommendationClaimEvidence,
    RecommendationClaimSourceSetting,
    Setting,
    SettingCanonicalMapping,
)
from app.domain import canonical_settings, canonicalization, guidance_claims
from app.domain.extraction_runs import utc_now_iso
from app.domain.guidance_claims import (
    ApplicabilityTargetInput,
    BiosScopeInput,
    GuidanceApplicabilityTarget,
    GuidanceBiosScope,
    GuidanceCitation,
    GuidanceSourceSettingCitation,
)

STATUS_DRAFT = "draft"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUSES = (STATUS_DRAFT, STATUS_APPROVED, STATUS_REJECTED)

ACTIONS = (
    "enable",
    "disable",
    "set_value",
    "set_range",
    "leave_auto",
    "avoid",
    "informational",
)
VALUE_NONE = "none"
VALUE_EXACT = "exact"
VALUE_RANGE = "range"
VALUE_MODES = (VALUE_NONE, VALUE_EXACT, VALUE_RANGE)
USE_CASES = (
    "gaming",
    "productivity",
    "efficiency",
    "quiet_operation",
    "overclocking",
    "undervolting",
    "memory_tuning",
    "stability_troubleshooting",
)
RISK_LEVELS = ("informational", "low", "moderate", "advanced", "high")
CONFIDENCE_LEVELS = ("insufficient", "moderate", "high", "very_high")
APPROVABLE_CONFIDENCE_LEVELS = ("high", "very_high")
CONFLICT_STATES = ("not_assessed", "none", "potential", "confirmed", "resolved")
APPROVABLE_CONFLICT_STATES = ("none", "resolved")

_TARGET_COLUMN = {
    guidance_claims.TARGET_CPU_MODEL: "cpu_model_id",
    guidance_claims.TARGET_CPU_FAMILY: "cpu_family_id",
    guidance_claims.TARGET_CPU_ARCHITECTURE: "cpu_architecture_id",
    guidance_claims.TARGET_MOTHERBOARD_MODEL: "motherboard_model_id",
    guidance_claims.TARGET_CHIPSET: "chipset_id",
    guidance_claims.TARGET_CHIPSET_FAMILY: "chipset_family_id",
    guidance_claims.TARGET_PLATFORM: "platform_id",
    guidance_claims.TARGET_VENDOR: "vendor_id",
    guidance_claims.TARGET_CPU_VENDOR: "cpu_vendor_id",
    guidance_claims.TARGET_MOTHERBOARD_FAMILY: "motherboard_family_id",
}


class RecommendationClaimError(ValueError):
    @property
    def message(self) -> str:
        return str(self)


@dataclass(frozen=True)
class RecommendationClaimOverview:
    claim: RecommendationClaim
    canonical_name: str
    evidence_count: int
    hardware_target_count: int
    bios_scope_count: int


@dataclass(frozen=True)
class RecommendationClaimDetail:
    claim: RecommendationClaim
    canonical_setting: CanonicalSetting
    evidence: tuple[GuidanceCitation, ...]
    source_settings: tuple[GuidanceSourceSettingCitation, ...]
    applicability_targets: tuple[GuidanceApplicabilityTarget, ...]
    bios_scopes: tuple[GuidanceBiosScope, ...]
    prerequisites: tuple[str, ...]
    events: tuple[RecommendationClaimEvent, ...]


def create_claim(
    session: Session,
    *,
    canonical_id: str,
    recommendation_text: str,
    recommended_action: str,
    exact_value: str = "",
    minimum_value: str = "",
    maximum_value: str = "",
    value_unit: str = "",
    use_case: str,
    risk_level: str,
    prerequisites: Sequence[str] = (),
    confidence_level: str,
    conflict_state: str = "not_assessed",
    passage_ids: Sequence[int] = (),
    source_setting_ids: Sequence[int] = (),
    applicability_targets: Sequence[ApplicabilityTargetInput] = (),
    bios_scopes: Sequence[BiosScopeInput] = (),
    actor: str | None = None,
    commit: bool = True,
) -> RecommendationClaim:
    values = _validate_values(
        session,
        canonical_id=canonical_id,
        recommendation_text=recommendation_text,
        recommended_action=recommended_action,
        exact_value=exact_value,
        minimum_value=minimum_value,
        maximum_value=maximum_value,
        value_unit=value_unit,
        use_case=use_case,
        risk_level=risk_level,
        prerequisites=prerequisites,
        confidence_level=confidence_level,
        conflict_state=conflict_state,
        passage_ids=passage_ids,
        source_setting_ids=source_setting_ids,
        applicability_targets=applicability_targets,
        bios_scopes=bios_scopes,
    )
    duplicate = session.scalar(
        select(RecommendationClaim.id).where(
            RecommendationClaim.canonical_id == values["canonical_id"],
            RecommendationClaim.use_case == values["use_case"],
            func.lower(RecommendationClaim.recommendation_text)
            == values["recommendation_text"].lower(),
        )
    )
    if duplicate is not None:
        raise RecommendationClaimError(
            f"An equivalent recommendation already exists as claim {duplicate}."
        )
    now = utc_now_iso()
    claim = RecommendationClaim(
        canonical_id=values["canonical_id"],
        recommendation_text=values["recommendation_text"],
        recommended_action=values["recommended_action"],
        value_mode=values["value_mode"],
        exact_value=values["exact_value"],
        minimum_value=values["minimum_value"],
        maximum_value=values["maximum_value"],
        value_unit=values["value_unit"],
        use_case=values["use_case"],
        risk_level=values["risk_level"],
        prerequisites_json=json.dumps(values["prerequisites"]),
        confidence_level=values["confidence_level"],
        conflict_state=values["conflict_state"],
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
        applicability_targets=values["applicability_targets"],
        bios_scopes=values["bios_scopes"],
    )
    session.add(
        RecommendationClaimEvent(
            claim_id=claim.id,
            action="candidate_created",
            actor=actor or current_actor(),
            detail=json.dumps(_snapshot(values), sort_keys=True),
            created_at=now,
        )
    )
    if commit:
        session.commit()
        session.refresh(claim)
    else:
        session.flush()
    return claim


def list_claims(session: Session, *, status: str = "") -> list[RecommendationClaimOverview]:
    status = status.strip().casefold()
    if status and status not in STATUSES:
        raise RecommendationClaimError("Status filter must be draft, approved, or rejected.")
    evidence_counts = (
        select(RecommendationClaimEvidence.claim_id, func.count().label("count"))
        .group_by(RecommendationClaimEvidence.claim_id)
        .subquery()
    )
    source_counts = (
        select(RecommendationClaimSourceSetting.claim_id, func.count().label("count"))
        .group_by(RecommendationClaimSourceSetting.claim_id)
        .subquery()
    )
    target_counts = (
        select(RecommendationClaimApplicabilityTarget.claim_id, func.count().label("count"))
        .group_by(RecommendationClaimApplicabilityTarget.claim_id)
        .subquery()
    )
    bios_counts = (
        select(RecommendationClaimBiosScope.claim_id, func.count().label("count"))
        .group_by(RecommendationClaimBiosScope.claim_id)
        .subquery()
    )
    query = (
        select(
            RecommendationClaim,
            CanonicalSetting.canonical_name,
            func.coalesce(evidence_counts.c.count, 0),
            func.coalesce(source_counts.c.count, 0),
            func.coalesce(target_counts.c.count, 0),
            func.coalesce(bios_counts.c.count, 0),
        )
        .join(CanonicalSetting, CanonicalSetting.canonical_id == RecommendationClaim.canonical_id)
        .outerjoin(evidence_counts, evidence_counts.c.claim_id == RecommendationClaim.id)
        .outerjoin(source_counts, source_counts.c.claim_id == RecommendationClaim.id)
        .outerjoin(target_counts, target_counts.c.claim_id == RecommendationClaim.id)
        .outerjoin(bios_counts, bios_counts.c.claim_id == RecommendationClaim.id)
        .order_by(RecommendationClaim.id)
    )
    if status:
        query = query.where(RecommendationClaim.status == status)
    return [
        RecommendationClaimOverview(
            claim=claim,
            canonical_name=canonical_name,
            evidence_count=evidence_count + source_count,
            hardware_target_count=target_count,
            bios_scope_count=bios_count,
        )
        for (
            claim,
            canonical_name,
            evidence_count,
            source_count,
            target_count,
            bios_count,
        ) in session.execute(query)
    ]


def status_counts(session: Session) -> dict[str, int]:
    counts = dict(
        session.execute(
            select(RecommendationClaim.status, func.count()).group_by(RecommendationClaim.status)
        ).all()
    )
    return {status: counts.get(status, 0) for status in STATUSES}


def get_claim(session: Session, claim_id: int) -> RecommendationClaim | None:
    return session.get(RecommendationClaim, claim_id)


def get_detail(session: Session, claim_id: int) -> RecommendationClaimDetail | None:
    claim = session.get(RecommendationClaim, claim_id)
    if claim is None:
        return None
    canonical = session.get(CanonicalSetting, claim.canonical_id)
    if canonical is None:
        raise RuntimeError("Recommendation references a missing canonical setting.")
    evidence = tuple(
        GuidanceCitation(passage=passage, source=source)
        for passage, source in session.execute(
            select(EvidencePassage, EvidenceSource)
            .join(
                RecommendationClaimEvidence,
                RecommendationClaimEvidence.passage_id == EvidencePassage.id,
            )
            .join(EvidenceSource, EvidenceSource.id == EvidencePassage.source_id)
            .where(RecommendationClaimEvidence.claim_id == claim_id)
            .order_by(EvidenceSource.id, EvidencePassage.page_number, EvidencePassage.ordinal)
        )
    )
    source_settings = tuple(
        GuidanceSourceSettingCitation(setting=setting, manual=manual)
        for setting, manual in session.execute(
            select(Setting, Manual)
            .join(
                RecommendationClaimSourceSetting,
                RecommendationClaimSourceSetting.setting_id == Setting.id,
            )
            .join(Manual, Manual.id == Setting.manual_id)
            .where(RecommendationClaimSourceSetting.claim_id == claim_id)
            .order_by(Manual.id, Setting.page_number, Setting.id)
        )
    )
    targets = tuple(
        guidance_claims.applicability_target_view(session, row)
        for row in session.scalars(
            select(RecommendationClaimApplicabilityTarget)
            .where(RecommendationClaimApplicabilityTarget.claim_id == claim_id)
            .order_by(RecommendationClaimApplicabilityTarget.ordinal)
        )
    )
    bios_scopes = tuple(
        guidance_claims.bios_scope_view(session, row)
        for row in session.scalars(
            select(RecommendationClaimBiosScope)
            .where(RecommendationClaimBiosScope.claim_id == claim_id)
            .order_by(RecommendationClaimBiosScope.ordinal)
        )
    )
    events = tuple(
        session.scalars(
            select(RecommendationClaimEvent)
            .where(RecommendationClaimEvent.claim_id == claim_id)
            .order_by(RecommendationClaimEvent.id)
        )
    )
    return RecommendationClaimDetail(
        claim=claim,
        canonical_setting=canonical,
        evidence=evidence,
        source_settings=source_settings,
        applicability_targets=targets,
        bios_scopes=bios_scopes,
        prerequisites=tuple(json.loads(claim.prerequisites_json)),
        events=events,
    )


def edit_claim(
    session: Session,
    claim: RecommendationClaim,
    **raw,
) -> bool:
    if claim.status != STATUS_DRAFT:
        raise RecommendationClaimError("Only draft recommendation claims can be edited.")
    values = _validate_values(session, **raw)
    duplicate = session.scalar(
        select(RecommendationClaim.id).where(
            RecommendationClaim.id != claim.id,
            RecommendationClaim.canonical_id == values["canonical_id"],
            RecommendationClaim.use_case == values["use_case"],
            func.lower(RecommendationClaim.recommendation_text)
            == values["recommendation_text"].lower(),
        )
    )
    if duplicate is not None:
        raise RecommendationClaimError(
            f"An equivalent recommendation already exists as claim {duplicate}."
        )
    detail = get_detail(session, claim.id)
    if detail is None:
        raise RuntimeError("Recommendation disappeared during edit.")
    before = _detail_snapshot(detail)
    after = _snapshot(values)
    if before == after:
        return False
    claim.canonical_id = values["canonical_id"]
    claim.recommendation_text = values["recommendation_text"]
    claim.recommended_action = values["recommended_action"]
    claim.value_mode = values["value_mode"]
    claim.exact_value = values["exact_value"]
    claim.minimum_value = values["minimum_value"]
    claim.maximum_value = values["maximum_value"]
    claim.value_unit = values["value_unit"]
    claim.use_case = values["use_case"]
    claim.risk_level = values["risk_level"]
    claim.prerequisites_json = json.dumps(values["prerequisites"])
    claim.confidence_level = values["confidence_level"]
    claim.conflict_state = values["conflict_state"]
    now = utc_now_iso()
    claim.updated_at = now
    _replace_links(
        session,
        claim.id,
        passage_ids=values["passage_ids"],
        source_setting_ids=values["source_setting_ids"],
        applicability_targets=values["applicability_targets"],
        bios_scopes=values["bios_scopes"],
        clear_existing=True,
    )
    session.add(
        RecommendationClaimEvent(
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
    claim: RecommendationClaim,
    *,
    decision: str,
    note: str,
) -> RecommendationClaim:
    if claim.status != STATUS_DRAFT:
        raise RecommendationClaimError("Only draft recommendation claims can be reviewed.")
    decision = decision.strip().casefold()
    if decision not in {STATUS_APPROVED, STATUS_REJECTED}:
        raise RecommendationClaimError("Decision must be approved or rejected.")
    note = " ".join(note.split())
    if not note:
        raise RecommendationClaimError("Record a reviewer note for this decision.")
    if len(note) > config.MAX_GUIDANCE_REVIEW_NOTE_LENGTH:
        raise RecommendationClaimError("Reviewer note is too long.")
    detail = get_detail(session, claim.id)
    if detail is None:
        raise RuntimeError("Recommendation disappeared during review.")
    if decision == STATUS_APPROVED:
        _validate_approval(detail)
    now = utc_now_iso()
    claim.status = decision
    claim.review_note = note
    claim.reviewer = current_actor()
    claim.reviewed_at = now
    claim.updated_at = now
    session.add(
        RecommendationClaimEvent(
            claim_id=claim.id,
            action=f"claim_{decision}",
            actor=claim.reviewer,
            detail=json.dumps({"note": note}, sort_keys=True),
            created_at=now,
        )
    )
    session.commit()
    session.refresh(claim)
    return claim


def _validate_values(
    session: Session,
    **raw,
) -> dict[str, object]:
    canonical_id = str(raw["canonical_id"]).strip()
    canonical = session.get(CanonicalSetting, canonical_id)
    if canonical is None or canonical.status != canonical_settings.IDENTITY_ACTIVE:
        raise RecommendationClaimError("Choose one active canonical setting.")
    recommendation_text = " ".join(str(raw["recommendation_text"]).split())
    if not recommendation_text:
        raise RecommendationClaimError("Recommendation text is required.")
    if len(recommendation_text) > config.MAX_GUIDANCE_CLAIM_CHARS:
        raise RecommendationClaimError("Recommendation text is too long.")
    action = str(raw["recommended_action"]).strip().casefold()
    if action not in ACTIONS:
        raise RecommendationClaimError(f"Recommended action must be one of: {', '.join(ACTIONS)}.")
    exact = _optional_value(raw["exact_value"], "Exact value")
    minimum = _optional_value(raw["minimum_value"], "Minimum value")
    maximum = _optional_value(raw["maximum_value"], "Maximum value")
    unit = _optional_value(raw["value_unit"], "Value unit", maximum_length=50)
    if action == "set_value":
        if not exact or minimum or maximum:
            raise RecommendationClaimError("set_value requires one exact value and no range.")
        value_mode = VALUE_EXACT
    elif action == "set_range":
        if exact or not minimum or not maximum or minimum == maximum:
            raise RecommendationClaimError(
                "set_range requires distinct minimum and maximum values and no exact value."
            )
        value_mode = VALUE_RANGE
    else:
        if exact or minimum or maximum or unit:
            raise RecommendationClaimError(
                "Only set_value or set_range recommendations may carry values or units."
            )
        value_mode = VALUE_NONE
    use_case = str(raw["use_case"]).strip().casefold()
    risk = str(raw["risk_level"]).strip().casefold()
    confidence = str(raw["confidence_level"]).strip().casefold()
    conflict = str(raw["conflict_state"]).strip().casefold()
    if use_case not in USE_CASES:
        raise RecommendationClaimError(f"Use case must be one of: {', '.join(USE_CASES)}.")
    if risk not in RISK_LEVELS:
        raise RecommendationClaimError(f"Risk level must be one of: {', '.join(RISK_LEVELS)}.")
    if confidence not in CONFIDENCE_LEVELS:
        raise RecommendationClaimError(
            f"Confidence level must be one of: {', '.join(CONFIDENCE_LEVELS)}."
        )
    if conflict not in CONFLICT_STATES:
        raise RecommendationClaimError(
            f"Conflict state must be one of: {', '.join(CONFLICT_STATES)}."
        )
    prerequisites = tuple(
        dict.fromkeys(
            " ".join(str(item).split())
            for item in raw["prerequisites"]
            if str(item).strip()
        )
    )
    if len(prerequisites) > 20 or any(len(item) > 300 for item in prerequisites):
        raise RecommendationClaimError(
            "Use at most 20 prerequisites, each 300 characters or fewer."
        )
    targets = guidance_claims.validate_applicability_targets(
        session, raw["applicability_targets"]
    )
    if not targets:
        raise RecommendationClaimError(
            "Recommendation candidates require at least one evidence-backed hardware target."
        )
    bios_scopes = guidance_claims.validate_bios_scopes(session, raw["bios_scopes"])
    guidance_claims.validate_bios_scope_coherence(session, targets, bios_scopes)
    passage_ids, source_setting_ids = _validate_evidence(
        session,
        canonical_id,
        raw["passage_ids"],
        raw["source_setting_ids"],
    )
    return {
        "canonical_id": canonical_id,
        "recommendation_text": recommendation_text,
        "recommended_action": action,
        "value_mode": value_mode,
        "exact_value": exact,
        "minimum_value": minimum,
        "maximum_value": maximum,
        "value_unit": unit,
        "use_case": use_case,
        "risk_level": risk,
        "prerequisites": prerequisites,
        "confidence_level": confidence,
        "conflict_state": conflict,
        "passage_ids": passage_ids,
        "source_setting_ids": source_setting_ids,
        "applicability_targets": targets,
        "bios_scopes": bios_scopes,
    }


def _validate_evidence(
    session: Session,
    canonical_id: str,
    passage_ids: Sequence[int],
    source_setting_ids: Sequence[int],
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    passages = tuple(sorted(dict.fromkeys(int(value) for value in passage_ids)))
    passage_rows = session.execute(
        select(EvidencePassage.id, EvidenceSource.status)
        .join(EvidenceSource, EvidenceSource.id == EvidencePassage.source_id)
        .where(EvidencePassage.id.in_(passages))
    ).all()
    if len(passage_rows) != len(passages) or any(
        status != "approved" for _, status in passage_rows
    ):
        raise RecommendationClaimError("Recommendation evidence passages must be approved.")
    setting_ids = tuple(sorted(dict.fromkeys(int(value) for value in source_setting_ids)))
    setting_rows = list(
        session.execute(
            select(Setting, Manual, SettingCanonicalMapping.canonical_id)
            .join(Manual, Manual.id == Setting.manual_id)
            .outerjoin(
                SettingCanonicalMapping,
                SettingCanonicalMapping.setting_id == Setting.id,
            )
            .where(Setting.id.in_(setting_ids))
        )
    )
    if len(setting_rows) != len(setting_ids):
        raise RecommendationClaimError("One or more recommendation source settings were not found.")
    if not passages and not setting_ids:
        raise RecommendationClaimError("Select at least one approved evidence citation.")
    for setting, manual, mapped_id in setting_rows:
        if canonicalization.source_trust_reasons(setting, manual):
            raise RecommendationClaimError(
                "Recommendation source settings must remain trusted and approved."
            )
        if mapped_id != canonical_id:
            raise RecommendationClaimError(
                "Every source setting must map to the recommendation's canonical setting."
            )
    return passages, setting_ids


def _replace_links(
    session: Session,
    claim_id: int,
    *,
    passage_ids: Sequence[int],
    source_setting_ids: Sequence[int],
    applicability_targets: Sequence[dict[str, str]],
    bios_scopes: Sequence[dict[str, str | None]],
    clear_existing: bool = False,
) -> None:
    if clear_existing:
        session.execute(
            delete(RecommendationClaimEvidence).where(
                RecommendationClaimEvidence.claim_id == claim_id
            )
        )
        session.execute(
            delete(RecommendationClaimSourceSetting).where(
                RecommendationClaimSourceSetting.claim_id == claim_id
            )
        )
        session.execute(
            delete(RecommendationClaimApplicabilityTarget).where(
                RecommendationClaimApplicabilityTarget.claim_id == claim_id
            )
        )
        session.execute(
            delete(RecommendationClaimBiosScope).where(
                RecommendationClaimBiosScope.claim_id == claim_id
            )
        )
    session.add_all(
        RecommendationClaimEvidence(claim_id=claim_id, passage_id=value, relation="supports")
        for value in passage_ids
    )
    session.add_all(
        RecommendationClaimSourceSetting(
            claim_id=claim_id, setting_id=value, relation="supports"
        )
        for value in source_setting_ids
    )
    for ordinal, target in enumerate(applicability_targets):
        values = {column: None for column in _TARGET_COLUMN.values()}
        values[_TARGET_COLUMN[target["target_type"]]] = target["target_id"]
        session.add(
            RecommendationClaimApplicabilityTarget(
                claim_id=claim_id,
                ordinal=ordinal,
                evidence_level=target["evidence_level"],
                evidence_basis=target["evidence_basis"],
                **values,
            )
        )
    for ordinal, scope in enumerate(bios_scopes):
        session.add(
            RecommendationClaimBiosScope(
                claim_id=claim_id,
                ordinal=ordinal,
                motherboard_model_id=(
                    scope["context_id"]
                    if scope["context_type"] == "motherboard_model"
                    else None
                ),
                vendor_id=(scope["context_id"] if scope["context_type"] == "vendor" else None),
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


def _validate_approval(detail: RecommendationClaimDetail) -> None:
    claim = detail.claim
    if not detail.evidence and not detail.source_settings:
        raise RecommendationClaimError("An approved recommendation requires evidence.")
    if not detail.applicability_targets:
        raise RecommendationClaimError("An approved recommendation requires hardware scope.")
    if claim.confidence_level not in APPROVABLE_CONFIDENCE_LEVELS:
        raise RecommendationClaimError(
            "Only high or very-high confidence recommendations may be approved."
        )
    if claim.conflict_state not in APPROVABLE_CONFLICT_STATES:
        raise RecommendationClaimError(
            "Resolve conflict assessment before approving a recommendation."
        )
    if any(citation.source.status != "approved" for citation in detail.evidence):
        raise RecommendationClaimError("All recommendation evidence sources must be approved.")
    for citation in detail.source_settings:
        if canonicalization.source_trust_reasons(citation.setting, citation.manual):
            raise RecommendationClaimError(
                "All recommendation source settings must remain trusted and approved."
            )


def _optional_value(value: object, label: str, *, maximum_length: int = 200) -> str | None:
    cleaned = " ".join(str(value or "").split())
    if len(cleaned) > maximum_length:
        raise RecommendationClaimError(f"{label} must be {maximum_length} characters or fewer.")
    return cleaned or None


def _snapshot(values: dict[str, object]) -> dict[str, object]:
    return {
        key: list(value) if isinstance(value, tuple) else value
        for key, value in values.items()
    }


def _detail_snapshot(detail: RecommendationClaimDetail) -> dict[str, object]:
    claim = detail.claim
    return {
        "canonical_id": claim.canonical_id,
        "recommendation_text": claim.recommendation_text,
        "recommended_action": claim.recommended_action,
        "value_mode": claim.value_mode,
        "exact_value": claim.exact_value,
        "minimum_value": claim.minimum_value,
        "maximum_value": claim.maximum_value,
        "value_unit": claim.value_unit,
        "use_case": claim.use_case,
        "risk_level": claim.risk_level,
        "prerequisites": list(detail.prerequisites),
        "confidence_level": claim.confidence_level,
        "conflict_state": claim.conflict_state,
        "passage_ids": [item.passage.id for item in detail.evidence],
        "source_setting_ids": [item.setting.id for item in detail.source_settings],
        "applicability_targets": [
            {
                "target_type": item.target_type,
                "target_id": item.target_id,
                "evidence_level": item.evidence_level,
                "evidence_basis": item.evidence_basis,
            }
            for item in detail.applicability_targets
        ],
        "bios_scopes": [
            {
                "context_type": item.context_type,
                "context_id": item.context_id,
                "bios_version_exact": item.bios_version_exact,
                "valid_from": item.valid_from,
                "valid_until": item.valid_until,
                "introduced_in": item.introduced_in,
                "changed_in": item.changed_in,
                "deprecated_in": item.deprecated_in,
                "comparison_mode": item.comparison_mode,
                "version_notes": item.version_notes,
                "evidence_basis": item.evidence_basis,
            }
            for item in detail.bios_scopes
        ],
    }
