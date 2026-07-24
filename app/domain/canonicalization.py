"""Conservative, evidence-backed canonicalization backlog and reviewer.

The engine may suggest broadly, but it may mutate knowledge only when every
mandatory trust signal is present and the deterministic confidence reaches
99.5%. Missing evidence is never treated as evidence of a match.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from urllib.parse import urlsplit
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    CanonicalIdentityProposal,
    CanonicalIdentityProposalEvent,
    CanonicalIdentityProposalSetting,
    CanonicalMappingSuggestion,
    CanonicalSetting,
    CanonicalSettingAlias,
    CanonicalSuggestionEvent,
    Chipset,
    ChipsetFamily,
    Manual,
    MotherboardModel,
    Setting,
    SettingCanonicalMapping,
    SettingMenuContext,
)
from app.domain import canonical_settings, hardware
from app.domain.errors import CanonicalSettingError
from app.domain.extraction_runs import utc_now_iso
from app.domain.review import effective_description, effective_name
from app.normalization.canonical_settings import canonical_slug, normalize_alias

ENGINE_VERSION = "canonicalization-1.0"
AUTOMATIC_ACTOR = "automatic-canonicalization-reviewer"
CONFIDENCE_THRESHOLD_BP = 9950
SUGGESTION_THRESHOLD_BP = 8000
MATCH_CONTEXTUAL = "contextual_evidence"

OUTCOME_AUTO_MAPPED = "auto_mapped"
OUTCOME_AUTO_CREATED = "auto_created"
OUTCOME_MANUAL_REVIEW = "manual_review"

_OFFICIAL_DOMAINS = {
    "asus": "asus.com",
    "msi": "msi.com",
    "gigabyte": "gigabyte.com",
    "asrock": "asrock.com",
}


@dataclass(frozen=True)
class CanonicalizationSignal:
    key: str
    label: str
    score_basis_points: int
    state: str
    detail: str


@dataclass(frozen=True)
class CanonicalizationEntry:
    setting_id: int
    effective_name: str
    effective_description: str
    manual_id: int
    manual_title: str
    vendor_id: str | None
    vendor_name: str
    page_number: int
    source_url: str | None
    candidate_canonical_id: str | None
    candidate_canonical_name: str | None
    confidence_basis_points: int
    source_trusted: bool
    automatic_eligible: bool
    reasons: tuple[str, ...]
    signals: tuple[CanonicalizationSignal, ...]
    support_setting_ids: tuple[int, ...]
    reviewed_menu_path: str | None = None
    proposed_new_canonical_id: str | None = None
    proposed_group_size: int = 0
    proposed_vendor_count: int = 0

    @property
    def confidence_percent(self) -> str:
        return f"{self.confidence_basis_points / 100:.2f}%"


@dataclass(frozen=True)
class ProposedCanonicalGroup:
    canonical_id: str
    canonical_name: str
    description: str
    setting_ids: tuple[int, ...]
    manual_ids: tuple[int, ...]
    vendor_ids: tuple[str, ...]
    confidence_basis_points: int
    automatic_eligible: bool
    reasons: tuple[str, ...]
    proposal_id: int | None = None
    proposal_status: str = "unpersisted"
    reviewed_menu_paths: tuple[str, ...] = ()

    @property
    def confidence_percent(self) -> str:
        return f"{self.confidence_basis_points / 100:.2f}%"


@dataclass(frozen=True)
class CanonicalizationEvaluation:
    entries: tuple[CanonicalizationEntry, ...]
    proposed_groups: tuple[ProposedCanonicalGroup, ...]


@dataclass(frozen=True)
class CanonicalizationReportItem:
    setting_id: int
    setting_name: str
    canonical_id: str
    canonical_name: str
    confidence_basis_points: int
    reasons: tuple[str, ...]
    support_setting_ids: tuple[int, ...]


@dataclass(frozen=True)
class CanonicalizationRunReport:
    run_id: str
    engine_version: str
    confidence_threshold_basis_points: int
    evaluated_count: int
    auto_approved_mappings: tuple[CanonicalizationReportItem, ...]
    new_canonical_settings: tuple[ProposedCanonicalGroup, ...]
    remaining_manual_review_count: int
    pending_suggestion_count: int
    identity_proposals_created_count: int = 0
    identity_proposals_refreshed_count: int = 0


@dataclass(frozen=True)
class BacklogPage:
    items: tuple[CanonicalizationEntry, ...]
    total: int
    page: int
    page_size: int
    pages: int
    proposed_groups: tuple[ProposedCanonicalGroup, ...]


@dataclass
class _CanonicalProfile:
    identity: CanonicalSetting
    aliases: list[CanonicalSettingAlias]
    mapped_rows: list[tuple[Setting, Manual]]
    normalized_names: set[str]
    trusted_descriptions: set[str]


@dataclass
class _ProfileIndex:
    profiles_by_id: dict[str, _CanonicalProfile]
    names: dict[str, set[str]]
    tokens: dict[str, set[str]]
    descriptions: dict[str, set[str]]


def evaluate_backlog(session: Session) -> CanonicalizationEvaluation:
    """Evaluate every approved, unmapped source without changing the database."""
    rows = list(
        session.execute(
            select(Setting, Manual)
            .join(Manual, Manual.id == Setting.manual_id)
            .outerjoin(
                SettingCanonicalMapping,
                SettingCanonicalMapping.setting_id == Setting.id,
            )
            .where(
                Setting.status == "approved",
                SettingCanonicalMapping.setting_id.is_(None),
            )
            .order_by(Setting.id)
        )
    )
    profiles = _profiles(session)
    _apply_reviewed_menu_contexts(session, rows, profiles)
    profile_index = _index_profiles(profiles)
    manual_ids = {
        manual.id
        for _, manual in rows
    } | {
        manual.id
        for profile in profiles
        for _, manual in profile.mapped_rows
    }
    platform_ids = _manual_platform_ids(session, manual_ids)

    entries = tuple(
        _evaluate_setting(
            setting,
            manual,
            profiles,
            profile_index,
            platform_ids,
        )
        for setting, manual in rows
    )
    groups = _attach_proposal_state(session, _proposed_groups(entries))
    group_by_setting = {
        setting_id: group for group in groups for setting_id in group.setting_ids
    }
    entries = tuple(
        replace(
            entry,
            candidate_canonical_id=None,
            candidate_canonical_name=None,
            automatic_eligible=False,
            proposed_new_canonical_id=group_by_setting[entry.setting_id].canonical_id,
            proposed_group_size=len(group_by_setting[entry.setting_id].setting_ids),
            proposed_vendor_count=len(group_by_setting[entry.setting_id].vendor_ids),
            confidence_basis_points=max(
                entry.confidence_basis_points,
                group_by_setting[entry.setting_id].confidence_basis_points,
            ),
            reasons=tuple(
                dict.fromkeys(
                    (
                        *entry.reasons,
                        *group_by_setting[entry.setting_id].reasons,
                    )
                )
            ),
            signals=(),
            support_setting_ids=(),
        )
        if entry.setting_id in group_by_setting
        else entry
        for entry in entries
    )
    return CanonicalizationEvaluation(entries=entries, proposed_groups=groups)


def backlog_page(
    session: Session,
    *,
    query: str = "",
    vendor_id: str = "",
    page: int = 1,
    page_size: int = 50,
) -> BacklogPage:
    evaluation = evaluate_backlog(session)
    normalized_query = normalize_alias(query)
    items = tuple(
        entry
        for entry in evaluation.entries
        if (not vendor_id or entry.vendor_id == vendor_id)
        and (
            not normalized_query
            or normalized_query in normalize_alias(entry.effective_name)
            or (
                entry.candidate_canonical_name is not None
                and normalized_query in normalize_alias(entry.candidate_canonical_name)
            )
        )
    )
    page_size = max(1, min(page_size, 200))
    pages = max(1, (len(items) + page_size - 1) // page_size)
    page = max(1, min(page, pages))
    start = (page - 1) * page_size
    return BacklogPage(
        items=items[start : start + page_size],
        total=len(items),
        page=page,
        page_size=page_size,
        pages=pages,
        proposed_groups=evaluation.proposed_groups,
    )


def run_reviewer(session: Session) -> CanonicalizationRunReport:
    """Persist suggestions and apply only decisions at or above 99.5%."""
    run_id = uuid4().hex
    now = utc_now_iso()
    evaluation = evaluate_backlog(session)
    auto_items: list[CanonicalizationReportItem] = []
    pending_suggestions = 0
    proposals_created = 0
    proposals_refreshed = 0

    try:
        for entry in evaluation.entries:
            if entry.candidate_canonical_id is None:
                continue
            if entry.confidence_basis_points < SUGGESTION_THRESHOLD_BP:
                continue
            suggestion = _persist_suggestion(
                session,
                entry,
                run_id=run_id,
                created_at=now,
            )
            if suggestion.status != "pending":
                continue
            if not entry.automatic_eligible:
                pending_suggestions += 1
                continue
            setting = session.get(Setting, entry.setting_id)
            if setting is None:
                continue
            note = _automatic_note(entry, run_id)
            canonical_settings.map_setting(
                session,
                setting,
                canonical_id=entry.candidate_canonical_id,
                note=note,
                actor=AUTOMATIC_ACTOR,
                mapping_method=canonical_settings.MAPPING_AUTOMATIC,
                commit=False,
            )
            suggestion.status = "accepted"
            suggestion.review_note = note
            suggestion.actor = AUTOMATIC_ACTOR
            suggestion.reviewed_at = now
            session.add(
                CanonicalSuggestionEvent(
                    suggestion_id=suggestion.id,
                    action="automatic_accepted",
                    actor=AUTOMATIC_ACTOR,
                    detail=json.dumps(
                        {
                            "run_id": run_id,
                            "confidence_basis_points": entry.confidence_basis_points,
                            "reasons": list(entry.reasons),
                            "support_setting_ids": list(entry.support_setting_ids),
                        },
                        sort_keys=True,
                    ),
                    created_at=now,
                )
            )
            auto_items.append(
                CanonicalizationReportItem(
                    setting_id=entry.setting_id,
                    setting_name=entry.effective_name,
                    canonical_id=entry.candidate_canonical_id,
                    canonical_name=entry.candidate_canonical_name or entry.candidate_canonical_id,
                    confidence_basis_points=entry.confidence_basis_points,
                    reasons=entry.reasons,
                    support_setting_ids=entry.support_setting_ids,
                )
            )
        proposals_created, proposals_refreshed = _persist_identity_proposals(
            session,
            evaluation.proposed_groups,
            created_at=now,
        )
        session.commit()
    except Exception:
        session.rollback()
        raise

    return CanonicalizationRunReport(
        run_id=run_id,
        engine_version=ENGINE_VERSION,
        confidence_threshold_basis_points=CONFIDENCE_THRESHOLD_BP,
        evaluated_count=len(evaluation.entries),
        auto_approved_mappings=tuple(auto_items),
        # New identities are proposed but remain human-reviewed until a cited
        # menu path exists for every member of the group.
        new_canonical_settings=(),
        remaining_manual_review_count=len(evaluation.entries) - len(auto_items),
        pending_suggestion_count=pending_suggestions,
        identity_proposals_created_count=proposals_created,
        identity_proposals_refreshed_count=proposals_refreshed,
    )


def reverse_automatic_mapping(
    session: Session,
    setting_id: int,
    *,
    reason: str,
) -> None:
    reason = reason.strip()
    if not reason:
        raise CanonicalSettingError("A reversal reason is required.")
    setting = session.get(Setting, setting_id)
    mapping = session.get(SettingCanonicalMapping, setting_id)
    if setting is None or mapping is None:
        raise CanonicalSettingError("Automatic mapping not found.")
    if mapping.mapping_method != canonical_settings.MAPPING_AUTOMATIC:
        raise CanonicalSettingError("Only automatic mappings can be reversed here.")
    canonical_settings.unmap_setting(
        session,
        setting,
        reason=f"Automatic decision reversed: {reason}",
    )


def source_trust_reasons(setting: Setting, manual: Manual) -> tuple[str, ...]:
    """Expose the automatic reviewer's fail-closed source checks."""
    return _trust_reasons(setting, manual)


