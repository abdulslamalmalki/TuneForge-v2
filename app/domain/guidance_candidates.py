"""Deterministic, audited guidance candidates derived from approved manuals.

Candidates are never public facts. Promotion creates a draft guidance claim,
which still requires the existing human approval workflow.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app import config
from app.auth.context import current_actor
from app.db.models import (
    CanonicalSetting,
    CanonicalSettingCategory,
    CanonicalSettingClassification,
    GuidanceCandidate,
    GuidanceCandidateEvent,
    GuidanceClaim,
    GuidanceClaimSetting,
    GuidanceClaimSourceSetting,
    Manual,
    ManualApplicability,
    Setting,
    SettingCanonicalMapping,
    UnansweredLookup,
)
from app.domain import canonicalization, guidance_claims, hardware
from app.domain.errors import GuidanceClaimError
from app.domain.extraction_runs import utc_now_iso
from app.domain.review import effective_description, effective_name
from app.normalization.canonical_settings import normalize_alias

GENERATOR_VERSION = "guidance-candidates-1.0"
GENERATOR_ACTOR = "guidance-candidate-engine"
STATUSES = (
    "pending", "needs_research", "uncertain", "rejected", "skipped",
    "duplicate", "promoted",
)
REVIEW_STATES = {"needs_research", "uncertain", "rejected", "skipped"}
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")
_BEHAVIOR = re.compile(
    r"\b(enable|enabled|disable|disabled|auto|increase|decrease|set|select|controls?|"
    r"allows?|determines?|configures?)\b", re.IGNORECASE,
)
_CONSTRAINT = re.compile(
    r"\b(require|requires|required|must|only|cannot|not supported|depends?|prerequisite|"
    r"incompatible|available when)\b", re.IGNORECASE,
)
_SAFETY = re.compile(
    r"\b(warning|caution|damage|unsafe|risk|overheat|instability|unstable|void)\b",
    re.IGNORECASE,
)
_NEGATION = re.compile(r"\b(no|not|never|cannot|disable|disabled|without)\b", re.IGNORECASE)


@dataclass(frozen=True)
class GenerationReport:
    processed_settings: int
    created_candidates: int
    pending_candidates: int
    needs_research_candidates: int
    duplicate_candidates: int
    conflict_candidates: int
    last_setting_id: int | None


@dataclass(frozen=True)
class CandidateQueueRow:
    candidate: GuidanceCandidate
    setting_name: str
    vendor_id: str | None
    vendor_name: str
    manual_title: str
    canonical_name: str | None
    category_name: str | None
    risk_level: str | None


@dataclass(frozen=True)
class CandidateDetail:
    candidate: GuidanceCandidate
    setting: Setting
    manual: Manual
    canonical: CanonicalSetting | None
    category_name: str | None
    risk_level: str | None
    related_claims: tuple[GuidanceClaim, ...]
    events: tuple[GuidanceCandidateEvent, ...]
    previous_id: int | None
    next_id: int | None
    hardware_scope: dict[str, object]
    bios_scope: dict[str, object]


@dataclass(frozen=True)
class CoverageMetrics:
    total_approved_settings: int
    settings_with_verified_claims: int
    settings_with_definitions: int
    settings_with_behavior: int
    settings_with_safety: int
    settings_with_hardware_scope: int
    settings_with_bios_version_scope: int
    needs_guidance: int
    needs_research: int
    conflicting_candidates: int
    candidate_count: int
    pending_candidates: int
    promoted_candidates: int
    coverage_percent: float


def generate_candidates(
    session: Session,
    *,
    after_setting_id: int = 0,
    limit: int | None = None,
    commit: bool = True,
) -> GenerationReport:
    """Generate exact-text candidates idempotently for approved source settings."""
    statement = (
        select(Setting, Manual, SettingCanonicalMapping.canonical_id)
        .join(Manual, Manual.id == Setting.manual_id)
        .outerjoin(
            SettingCanonicalMapping,
            SettingCanonicalMapping.setting_id == Setting.id,
        )
        .where(Setting.status == "approved", Setting.id > max(0, after_setting_id))
        .order_by(Setting.id)
    )
    if limit is not None:
        statement = statement.limit(max(1, min(limit, 10000)))
    rows = list(session.execute(statement))
    manual_scopes = {manual.id: _hardware_scope(session, manual.id) for _, manual, _ in rows}
    demand = _demand_by_canonical(session)
    vendor_counts = dict(
        session.execute(
            select(
                SettingCanonicalMapping.canonical_id,
                func.count(distinct(Manual.vendor_id)),
            )
            .join(Setting, Setting.id == SettingCanonicalMapping.setting_id)
            .join(Manual, Manual.id == Setting.manual_id)
            .where(Setting.status == "approved")
            .group_by(SettingCanonicalMapping.canonical_id)
        ).all()
    )
    classifications = {
        row.canonical_id: row for row in session.scalars(select(CanonicalSettingClassification))
    }
    claims_by_canonical: dict[str, list[GuidanceClaim]] = defaultdict(list)
    for claim, canonical_id in session.execute(
        select(GuidanceClaim, GuidanceClaimSetting.canonical_id)
        .join(GuidanceClaimSetting, GuidanceClaimSetting.claim_id == GuidanceClaim.id)
        .where(GuidanceClaim.status.in_(("draft", "approved")))
    ):
        claims_by_canonical[canonical_id].append(claim)
    candidates_by_canonical: dict[str, list[GuidanceCandidate]] = defaultdict(list)
    existing_keys: set[tuple[int, str | None, str]] = set()
    for candidate in session.scalars(select(GuidanceCandidate).order_by(GuidanceCandidate.id)):
        existing_keys.add((candidate.setting_id, candidate.claim_type, candidate.evidence_sha256))
        if candidate.canonical_id:
            candidates_by_canonical[candidate.canonical_id].append(candidate)

    now = utc_now_iso()
    counts: dict[str, int] = defaultdict(int)
    last_setting_id = None
    for setting, manual, canonical_id in rows:
        last_setting_id = setting.id
        description = " ".join(effective_description(setting).split())
        facts = _atomic_facts(description) or (None,)
        trusted = not canonicalization.source_trust_reasons(setting, manual)
        hardware_scope = manual_scopes[manual.id]
        bios_scope = {"status": "not_explicitly_documented"}
        for fact in facts:
            claim_type = _claim_type(fact) if fact else None
            excerpt = fact or description
            evidence_hash = _evidence_hash(setting, excerpt)
            key = (setting.id, claim_type, evidence_hash)
            if key in existing_keys:
                continue
            status = "pending" if fact and canonical_id and trusted else "needs_research"
            duplicate_candidate = duplicate_claim = None
            conflict_detail = None
            if fact and canonical_id:
                duplicate_claim = _matching_claim(
                    claims_by_canonical[canonical_id], fact, claim_type
                )
                duplicate_candidate = _matching_candidate(
                    candidates_by_canonical[canonical_id], fact, claim_type
                )
                conflict_detail = _conflict_detail(
                    fact, claim_type, claims_by_canonical[canonical_id],
                    candidates_by_canonical[canonical_id],
                )
                if duplicate_claim is not None or duplicate_candidate is not None:
                    status = "duplicate"
            confidence = _confidence(
                trusted=trusted, canonical_id=canonical_id,
                hardware_scope=hardware_scope, fact=fact,
            )
            candidate = GuidanceCandidate(
                setting_id=setting.id,
                canonical_id=canonical_id,
                claim_text=fact,
                claim_type=claim_type,
                source_excerpt=excerpt,
                source_page_number=setting.edited_page_number or setting.page_number,
                hardware_scope_json=json.dumps(hardware_scope, sort_keys=True),
                bios_scope_json=json.dumps(bios_scope, sort_keys=True),
                source_trust="trusted_official_manual" if trusted else "incomplete",
                confidence_basis_points=confidence,
                priority_score=_priority_score(
                    canonical_id, demand=demand, vendor_counts=vendor_counts,
                    classification=classifications.get(canonical_id),
                ),
                status=status,
                possible_conflict=int(conflict_detail is not None),
                conflict_detail=conflict_detail,
                duplicate_of_candidate_id=(
                    duplicate_candidate.id if duplicate_candidate is not None else None
                ),
                promoted_claim_id=duplicate_claim.id if duplicate_claim is not None else None,
                generator_version=GENERATOR_VERSION,
                evidence_sha256=evidence_hash,
                reviewer=None,
                review_note=None,
                reviewed_at=None,
                created_at=now,
                updated_at=now,
            )
            session.add(candidate)
            session.flush()
            session.add(
                GuidanceCandidateEvent(
                    candidate_id=candidate.id,
                    action="generated",
                    actor=GENERATOR_ACTOR,
                    detail=json.dumps(
                        {
                            "canonical_id": canonical_id,
                            "claim_type": claim_type,
                            "confidence_basis_points": confidence,
                            "source_trust": candidate.source_trust,
                            "status": status,
                            "exact_source_excerpt": True,
                            "duplicate_claim_id": candidate.promoted_claim_id,
                            "duplicate_candidate_id": candidate.duplicate_of_candidate_id,
                            "possible_conflict": bool(candidate.possible_conflict),
                        }, sort_keys=True,
                    ),
                    created_at=now,
                )
            )
            existing_keys.add(key)
            if canonical_id:
                candidates_by_canonical[canonical_id].append(candidate)
            counts[status] += 1
            counts["created"] += 1
            if conflict_detail:
                counts["conflict"] += 1
        counts["processed"] += 1
    if commit:
        session.commit()
    else:
        session.flush()
    return GenerationReport(
        processed_settings=counts["processed"],
        created_candidates=counts["created"],
        pending_candidates=counts["pending"],
        needs_research_candidates=counts["needs_research"],
        duplicate_candidates=counts["duplicate"],
        conflict_candidates=counts["conflict"],
        last_setting_id=last_setting_id,
    )


def list_candidates(
    session: Session,
    *,
    status: str = "pending",
    vendor_id: str = "",
    claim_type: str = "",
    conflicts_only: bool = False,
    limit: int = 200,
) -> tuple[CandidateQueueRow, ...]:
    if status and status not in STATUSES:
        raise GuidanceClaimError("Unknown guidance candidate status.")
    statement = (
        select(
            GuidanceCandidate, Setting, Manual, CanonicalSetting,
            CanonicalSettingClassification, CanonicalSettingCategory,
        )
        .join(Setting, Setting.id == GuidanceCandidate.setting_id)
        .join(Manual, Manual.id == Setting.manual_id)
        .outerjoin(
            CanonicalSetting,
            CanonicalSetting.canonical_id == GuidanceCandidate.canonical_id,
        )
        .outerjoin(
            CanonicalSettingClassification,
            CanonicalSettingClassification.canonical_id == GuidanceCandidate.canonical_id,
        )
        .outerjoin(
            CanonicalSettingCategory,
            CanonicalSettingCategory.category_id
            == CanonicalSettingClassification.category_id,
        )
        .order_by(GuidanceCandidate.priority_score.desc(), GuidanceCandidate.id)
        .limit(max(1, min(limit, 500)))
    )
    if status:
        statement = statement.where(GuidanceCandidate.status == status)
    if vendor_id:
        statement = statement.where(Manual.vendor_id == vendor_id.strip().casefold())
    if claim_type:
        statement = statement.where(GuidanceCandidate.claim_type == claim_type)
    if conflicts_only:
        statement = statement.where(GuidanceCandidate.possible_conflict == 1)
    return tuple(
        CandidateQueueRow(
            candidate=candidate,
            setting_name=effective_name(setting),
            vendor_id=manual.vendor_id,
            vendor_name=manual.vendor or "Unknown vendor",
            manual_title=manual.document_title,
            canonical_name=canonical.canonical_name if canonical else None,
            category_name=category.canonical_name if category else None,
            risk_level=classification.risk_level if classification else None,
        )
        for candidate, setting, manual, canonical, classification, category
        in session.execute(statement)
    )


def get_detail(session: Session, candidate_id: int) -> CandidateDetail | None:
    row = session.execute(
        select(
            GuidanceCandidate, Setting, Manual, CanonicalSetting,
            CanonicalSettingClassification, CanonicalSettingCategory,
        )
        .join(Setting, Setting.id == GuidanceCandidate.setting_id)
        .join(Manual, Manual.id == Setting.manual_id)
        .outerjoin(
            CanonicalSetting,
            CanonicalSetting.canonical_id == GuidanceCandidate.canonical_id,
        )
        .outerjoin(
            CanonicalSettingClassification,
            CanonicalSettingClassification.canonical_id == GuidanceCandidate.canonical_id,
        )
        .outerjoin(
            CanonicalSettingCategory,
            CanonicalSettingCategory.category_id
            == CanonicalSettingClassification.category_id,
        )
        .where(GuidanceCandidate.id == candidate_id)
    ).first()
    if row is None:
        return None
    candidate, setting, manual, canonical, classification, category = row
    related_claims = (
        tuple(
            session.scalars(
                select(GuidanceClaim)
                .join(GuidanceClaimSetting, GuidanceClaimSetting.claim_id == GuidanceClaim.id)
                .where(GuidanceClaimSetting.canonical_id == candidate.canonical_id)
                .order_by(GuidanceClaim.status, GuidanceClaim.id)
            )
        )
        if candidate.canonical_id
        else ()
    )
    ids = tuple(
        session.scalars(
            select(GuidanceCandidate.id)
            .where(GuidanceCandidate.status == candidate.status)
            .order_by(GuidanceCandidate.priority_score.desc(), GuidanceCandidate.id)
        )
    )
    index = ids.index(candidate.id)
    return CandidateDetail(
        candidate=candidate,
        setting=setting,
        manual=manual,
        canonical=canonical,
        category_name=category.canonical_name if category else None,
        risk_level=classification.risk_level if classification else None,
        related_claims=related_claims,
        events=tuple(
            session.scalars(
                select(GuidanceCandidateEvent)
                .where(GuidanceCandidateEvent.candidate_id == candidate.id)
                .order_by(GuidanceCandidateEvent.id)
            )
        ),
        previous_id=ids[index - 1] if index > 0 else None,
        next_id=ids[index + 1] if index + 1 < len(ids) else None,
        hardware_scope=_json_object(candidate.hardware_scope_json),
        bios_scope=_json_object(candidate.bios_scope_json),
    )


def edit_candidate(
    session: Session,
    candidate_id: int,
    *,
    claim_text: str,
    claim_type: str,
    note: str,
) -> GuidanceCandidate:
    candidate = _reviewable_candidate(session, candidate_id)
    claim_text = " ".join(claim_text.split())
    claim_type = claim_type.strip().casefold()
    note = _review_note(note)
    if not claim_text or len(claim_text) > config.MAX_GUIDANCE_CLAIM_CHARS:
        raise GuidanceClaimError("Candidate claim text is required and exceeds the claim limit.")
    if claim_type not in guidance_claims.CLAIM_TYPES:
        raise GuidanceClaimError("Choose a supported claim type.")
    before = {"claim_text": candidate.claim_text, "claim_type": candidate.claim_type}
    candidate.claim_text = claim_text
    candidate.claim_type = claim_type
    candidate.status = "pending"
    candidate.reviewer = current_actor()
    candidate.review_note = note
    candidate.reviewed_at = utc_now_iso()
    candidate.updated_at = candidate.reviewed_at
    _event(session, candidate, "edited", {"before": before, "note": note})
    session.commit()
    return candidate


def set_review_state(
    session: Session,
    candidate_id: int,
    *,
    status: str,
    note: str,
) -> GuidanceCandidate:
    if status not in REVIEW_STATES:
        raise GuidanceClaimError("Unsupported candidate review state.")
    candidate = _reviewable_candidate(session, candidate_id)
    note = _review_note(note)
    candidate.status = status
    candidate.reviewer = current_actor()
    candidate.review_note = note
    candidate.reviewed_at = utc_now_iso()
    candidate.updated_at = candidate.reviewed_at
    _event(session, candidate, status, {"note": note})
    session.commit()
    return candidate


def merge_duplicate(
    session: Session,
    candidate_id: int,
    *,
    duplicate_of_candidate_id: int,
    note: str,
) -> GuidanceCandidate:
    candidate = _reviewable_candidate(session, candidate_id)
    target = session.get(GuidanceCandidate, duplicate_of_candidate_id)
    if target is None or target.id == candidate.id:
        raise GuidanceClaimError("Choose a different existing candidate as the duplicate target.")
    if candidate.canonical_id != target.canonical_id:
        raise GuidanceClaimError("Duplicate candidates must reference the same canonical setting.")
    note = _review_note(note)
    candidate.status = "duplicate"
    candidate.duplicate_of_candidate_id = target.id
    candidate.reviewer = current_actor()
    candidate.review_note = note
    candidate.reviewed_at = utc_now_iso()
    candidate.updated_at = candidate.reviewed_at
    _event(session, candidate, "merged_duplicate", {"target_id": target.id, "note": note})
    session.commit()
    return candidate


def promote_candidate(
    session: Session,
    candidate_id: int,
    *,
    note: str,
) -> GuidanceClaim:
    candidate = session.get(GuidanceCandidate, candidate_id)
    if candidate is None or candidate.status != "pending":
        raise GuidanceClaimError("Only a pending candidate can be promoted.")
    note = _review_note(note)
    if candidate.possible_conflict:
        raise GuidanceClaimError("Resolve the possible conflict before promotion.")
    if (
        candidate.source_trust != "trusted_official_manual"
        or candidate.canonical_id is None
        or not candidate.claim_text
        or candidate.claim_type not in guidance_claims.CLAIM_TYPES
    ):
        raise GuidanceClaimError("Candidate evidence is incomplete; mark it Needs Research.")
    setting = session.get(Setting, candidate.setting_id)
    manual = session.get(Manual, setting.manual_id) if setting else None
    if setting is None or manual is None or canonicalization.source_trust_reasons(setting, manual):
        raise GuidanceClaimError("The source no longer satisfies trusted manual evidence gates.")
    try:
        claim = guidance_claims.create_claim(
            session,
            claim_text=candidate.claim_text,
            claim_type=candidate.claim_type,
            passage_ids=(),
            source_setting_ids=(setting.id,),
            canonical_ids=(candidate.canonical_id,),
            actor=current_actor(),
            commit=False,
        )
        now = utc_now_iso()
        candidate.status = "promoted"
        candidate.promoted_claim_id = claim.id
        candidate.reviewer = current_actor()
        candidate.review_note = note
        candidate.reviewed_at = now
        candidate.updated_at = now
        _event(
            session, candidate, "promoted_to_draft_claim",
            {"claim_id": claim.id, "note": note, "public": False},
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return claim


def coverage_metrics(session: Session) -> CoverageMetrics:
    total = session.scalar(
        select(func.count()).select_from(Setting).where(Setting.status == "approved")
    ) or 0
    covered_ids = _covered_setting_ids(session)
    type_counts = {
        claim_type: session.scalar(
            select(func.count(distinct(SettingCanonicalMapping.setting_id)))
            .select_from(SettingCanonicalMapping)
            .join(
                GuidanceClaimSetting,
                GuidanceClaimSetting.canonical_id == SettingCanonicalMapping.canonical_id,
            )
            .join(GuidanceClaim, GuidanceClaim.id == GuidanceClaimSetting.claim_id)
            .where(
                GuidanceClaim.status == "approved",
                GuidanceClaim.claim_type == claim_type,
            )
        ) or 0
        for claim_type in ("definition", "behavior", "safety")
    }
    status_values = dict(
        session.execute(
            select(GuidanceCandidate.status, func.count()).group_by(GuidanceCandidate.status)
        ).all()
    )
    needs_research = session.scalar(
        select(func.count(distinct(GuidanceCandidate.setting_id))).where(
            GuidanceCandidate.status == "needs_research"
        )
    ) or 0
    conflicts = session.scalar(
        select(func.count()).select_from(GuidanceCandidate).where(
            GuidanceCandidate.possible_conflict == 1,
            GuidanceCandidate.status.in_(("pending", "uncertain", "needs_research")),
        )
    ) or 0
    hardware_scope_count = (
        session.scalar(
            select(func.count(distinct(Setting.id)))
            .select_from(Setting)
            .join(ManualApplicability, ManualApplicability.manual_id == Setting.manual_id)
            .where(Setting.id.in_(covered_ids))
        )
        if covered_ids
        else 0
    )
    return CoverageMetrics(
        total_approved_settings=total,
        settings_with_verified_claims=len(covered_ids),
        settings_with_definitions=type_counts["definition"],
        settings_with_behavior=type_counts["behavior"],
        settings_with_safety=type_counts["safety"],
        settings_with_hardware_scope=hardware_scope_count or 0,
        settings_with_bios_version_scope=0,
        needs_guidance=max(0, total - len(covered_ids)),
        needs_research=needs_research,
        conflicting_candidates=conflicts,
        candidate_count=sum(status_values.values()),
        pending_candidates=status_values.get("pending", 0),
        promoted_candidates=status_values.get("promoted", 0),
        coverage_percent=round((len(covered_ids) / total * 100) if total else 0.0, 2),
    )


def status_counts(session: Session) -> dict[str, int]:
    counts = dict(
        session.execute(
            select(GuidanceCandidate.status, func.count()).group_by(GuidanceCandidate.status)
        ).all()
    )
    return {status: counts.get(status, 0) for status in STATUSES}


def _atomic_facts(description: str) -> tuple[str, ...]:
    if not description:
        return ()
    sentences = [item.strip() for item in _SENTENCE_BOUNDARY.split(description) if item.strip()]
    facts: list[str] = []
    for sentence in sentences:
        parts = [item.strip() for item in sentence.split(";") if item.strip()]
        facts.extend(parts if len(parts) > 1 else (sentence,))
    return tuple(dict.fromkeys(fact for fact in facts if len(fact) >= 12))[:8]


def _claim_type(text: str) -> str:
    if _SAFETY.search(text):
        return "safety"
    if _CONSTRAINT.search(text):
        return "constraint"
    if _BEHAVIOR.search(text):
        return "behavior"
    return "definition"


def _evidence_hash(setting: Setting, excerpt: str) -> str:
    page = setting.edited_page_number or setting.page_number
    payload = f"{setting.id}\n{page}\n{excerpt}".encode()
    return hashlib.sha256(payload).hexdigest()


def _confidence(
    *,
    trusted: bool,
    canonical_id: str | None,
    hardware_scope: dict[str, object],
    fact: str | None,
) -> int:
    return min(
        10000,
        (4500 if trusted else 0)
        + (1500 if fact else 0)
        + (1500 if canonical_id else 0)
        + (1500 if fact else 0)
        + (500 if hardware_scope.get("targets") else 0)
        + (500 if fact and len(fact) <= config.MAX_GUIDANCE_CLAIM_CHARS else 0),
    )


def _hardware_scope(session: Session, manual_id: int) -> dict[str, object]:
    view = hardware.get_manual_applicability(session, manual_id)
    if view is None:
        return {"status": "not_reviewed", "targets": []}
    return {
        "status": view.model_status,
        "scope": view.scope,
        "targets": [
            {"type": target.target_type, "id": target.target_id, "name": target.canonical_name}
            for target in view.targets
        ],
    }


def _priority_score(
    canonical_id: str | None,
    *,
    demand: dict[str, int],
    vendor_counts: dict[str, int],
    classification: CanonicalSettingClassification | None,
) -> int:
    if canonical_id is None:
        return 0
    category_bonus = {
        "cpu-performance": 250,
        "voltage-power": 225,
        "memory": 200,
        "pcie-resources": 175,
        "security": 150,
        "virtualization": 150,
    }.get(classification.category_id if classification else "", 50)
    risk_bonus = {
        "critical": 200, "high": 160, "moderate": 100,
        "low": 40, "unclassified": 0,
    }.get(classification.risk_level if classification else "", 0)
    return (
        min(demand.get(canonical_id, 0), 1000) * 1000
        + min(vendor_counts.get(canonical_id, 0), 10) * 100
        + category_bonus
        + risk_bonus
    )


def _demand_by_canonical(session: Session) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for context_json, nearest_json in session.execute(
        select(UnansweredLookup.context_json, UnansweredLookup.nearest_matches_json)
    ):
        context = _json_object(context_json)
        if canonical_id := str(context.get("canonical_id", "")).strip():
            counts[canonical_id] += 1
        try:
            nearest = json.loads(nearest_json)
        except (json.JSONDecodeError, TypeError):
            nearest = []
        for match in nearest[:3] if isinstance(nearest, list) else ():
            if isinstance(match, dict) and match.get("canonical_id"):
                counts[str(match["canonical_id"])] += 1
    return dict(counts)


def _matching_claim(
    claims: list[GuidanceClaim], text: str, claim_type: str
) -> GuidanceClaim | None:
    normalized = normalize_alias(text)
    return next(
        (
            claim for claim in claims
            if claim.claim_type == claim_type
            and normalize_alias(claim.claim_text) == normalized
        ),
        None,
    )


def _matching_candidate(
    candidates: list[GuidanceCandidate], text: str, claim_type: str
) -> GuidanceCandidate | None:
    normalized = normalize_alias(text)
    return next(
        (
            candidate for candidate in candidates
            if candidate.claim_type == claim_type
            and candidate.claim_text
            and normalize_alias(candidate.claim_text) == normalized
        ),
        None,
    )


def _conflict_detail(
    text: str,
    claim_type: str,
    claims: list[GuidanceClaim],
    candidates: list[GuidanceCandidate],
) -> str | None:
    tokens = set(normalize_alias(text).split())
    if len(tokens) < 3:
        return None
    negated = bool(_NEGATION.search(text))
    others = [
        (f"claim {claim.id}", claim.claim_text)
        for claim in claims
        if claim.claim_type == claim_type
    ] + [
        (f"candidate {candidate.id}", candidate.claim_text or "")
        for candidate in candidates
        if candidate.claim_type == claim_type
    ]
    for label, other in others:
        other_tokens = set(normalize_alias(other).split())
        overlap = len(tokens & other_tokens) / len(tokens | other_tokens) if other_tokens else 0
        if overlap >= 0.6 and negated != bool(_NEGATION.search(other)):
            return f"Possible polarity conflict with {label}; reviewer comparison required."
    return None


def _covered_setting_ids(session: Session) -> set[int]:
    mapped = set(
        session.scalars(
            select(distinct(SettingCanonicalMapping.setting_id))
            .join(
                GuidanceClaimSetting,
                GuidanceClaimSetting.canonical_id == SettingCanonicalMapping.canonical_id,
            )
            .join(GuidanceClaim, GuidanceClaim.id == GuidanceClaimSetting.claim_id)
            .where(GuidanceClaim.status == "approved")
        )
    )
    direct = set(
        session.scalars(
            select(distinct(GuidanceClaimSourceSetting.setting_id))
            .join(GuidanceClaim, GuidanceClaim.id == GuidanceClaimSourceSetting.claim_id)
            .where(GuidanceClaim.status == "approved")
        )
    )
    return mapped | direct


def _reviewable_candidate(session: Session, candidate_id: int) -> GuidanceCandidate:
    candidate = session.get(GuidanceCandidate, candidate_id)
    if candidate is None:
        raise GuidanceClaimError("Guidance candidate not found.")
    if candidate.status in {"promoted", "duplicate"}:
        raise GuidanceClaimError("This candidate has already reached a terminal state.")
    return candidate


def _review_note(value: str) -> str:
    value = " ".join(value.split())
    if not value:
        raise GuidanceClaimError("A reviewer note is required.")
    if len(value) > config.MAX_GUIDANCE_REVIEW_NOTE_LENGTH:
        raise GuidanceClaimError("Reviewer note is too long.")
    return value


def _event(
    session: Session,
    candidate: GuidanceCandidate,
    action: str,
    detail: dict[str, object],
) -> None:
    session.add(
        GuidanceCandidateEvent(
            candidate_id=candidate.id,
            action=action,
            actor=current_actor(),
            detail=json.dumps(detail, sort_keys=True),
            created_at=utc_now_iso(),
        )
    )


def _json_object(value: str) -> dict[str, object]:
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
