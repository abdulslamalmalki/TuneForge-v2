"""Conservative detection and human resolution of approved-claim disagreements."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from itertools import combinations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.context import current_actor
from app.db.models import (
    EvidencePassage,
    EvidenceSource,
    GuidanceClaim,
    GuidanceClaimConflict,
    GuidanceClaimConflictEvent,
    GuidanceClaimEvidence,
    GuidanceClaimSetting,
    GuidanceClaimSourceSetting,
    MotherboardModel,
)
from app.domain import guidance_claims
from app.domain.errors import GuidanceClaimError
from app.domain.extraction_runs import utc_now_iso
from app.normalization.canonical_settings import normalize_alias

STATUS_PENDING = "pending"
STATUS_CONFIRMED = "confirmed"
STATUS_RESOLVED_SCOPE = "resolved_scope"
STATUS_RESOLVED_VERSION = "resolved_version"
STATUS_DISMISSED = "dismissed"
STATUSES = (
    STATUS_PENDING,
    STATUS_CONFIRMED,
    STATUS_RESOLVED_SCOPE,
    STATUS_RESOLVED_VERSION,
    STATUS_DISMISSED,
)
PUBLIC_STATUSES = (STATUS_CONFIRMED, STATUS_RESOLVED_SCOPE, STATUS_RESOLVED_VERSION)

HARDWARE_OVERLAP = "overlap"
HARDWARE_DISJOINT = "disjoint"
HARDWARE_UNKNOWN = "unknown"
BIOS_OVERLAP = "overlap"
BIOS_VERSION_DIFFERENCE = "version_difference"
BIOS_DISJOINT_CONTEXT = "disjoint_context"
BIOS_UNKNOWN = "unknown"

_NEGATION = re.compile(r"\b(no|not|never|cannot|can't|doesn't|disabled?|disallow(?:ed)?)\b", re.I)
_NEGATION_TOKENS = {
    "no",
    "not",
    "never",
    "cannot",
    "cant",
    "doesnt",
    "disable",
    "disabled",
    "disallow",
    "disallowed",
}


@dataclass(frozen=True)
class ConflictOverview:
    conflict: GuidanceClaimConflict
    left_claim: GuidanceClaim
    right_claim: GuidanceClaim
    canonical_ids: tuple[str, ...]


@dataclass(frozen=True)
class ConflictDetail:
    conflict: GuidanceClaimConflict
    left_claim: GuidanceClaim
    right_claim: GuidanceClaim
    canonical_ids: tuple[str, ...]
    left_hardware_targets: tuple[guidance_claims.GuidanceApplicabilityTarget, ...]
    right_hardware_targets: tuple[guidance_claims.GuidanceApplicabilityTarget, ...]
    left_bios_scopes: tuple[guidance_claims.GuidanceBiosScope, ...]
    right_bios_scopes: tuple[guidance_claims.GuidanceBiosScope, ...]
    events: tuple[GuidanceClaimConflictEvent, ...]


@dataclass(frozen=True)
class ConflictScanReport:
    compared_pairs: int
    detected_pairs: int
    existing_pairs: int


def scan_approved_claims(
    session: Session,
    *,
    actor: str | None = None,
) -> ConflictScanReport:
    """Create pending candidates only for conservative polarity disagreements."""
    actor = actor or current_actor()
    rows = session.execute(
        select(GuidanceClaimSetting.canonical_id, GuidanceClaim)
        .join(GuidanceClaim, GuidanceClaim.id == GuidanceClaimSetting.claim_id)
        .where(GuidanceClaim.status == guidance_claims.STATUS_APPROVED)
        .order_by(GuidanceClaimSetting.canonical_id, GuidanceClaim.id)
    )
    by_canonical: dict[str, list[GuidanceClaim]] = {}
    for canonical_id, claim in rows:
        by_canonical.setdefault(canonical_id, []).append(claim)

    existing = {
        (row.left_claim_id, row.right_claim_id)
        for row in session.scalars(select(GuidanceClaimConflict))
    }
    compared = detected = existing_count = 0
    seen_pairs: set[tuple[int, int]] = set()
    for claims in by_canonical.values():
        for left, right in combinations(claims, 2):
            pair = tuple(sorted((left.id, right.id)))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            if left.claim_type != right.claim_type:
                continue
            compared += 1
            reason = _polarity_conflict_reason(left, right)
            if reason is None:
                continue
            if pair in existing:
                existing_count += 1
                continue
            register_conflict(
                session,
                left_claim_id=pair[0],
                right_claim_id=pair[1],
                detection_reason=reason,
                actor=actor,
                commit=False,
            )
            detected += 1
    session.commit()
    return ConflictScanReport(compared, detected, existing_count)


def register_conflict(
    session: Session,
    *,
    left_claim_id: int,
    right_claim_id: int,
    detection_reason: str,
    actor: str | None = None,
    commit: bool = True,
) -> GuidanceClaimConflict:
    actor = actor or current_actor()
    left_id, right_id = sorted((int(left_claim_id), int(right_claim_id)))
    if left_id == right_id:
        raise GuidanceClaimError("A claim cannot conflict with itself.")
    left = session.get(GuidanceClaim, left_id)
    right = session.get(GuidanceClaim, right_id)
    if left is None or right is None:
        raise GuidanceClaimError("Both conflict claims must exist.")
    if (
        left.status != guidance_claims.STATUS_APPROVED
        or right.status != guidance_claims.STATUS_APPROVED
    ):
        raise GuidanceClaimError("Knowledge conflicts can reference approved claims only.")
    shared = _canonical_ids(session, left_id) & _canonical_ids(session, right_id)
    if not shared:
        raise GuidanceClaimError("Conflict claims must share a canonical setting.")
    reason = _required_text(detection_reason, "Conflict detection reason", maximum=1000)
    existing = session.scalar(
        select(GuidanceClaimConflict).where(
            GuidanceClaimConflict.left_claim_id == left_id,
            GuidanceClaimConflict.right_claim_id == right_id,
        )
    )
    if existing is not None:
        return existing

    left_targets = guidance_claims.list_applicability_targets(session, left_id)
    right_targets = guidance_claims.list_applicability_targets(session, right_id)
    left_scopes = guidance_claims.list_bios_scopes(session, left_id)
    right_scopes = guidance_claims.list_bios_scopes(session, right_id)
    now = utc_now_iso()
    conflict = GuidanceClaimConflict(
        left_claim_id=left_id,
        right_claim_id=right_id,
        detection_reason=reason,
        hardware_relationship=_hardware_relationship(left_targets, right_targets),
        bios_relationship=_bios_relationship(session, left_scopes, right_scopes),
        left_evidence_strength=_evidence_strength(session, left_id),
        right_evidence_strength=_evidence_strength(session, right_id),
        status=STATUS_PENDING,
        reviewer_note=None,
        reviewer=None,
        reviewed_at=None,
        created_at=now,
        updated_at=now,
    )
    session.add(conflict)
    session.flush()
    session.add(
        GuidanceClaimConflictEvent(
            conflict_id=conflict.id,
            action="conflict_detected",
            actor=actor,
            detail=json.dumps(_snapshot(conflict), sort_keys=True),
            created_at=now,
        )
    )
    if commit:
        session.commit()
    else:
        session.flush()
    return conflict


def review_conflict(
    session: Session,
    conflict: GuidanceClaimConflict,
    *,
    decision: str,
    note: str,
    actor: str | None = None,
) -> None:
    actor = actor or current_actor()
    decision = decision.strip().casefold()
    if conflict.status != STATUS_PENDING:
        raise GuidanceClaimError("Only pending knowledge conflicts can be reviewed.")
    if decision not in STATUSES[1:]:
        raise GuidanceClaimError(
            "Decision must be confirmed, resolved_scope, resolved_version, or dismissed."
        )
    note = _required_text(note, "Conflict reviewer note", maximum=2000)
    if decision == STATUS_RESOLVED_SCOPE and conflict.hardware_relationship != HARDWARE_DISJOINT:
        raise GuidanceClaimError(
            "Scope resolution requires reviewed disjoint hardware targets."
        )
    if decision == STATUS_RESOLVED_VERSION and conflict.bios_relationship not in {
        BIOS_VERSION_DIFFERENCE,
        BIOS_DISJOINT_CONTEXT,
    }:
        raise GuidanceClaimError(
            "Version resolution requires a reviewed BIOS version or context difference."
        )
    now = utc_now_iso()
    before = _snapshot(conflict)
    conflict.status = decision
    conflict.reviewer_note = note
    conflict.reviewer = actor
    conflict.reviewed_at = now
    conflict.updated_at = now
    session.add(
        GuidanceClaimConflictEvent(
            conflict_id=conflict.id,
            action=f"conflict_{decision}",
            actor=actor,
            detail=json.dumps({"before": before, "after": _snapshot(conflict)}, sort_keys=True),
            created_at=now,
        )
    )
    session.commit()


def list_conflicts(session: Session, *, status: str = "") -> list[ConflictOverview]:
    status = status.strip().casefold()
    if status and status not in STATUSES:
        raise GuidanceClaimError("Unknown conflict status filter.")
    statement = select(GuidanceClaimConflict).order_by(
        GuidanceClaimConflict.status, GuidanceClaimConflict.id.desc()
    )
    if status:
        statement = statement.where(GuidanceClaimConflict.status == status)
    return [_overview(session, row) for row in session.scalars(statement)]


def get_detail(session: Session, conflict_id: int) -> ConflictDetail | None:
    row = session.get(GuidanceClaimConflict, conflict_id)
    if row is None:
        return None
    overview = _overview(session, row)
    return ConflictDetail(
        conflict=row,
        left_claim=overview.left_claim,
        right_claim=overview.right_claim,
        canonical_ids=overview.canonical_ids,
        left_hardware_targets=guidance_claims.list_applicability_targets(
            session, row.left_claim_id
        ),
        right_hardware_targets=guidance_claims.list_applicability_targets(
            session, row.right_claim_id
        ),
        left_bios_scopes=guidance_claims.list_bios_scopes(session, row.left_claim_id),
        right_bios_scopes=guidance_claims.list_bios_scopes(session, row.right_claim_id),
        events=tuple(
            session.scalars(
                select(GuidanceClaimConflictEvent)
                .where(GuidanceClaimConflictEvent.conflict_id == row.id)
                .order_by(GuidanceClaimConflictEvent.id)
            )
        ),
    )


def public_conflicts_for_claims(
    session: Session, claim_ids: tuple[int, ...] | list[int]
) -> tuple[ConflictOverview, ...]:
    if not claim_ids:
        return ()
    rows = session.scalars(
        select(GuidanceClaimConflict)
        .where(
            GuidanceClaimConflict.status.in_(PUBLIC_STATUSES),
            GuidanceClaimConflict.left_claim_id.in_(claim_ids),
            GuidanceClaimConflict.right_claim_id.in_(claim_ids),
        )
        .order_by(GuidanceClaimConflict.id)
    )
    return tuple(_overview(session, row) for row in rows)


def status_counts(session: Session) -> dict[str, int]:
    rows = dict(
        session.execute(
            select(GuidanceClaimConflict.status, func.count(GuidanceClaimConflict.id)).group_by(
                GuidanceClaimConflict.status
            )
        )
    )
    return {status: rows.get(status, 0) for status in STATUSES}


def _overview(session: Session, row: GuidanceClaimConflict) -> ConflictOverview:
    left = session.get(GuidanceClaim, row.left_claim_id)
    right = session.get(GuidanceClaim, row.right_claim_id)
    if left is None or right is None:
        raise RuntimeError(f"Knowledge conflict {row.id} references a missing claim.")
    shared = tuple(sorted(_canonical_ids(session, left.id) & _canonical_ids(session, right.id)))
    return ConflictOverview(row, left, right, shared)


def _canonical_ids(session: Session, claim_id: int) -> set[str]:
    return set(
        session.scalars(
            select(GuidanceClaimSetting.canonical_id).where(
                GuidanceClaimSetting.claim_id == claim_id
            )
        )
    )


def _polarity_conflict_reason(left: GuidanceClaim, right: GuidanceClaim) -> str | None:
    left_tokens = set(normalize_alias(left.claim_text).split()) - _NEGATION_TOKENS
    right_tokens = set(normalize_alias(right.claim_text).split()) - _NEGATION_TOKENS
    union = left_tokens | right_tokens
    if len(union) < 3:
        return None
    overlap = len(left_tokens & right_tokens) / len(union)
    if overlap < 0.6 or bool(_NEGATION.search(left.claim_text)) == bool(
        _NEGATION.search(right.claim_text)
    ):
        return None
    return (
        f"Possible polarity disagreement between approved claims {left.id} and {right.id} "
        f"({overlap:.0%} normalized semantic-token overlap); reviewer comparison required."
    )


def _hardware_relationship(
    left: tuple[guidance_claims.GuidanceApplicabilityTarget, ...],
    right: tuple[guidance_claims.GuidanceApplicabilityTarget, ...],
) -> str:
    if not left or not right:
        return HARDWARE_UNKNOWN
    left_groups = _target_groups(left)
    right_groups = _target_groups(right)
    common_types = set(left_groups) & set(right_groups)
    if any(left_groups[kind] & right_groups[kind] for kind in common_types):
        return HARDWARE_OVERLAP
    if common_types:
        return HARDWARE_DISJOINT
    return HARDWARE_UNKNOWN


def _target_groups(
    targets: tuple[guidance_claims.GuidanceApplicabilityTarget, ...],
) -> dict[str, set[str]]:
    groups: dict[str, set[str]] = {}
    for target in targets:
        groups.setdefault(target.target_type, set()).add(target.target_id)
    return groups


def _bios_relationship(
    session: Session,
    left: tuple[guidance_claims.GuidanceBiosScope, ...],
    right: tuple[guidance_claims.GuidanceBiosScope, ...],
) -> str:
    if not left or not right:
        return BIOS_UNKNOWN
    relevant = [
        (left_scope, right_scope)
        for left_scope in left
        for right_scope in right
        if _bios_contexts_overlap(session, left_scope, right_scope)
    ]
    if not relevant:
        return BIOS_DISJOINT_CONTEXT
    relationships = {
        _version_relationship(left_scope, right_scope)
        for left_scope, right_scope in relevant
    }
    if BIOS_OVERLAP in relationships:
        return BIOS_OVERLAP
    if BIOS_VERSION_DIFFERENCE in relationships:
        return BIOS_VERSION_DIFFERENCE
    return BIOS_UNKNOWN


def _bios_contexts_overlap(
    session: Session,
    left: guidance_claims.GuidanceBiosScope,
    right: guidance_claims.GuidanceBiosScope,
) -> bool:
    if left.context_type == right.context_type:
        return left.context_id == right.context_id
    model_scope = left if left.context_type == "motherboard_model" else right
    vendor_scope = right if left.context_type == "motherboard_model" else left
    model = session.get(MotherboardModel, model_scope.context_id)
    return model is not None and model.vendor_id == vendor_scope.context_id


def _version_relationship(
    left: guidance_claims.GuidanceBiosScope,
    right: guidance_claims.GuidanceBiosScope,
) -> str:
    if left.bios_version_exact is not None and right.bios_version_exact is not None:
        return (
            BIOS_OVERLAP
            if left.bios_version_exact.casefold() == right.bios_version_exact.casefold()
            else BIOS_VERSION_DIFFERENCE
        )
    if left.comparison_mode != right.comparison_mode or left.comparison_mode == "opaque":
        return BIOS_UNKNOWN
    if not any((left.valid_from, left.valid_until)) or not any(
        (right.valid_from, right.valid_until)
    ):
        return BIOS_UNKNOWN
    left_from = _version_key(left.valid_from, left.comparison_mode, lower=True)
    left_until = _version_key(left.valid_until, left.comparison_mode, lower=False)
    right_from = _version_key(right.valid_from, right.comparison_mode, lower=True)
    right_until = _version_key(right.valid_until, right.comparison_mode, lower=False)
    if left_until < right_from or right_until < left_from:
        return BIOS_VERSION_DIFFERENCE
    return BIOS_OVERLAP


def _version_key(value: str | None, mode: str, *, lower: bool) -> tuple[int, ...]:
    if value is None:
        return (-1,) if lower else (10**18,)
    if mode == "integer_numeric":
        return (int(value),)
    return tuple(int(part) for part in value.split("."))


def _evidence_strength(session: Session, claim_id: int) -> str:
    primary = bool(
        session.scalar(
            select(EvidencePassage.id)
            .join(GuidanceClaimEvidence, GuidanceClaimEvidence.passage_id == EvidencePassage.id)
            .join(EvidenceSource, EvidenceSource.id == EvidencePassage.source_id)
            .where(
                GuidanceClaimEvidence.claim_id == claim_id,
                EvidenceSource.status == "approved",
                EvidenceSource.trust_tier == "primary_official",
            )
            .limit(1)
        )
    )
    manual = bool(
        session.scalar(
            select(GuidanceClaimSourceSetting.setting_id)
            .where(GuidanceClaimSourceSetting.claim_id == claim_id)
            .limit(1)
        )
    )
    if primary and manual:
        return "mixed_official"
    if primary:
        return "primary_official"
    if manual:
        return "official_manual"
    raise RuntimeError(f"Approved guidance claim {claim_id} has no supported evidence strength.")


def _required_text(value: str, label: str, *, maximum: int) -> str:
    value = " ".join(value.split())
    if not value:
        raise GuidanceClaimError(f"{label} is required.")
    if len(value) > maximum:
        raise GuidanceClaimError(f"{label} is too long.")
    return value


def _snapshot(conflict: GuidanceClaimConflict) -> dict[str, object]:
    return {
        "left_claim_id": conflict.left_claim_id,
        "right_claim_id": conflict.right_claim_id,
        "detection_reason": conflict.detection_reason,
        "hardware_relationship": conflict.hardware_relationship,
        "bios_relationship": conflict.bios_relationship,
        "left_evidence_strength": conflict.left_evidence_strength,
        "right_evidence_strength": conflict.right_evidence_strength,
        "status": conflict.status,
        "reviewer_note": conflict.reviewer_note,
        "reviewer": conflict.reviewer,
        "reviewed_at": conflict.reviewed_at,
    }