def official_manual(manual: Manual) -> bool:
    """Return whether a manual satisfies official vendor provenance rules."""
    return _official_manual(manual)


def _apply_reviewed_menu_contexts(
    session: Session,
    backlog_rows: list[object],
    profiles: list[_CanonicalProfile],
) -> None:
    settings = {
        setting.id: setting
        for setting, _ in backlog_rows
    }
    settings.update(
        {
            setting.id: setting
            for profile in profiles
            for setting, _ in profile.mapped_rows
        }
    )
    if not settings:
        return
    for setting in settings.values():
        setting.reviewed_menu_path = None
    contexts = session.scalars(
        select(SettingMenuContext).where(
            SettingMenuContext.setting_id.in_(settings),
            SettingMenuContext.status == "active",
        )
    )
    for context in contexts:
        settings[context.setting_id].reviewed_menu_path = context.menu_path


def _attach_proposal_state(
    session: Session,
    groups: tuple[ProposedCanonicalGroup, ...],
) -> tuple[ProposedCanonicalGroup, ...]:
    if not groups:
        return ()
    proposal_by_id = {
        proposal.canonical_id: proposal
        for proposal in session.scalars(
            select(CanonicalIdentityProposal).where(
                CanonicalIdentityProposal.canonical_id.in_(
                    group.canonical_id for group in groups
                )
            )
        )
    }
    return tuple(
        replace(
            group,
            proposal_id=proposal.id,
            proposal_status=proposal.status,
        )
        if (proposal := proposal_by_id.get(group.canonical_id)) is not None
        else group
        for group in groups
    )


def _persist_identity_proposals(
    session: Session,
    groups: tuple[ProposedCanonicalGroup, ...],
    *,
    created_at: str,
) -> tuple[int, int]:
    created = 0
    refreshed = 0
    for group in groups:
        if session.get(CanonicalSetting, group.canonical_id) is not None:
            continue
        proposal = session.scalar(
            select(CanonicalIdentityProposal).where(
                CanonicalIdentityProposal.canonical_id == group.canonical_id
            )
        )
        evidence = {
            "engine_version": ENGINE_VERSION,
            "confidence_basis_points": group.confidence_basis_points,
            "automatic_eligible": group.automatic_eligible,
            "setting_ids": list(group.setting_ids),
            "manual_ids": list(group.manual_ids),
            "vendor_ids": list(group.vendor_ids),
            "reviewed_menu_paths": list(group.reviewed_menu_paths),
            "reasons": list(group.reasons),
        }
        evidence_json = json.dumps(evidence, sort_keys=True)
        if proposal is None:
            proposal = CanonicalIdentityProposal(
                canonical_id=group.canonical_id,
                canonical_name=group.canonical_name,
                description=group.description,
                status="pending",
                confidence_basis_points=group.confidence_basis_points,
                evidence_json=evidence_json,
                engine_version=ENGINE_VERSION,
                review_note=None,
                actor=None,
                approved_canonical_id=None,
                created_at=created_at,
                updated_at=created_at,
                reviewed_at=None,
            )
            session.add(proposal)
            session.flush()
            for setting_id in group.setting_ids:
                session.add(
                    CanonicalIdentityProposalSetting(
                        proposal_id=proposal.id,
                        setting_id=setting_id,
                        created_at=created_at,
                    )
                )
            session.add(
                CanonicalIdentityProposalEvent(
                    proposal_id=proposal.id,
                    action="generated",
                    actor=AUTOMATIC_ACTOR,
                    detail=evidence_json,
                    created_at=created_at,
                )
            )
            created += 1
            continue
        if proposal.status != "pending":
            continue

        existing_setting_ids = set(
            session.scalars(
                select(CanonicalIdentityProposalSetting.setting_id).where(
                    CanonicalIdentityProposalSetting.proposal_id == proposal.id
                )
            )
        )
        new_setting_ids = set(group.setting_ids) - existing_setting_ids
        changed = (
            proposal.canonical_name != group.canonical_name
            or proposal.description != group.description
            or proposal.confidence_basis_points != group.confidence_basis_points
            or proposal.evidence_json != evidence_json
            or proposal.engine_version != ENGINE_VERSION
            or bool(new_setting_ids)
        )
        if not changed:
            continue
        proposal.canonical_name = group.canonical_name
        proposal.description = group.description
        proposal.confidence_basis_points = group.confidence_basis_points
        proposal.evidence_json = evidence_json
        proposal.engine_version = ENGINE_VERSION
        proposal.updated_at = created_at
        for setting_id in sorted(new_setting_ids):
            session.add(
                CanonicalIdentityProposalSetting(
                    proposal_id=proposal.id,
                    setting_id=setting_id,
                    created_at=created_at,
                )
            )
        session.add(
            CanonicalIdentityProposalEvent(
                proposal_id=proposal.id,
                action="refreshed",
                actor=AUTOMATIC_ACTOR,
                detail=evidence_json,
                created_at=created_at,
            )
        )
        refreshed += 1
    return created, refreshed


def _profiles(session: Session) -> list[_CanonicalProfile]:
    identities = list(
        session.scalars(
            select(CanonicalSetting)
            .where(CanonicalSetting.status == canonical_settings.IDENTITY_ACTIVE)
            .order_by(CanonicalSetting.canonical_id)
        )
    )
    profiles = {
        identity.canonical_id: _CanonicalProfile(
            identity=identity,
            aliases=[],
            mapped_rows=[],
            normalized_names={normalize_alias(identity.canonical_name)},
            trusted_descriptions=set(),
        )
        for identity in identities
    }
    for alias in session.scalars(select(CanonicalSettingAlias)):
        profile = profiles.get(alias.canonical_id)
        if profile is not None:
            profile.aliases.append(alias)
            profile.normalized_names.add(alias.normalized_alias)
    rows = session.execute(
        select(SettingCanonicalMapping.canonical_id, Setting, Manual)
        .join(Setting, Setting.id == SettingCanonicalMapping.setting_id)
        .join(Manual, Manual.id == Setting.manual_id)
        .where(Setting.status == "approved")
    )
    for canonical_id, setting, manual in rows:
        profile = profiles.get(canonical_id)
        if profile is not None:
            profile.mapped_rows.append((setting, manual))
            profile.normalized_names.add(normalize_alias(effective_name(setting)))
            if (
                setting.page_review_status in {"correct", "corrected"}
                and _official_manual(manual)
                and effective_description(setting).strip()
            ):
                profile.trusted_descriptions.add(
                    _normalize_description(effective_description(setting))
                )
    return list(profiles.values())


def _index_profiles(profiles: list[_CanonicalProfile]) -> _ProfileIndex:
    profiles_by_id = {
        profile.identity.canonical_id: profile for profile in profiles
    }
    names: dict[str, set[str]] = defaultdict(set)
    tokens: dict[str, set[str]] = defaultdict(set)
    descriptions: dict[str, set[str]] = defaultdict(set)
    for profile in profiles:
        canonical_id = profile.identity.canonical_id
        for name in profile.normalized_names:
            names[name].add(canonical_id)
            for token in name.split():
                if len(token) >= 3:
                    tokens[token].add(canonical_id)
        for description in profile.trusted_descriptions:
            descriptions[description].add(canonical_id)
    return _ProfileIndex(
        profiles_by_id=profiles_by_id,
        names=dict(names),
        tokens=dict(tokens),
        descriptions=dict(descriptions),
    )


def _evaluate_setting(
    setting: Setting,
    manual: Manual,
    profiles: list[_CanonicalProfile],
    profile_index: _ProfileIndex,
    platform_ids: dict[int, set[str]],
) -> CanonicalizationEntry:
    name = normalize_alias(effective_name(setting))
    description = _normalize_description(effective_description(setting))
    possible_ids = set(profile_index.names.get(name, ()))
    possible_ids.update(profile_index.descriptions.get(description, ()))
    for token in {token for token in name.split() if len(token) >= 3}:
        possible_ids.update(profile_index.tokens.get(token, ()))
    possible_profiles = [
        profile_index.profiles_by_id[canonical_id]
        for canonical_id in possible_ids
    ]
    ranked_profiles = sorted(
        (
            (
                max(
                    (
                        _text_similarity(name, candidate)
                        for candidate in profile.normalized_names
                        if candidate
                    ),
                    default=0.0,
                ),
                profile,
            )
            for profile in possible_profiles
        ),
        key=lambda value: (-value[0], value[1].identity.canonical_id),
    )
    shortlisted_ids = {
        profile.identity.canonical_id
        for similarity, profile in ranked_profiles[:5]
        if similarity >= 0.45
    }
    shortlisted_ids.update(
        profile.identity.canonical_id
        for profile in possible_profiles
        if description and description in profile.trusted_descriptions
    )
    shortlisted_profiles = [
        profile
        for profile in profiles
        if profile.identity.canonical_id in shortlisted_ids
    ]
    scored = sorted(
        (
            _score_profile(
                setting,
                manual,
                profile,
                platform_ids,
            )
            for profile in shortlisted_profiles
        ),
        key=lambda value: (-value[0], value[1].identity.canonical_id),
    )
    scored = [value for value in scored if value[0] >= 3500]
    top = scored[0] if scored else None
    runner_up = scored[1][0] if len(scored) > 1 else 0

    reasons = list(_trust_reasons(setting, manual))
    if top is None:
        reasons.append("No existing canonical identity has sufficient supporting evidence.")
        return _entry(setting, manual, reasons=tuple(reasons))

    confidence, profile, signals, support_ids, hard_gates = top
    if confidence - runner_up < 500:
        reasons.append(
            "Competing canonical candidates are too close; human resolution is required."
        )
        hard_gates = False
    if not hard_gates:
        reasons.append("One or more mandatory automatic-approval signals are missing.")
    if confidence < CONFIDENCE_THRESHOLD_BP:
        reasons.append(
            f"Confidence {confidence / 100:.2f}% is below the 99.50% automatic threshold."
        )
    automatic_eligible = (
        hard_gates
        and confidence >= CONFIDENCE_THRESHOLD_BP
        and not _trust_reasons(setting, manual)
    )
    if automatic_eligible:
        reasons.append("All mandatory signals independently support the same canonical identity.")

    return _entry(
        setting,
        manual,
        candidate=profile.identity,
        confidence=confidence,
        automatic_eligible=automatic_eligible,
        reasons=tuple(dict.fromkeys(reasons)),
        signals=signals,
        support_ids=support_ids,
    )


def _score_profile(
    setting: Setting,
    manual: Manual,
    profile: _CanonicalProfile,
    platform_ids: dict[int, set[str]],
) -> tuple[
    int,
    _CanonicalProfile,
    tuple[CanonicalizationSignal, ...],
    tuple[int, ...],
    bool,
]:
    name = normalize_alias(effective_name(setting))
    description = _normalize_description(effective_description(setting))
    profile_names = profile.normalized_names
    exact_aliases = [alias for alias in profile.aliases if alias.normalized_alias == name]
    exact_mapped = [
        (row, source)
        for row, source in profile.mapped_rows
        if normalize_alias(effective_name(row)) == name
    ]
    name_similarity = max(
        (_text_similarity(name, candidate) for candidate in profile_names if candidate),
        default=0.0,
    )
    description_matches = [
        (
            _description_similarity(
                description,
                _normalize_description(effective_description(row)),
            ),
            row,
            source,
        )
        for row, source in profile.mapped_rows
        if effective_description(row).strip()
    ]
    description_similarity = max(
        (similarity for similarity, _, _ in description_matches),
        default=0.0,
    )
    exact_description_support = [
        (row, source)
        for similarity, row, source in description_matches
        if (
            similarity == 1.0
            and source.id != manual.id
            and row.page_review_status in {"correct", "corrected"}
            and _official_manual(source)
        )
    ]
    source_platforms = platform_ids.get(manual.id, set())
    support_platforms = {
        platform_id
        for _, source in profile.mapped_rows
        for platform_id in platform_ids.get(source.id, set())
    }
    context_score = (
        1.0
        if source_platforms and support_platforms and source_platforms & support_platforms
        else 0.0
    )
    official = _official_manual(manual)
    exact_alias = bool(exact_aliases)
    exact_mapped_name = bool(exact_mapped)
    vendor_alias = any(
        alias.vendor_id is not None and alias.vendor_id == manual.vendor_id
        for alias in exact_aliases
    )
    menu_context = _menu_context(setting)
    support_menu_contexts = {
        value
        for row, _ in profile.mapped_rows
        if (value := _menu_context(row)) is not None
    }
    menu_match = menu_context is not None and menu_context in support_menu_contexts

    name_points = 2000 if exact_alias or exact_mapped_name else round(1600 * name_similarity)
    description_points = round(2500 * description_similarity)
    official_points = 1500 if official else 0
    context_points = 1500 if context_score == 1.0 else 0
    menu_points = 1000 if menu_match else 0
    alias_points = 750 if exact_alias else 0
    mapped_points = 500 if exact_mapped_name else 0
    vendor_points = 250 if vendor_alias else 0
    confidence = min(
        10000,
        name_points
        + description_points
        + official_points
        + context_points
        + menu_points
        + alias_points
        + mapped_points
        + vendor_points,
    )
    support_ids = tuple(
        sorted(
            {
                row.id
                for row, _ in (
                    *exact_mapped,
                    *exact_description_support,
                )
            }
        )
    )
    signals = (
        _signal(
            "normalized_name",
            "Normalized name",
            name_points,
            "exact" if exact_alias or exact_mapped_name else "similar",
            (
                "Exact approved alias or mapped source name."
                if exact_alias or exact_mapped_name
                else f"Best deterministic similarity: {name_similarity:.3f}."
            ),
        ),
        _signal(
            "description",
            "Reviewed description",
            description_points,
            "exact" if description_similarity == 1.0 else "similar",
            f"Best deterministic similarity: {description_similarity:.3f}.",
        ),
        _signal(
            "official_manual",
            "Official manual evidence",
            official_points,
            "verified" if official else "missing",
            "Official HTTPS vendor source, accepted manual, and verified page."
            if official
            else "Official provenance or acceptance requirements are incomplete.",
        ),
        _signal(
            "document_context",
            "Document and hardware context",
            context_points,
            "compatible" if context_score == 1.0 else "unverified",
            "Reviewed platform applicability overlaps mapped evidence."
            if context_score == 1.0
            else "No reviewed applicability overlap was established.",
        ),
        _signal(
            "vendor_alias",
            "Vendor-specific alias",
            vendor_points,
            "exact" if vendor_alias else "missing",
            "Exact alias is explicitly scoped to this vendor."
            if vendor_alias
            else "No exact vendor-scoped alias supports this candidate.",
        ),
        _signal(
            "menu_context",
            "BIOS menu context",
            menu_points,
            "exact" if menu_match else "unavailable",
            "Reviewed menu context matches mapped evidence."
            if menu_match
            else "TuneForge has no reviewed menu path for this source setting.",
        ),
    )
    hard_gates = all(
        (
            official,
            exact_alias or exact_mapped_name,
            bool(exact_description_support),
            context_score == 1.0,
            menu_match,
        )
    )
    return confidence, profile, signals, support_ids, hard_gates


def _entry(
    setting: Setting,
    manual: Manual,
    *,
    candidate: CanonicalSetting | None = None,
    confidence: int = 0,
    automatic_eligible: bool = False,
    reasons: tuple[str, ...] = (),
    signals: tuple[CanonicalizationSignal, ...] = (),
    support_ids: tuple[int, ...] = (),
) -> CanonicalizationEntry:
    return CanonicalizationEntry(
        setting_id=setting.id,
        effective_name=effective_name(setting),
        effective_description=effective_description(setting),
        manual_id=manual.id,
        manual_title=manual.document_title,
        vendor_id=manual.vendor_id,
        vendor_name=manual.vendor or "Unknown vendor",
        page_number=setting.edited_page_number or setting.page_number,
        source_url=manual.source_url,
        candidate_canonical_id=candidate.canonical_id if candidate else None,
        candidate_canonical_name=candidate.canonical_name if candidate else None,
        confidence_basis_points=confidence,
        source_trusted=not _trust_reasons(setting, manual),
        automatic_eligible=automatic_eligible,
        reasons=reasons,
        signals=signals,
        support_setting_ids=support_ids,
        reviewed_menu_path=(
            value
            if isinstance((value := getattr(setting, "reviewed_menu_path", None)), str)
            and value.strip()
            else None
        ),
    )


def _proposed_groups(
    entries: tuple[CanonicalizationEntry, ...],
) -> tuple[ProposedCanonicalGroup, ...]:
    grouped: dict[tuple[str, str], list[CanonicalizationEntry]] = defaultdict(list)
    for entry in entries:
        if (
            entry.candidate_canonical_id is not None
            and entry.confidence_basis_points >= SUGGESTION_THRESHOLD_BP
        ):
            continue
        name = normalize_alias(entry.effective_name)
        description = _normalize_description(entry.effective_description)
        if name and description:
            grouped[(name, description)].append(entry)

    groups = []
    for _, values in grouped.items():
        manual_ids = tuple(sorted({entry.manual_id for entry in values}))
        vendor_ids = tuple(
            sorted({entry.vendor_id for entry in values if entry.vendor_id is not None})
        )
        if len(manual_ids) < 2 or len(vendor_ids) < 2:
            continue
        trusted_sources = all(entry.source_trusted for entry in values)
        canonical_id = canonical_slug(values[0].effective_name)
        reviewed_menu_paths = tuple(
            sorted(
                {
                    entry.reviewed_menu_path.strip()
                    for entry in values
                    if entry.reviewed_menu_path
                },
                key=str.casefold,
            )
        )
        normalized_menu_paths = {
            normalize_alias(path) for path in reviewed_menu_paths
        }
        menu_complete = all(entry.reviewed_menu_path for entry in values)
        menu_exact = menu_complete and len(normalized_menu_paths) == 1
        confidence = 10000 if menu_exact else (9950 if menu_complete else 9000)
        reasons = (
            "Exact normalized name and description agree across multiple official manuals.",
            f"Evidence spans {len(manual_ids)} manuals and {len(vendor_ids)} vendors.",
            (
                "Reviewed BIOS menu context is recorded for every source."
                if menu_complete
                else "Reviewed BIOS menu context is unavailable; human approval is required."
            ),
            (
                "The reviewed menu hierarchy is identical across sources."
                if menu_exact
                else (
                    "Vendor menu hierarchies differ and require the proposal reviewer's "
                    "explicit equivalence decision."
                    if menu_complete
                    else "No menu hierarchy comparison can be made yet."
                )
            ),
            (
                "Every source passes official provenance and citation review."
                if trusted_sources
                else "At least one source fails official provenance or citation review."
            ),
        )
        groups.append(
            ProposedCanonicalGroup(
                canonical_id=canonical_id,
                canonical_name=values[0].effective_name,
                description=values[0].effective_description.strip(),
                setting_ids=tuple(sorted(entry.setting_id for entry in values)),
                manual_ids=manual_ids,
                vendor_ids=vendor_ids,
                confidence_basis_points=confidence,
                automatic_eligible=(
                    trusted_sources
                    and menu_complete
                    and confidence >= CONFIDENCE_THRESHOLD_BP
                ),
                reasons=reasons,
                reviewed_menu_paths=reviewed_menu_paths,
            )
        )
    return tuple(sorted(groups, key=lambda value: value.canonical_name.casefold()))


def _persist_suggestion(
    session: Session,
    entry: CanonicalizationEntry,
    *,
    run_id: str,
    created_at: str,
) -> CanonicalMappingSuggestion:
    if entry.candidate_canonical_id is None:
        raise CanonicalSettingError("A canonical candidate is required.")
    suggestion = session.scalar(
        select(CanonicalMappingSuggestion).where(
            CanonicalMappingSuggestion.setting_id == entry.setting_id,
            CanonicalMappingSuggestion.canonical_id == entry.candidate_canonical_id,
        )
    )
    evidence = _evidence_payload(entry, run_id)
    if suggestion is None:
        suggestion = CanonicalMappingSuggestion(
            setting_id=entry.setting_id,
            canonical_id=entry.candidate_canonical_id,
            match_type=MATCH_CONTEXTUAL,
            matched_alias=entry.candidate_canonical_name or entry.candidate_canonical_id,
            status="pending",
            confidence_basis_points=entry.confidence_basis_points,
            evidence_json=json.dumps(evidence, sort_keys=True),
            engine_version=ENGINE_VERSION,
            review_note=None,
            actor=None,
            created_at=created_at,
            reviewed_at=None,
        )
        session.add(suggestion)
        session.flush()
        action = "contextual_generated"
    elif suggestion.status == "pending":
        suggestion.match_type = MATCH_CONTEXTUAL
        suggestion.confidence_basis_points = entry.confidence_basis_points
        suggestion.evidence_json = json.dumps(evidence, sort_keys=True)
        suggestion.engine_version = ENGINE_VERSION
        action = "contextual_refreshed"
    else:
        return suggestion
    session.add(
        CanonicalSuggestionEvent(
            suggestion_id=suggestion.id,
            action=action,
            actor=AUTOMATIC_ACTOR,
            detail=json.dumps(evidence, sort_keys=True),
            created_at=created_at,
        )
    )
    return suggestion


def _evidence_payload(entry: CanonicalizationEntry, run_id: str) -> dict[str, object]:
    return {
        "run_id": run_id,
        "engine_version": ENGINE_VERSION,
        "confidence_basis_points": entry.confidence_basis_points,
        "confidence_threshold_basis_points": CONFIDENCE_THRESHOLD_BP,
        "automatic_eligible": entry.automatic_eligible,
        "reasons": list(entry.reasons),
        "support_setting_ids": list(entry.support_setting_ids),
        "signals": [
            {
                "key": signal.key,
                "state": signal.state,
                "score_basis_points": signal.score_basis_points,
                "detail": signal.detail,
            }
            for signal in entry.signals
        ],
    }


def _automatic_note(entry: CanonicalizationEntry, run_id: str) -> str:
    return (
        f"Automatic canonicalization {ENGINE_VERSION}; run={run_id}; "
        f"confidence={entry.confidence_basis_points / 100:.2f}%; "
        f"support={','.join(str(value) for value in entry.support_setting_ids)}."
    )


def _trust_reasons(setting: Setting, manual: Manual) -> tuple[str, ...]:
    reasons = []
    if setting.page_review_status not in {"correct", "corrected"}:
        reasons.append("The setting page citation has not been reviewed.")
    if manual.eligibility_status not in {"eligible", "legacy"}:
        reasons.append("The source manual is not eligible for trusted extraction.")
    if manual.acceptance_reviewed_at is None:
        reasons.append("The source manual has not completed acceptance certification.")
    if not _official_manual(manual):
        reasons.append("The source URL is not a verified official vendor HTTPS domain.")
    if not effective_description(setting).strip():
        reasons.append("The approved source setting has no reviewed description.")
    return tuple(reasons)


def _official_manual(manual: Manual) -> bool:
    if (
        manual.eligibility_status not in {"eligible", "legacy"}
        or manual.acceptance_reviewed_at is None
        or manual.vendor_id not in _OFFICIAL_DOMAINS
        or not manual.source_url
    ):
        return False
    parsed = urlsplit(manual.source_url)
    host = (parsed.hostname or "").casefold()
    root = _OFFICIAL_DOMAINS[manual.vendor_id]
    return parsed.scheme == "https" and (host == root or host.endswith(f".{root}"))


def _manual_platform_ids(
    session: Session,
    manual_ids: set[int],
) -> dict[int, set[str]]:
    if not manual_ids:
        return {}
    families = {
        row.family_id: row.platform_id for row in session.scalars(select(ChipsetFamily))
    }
    chipsets = {row.chipset_id: row.platform_id for row in session.scalars(select(Chipset))}
    models = {
        row.model_id: row.platform_id for row in session.scalars(select(MotherboardModel))
    }
    values: dict[int, set[str]] = {}
    for manual_id in manual_ids:
        view = hardware.get_manual_applicability(session, manual_id)
        platforms: set[str] = set()
        for target in view.targets if view else ():
            if target.target_type == hardware.TARGET_PLATFORM:
                platforms.add(target.target_id)
            elif target.target_type == hardware.TARGET_CHIPSET_FAMILY:
                if platform_id := families.get(target.target_id):
                    platforms.add(platform_id)
            elif target.target_type == hardware.TARGET_CHIPSET:
                if platform_id := chipsets.get(target.target_id):
                    platforms.add(platform_id)
            elif target.target_type == hardware.TARGET_MOTHERBOARD_MODEL:
                if platform_id := models.get(target.target_id):
                    platforms.add(platform_id)
        values[manual_id] = platforms
    return values


def _menu_context(setting: Setting) -> str | None:
    # evaluate_backlog attaches only active, reviewer-approved menu evidence.
    # Missing or revoked context therefore remains fail-closed.
    value = getattr(setting, "reviewed_menu_path", None)
    return normalize_alias(value) if isinstance(value, str) and value.strip() else None


def _normalize_description(value: str) -> str:
    return normalize_alias(value)


def _text_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    token_score = (
        len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
        if left_tokens and right_tokens
        else 0.0
    )
    sequence_score = SequenceMatcher(None, left, right, autojunk=False).ratio()
    return max(token_score, sequence_score)


def _description_similarity(left: str, right: str) -> float:
    """Compare prose conservatively without expensive unrelated-text matching."""
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    if not left_tokens or not right_tokens:
        return 0.0
    token_score = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
    if token_score < 0.25 or max(len(left), len(right)) > 600:
        return token_score
    return max(
        token_score,
        SequenceMatcher(None, left, right, autojunk=False).ratio(),
    )


def _signal(
    key: str,
    label: str,
    score_basis_points: int,
    state: str,
    detail: str,
) -> CanonicalizationSignal:
    return CanonicalizationSignal(
        key=key,
        label=label,
        score_basis_points=score_basis_points,
        state=state,
        detail=detail,
    )
