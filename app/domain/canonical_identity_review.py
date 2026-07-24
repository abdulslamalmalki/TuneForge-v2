"""Reviewed BIOS menu evidence and canonical identity proposal decisions."""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.auth.context import current_actor
from app.db.models import (
    CanonicalIdentityProposal,
    CanonicalIdentityProposalEvent,
    CanonicalIdentityProposalSetting,
    CanonicalSetting,
    Manual,
    Setting,
    SettingMenuContext,
    SettingMenuContextCitation,
    SettingMenuContextEvent,
)
from app.domain import canonical_settings, canonicalization
from app.domain.errors import CanonicalSettingError
from app.domain.extraction_runs import utc_now_iso
from app.domain.review import effective_name
from app.normalization.canonical_settings import normalize_alias

MENU_CONTEXT_ACTIVE = "active"
MENU_CONTEXT_REVOKED = "revoked"
PROPOSAL_PENDING = "pending"
PROPOSAL_APPROVED = "approved"
PROPOSAL_REJECTED = "rejected"

MAX_MENU_PATH_LENGTH = 1000
MAX_MENU_SEGMENTS = 16
MAX_EVIDENCE_EXCERPT_LENGTH = 2000


@dataclass(frozen=True)
class MenuContextView:
    setting_id: int
    menu_path: str
    normalized_menu_path: str
    source_page_number: int
    evidence_excerpt: str
    status: str
    actor: str
    reviewed_at: str
    updated_at: str
    manual_id: int
    manual_title: str
    source_url: str | None
    citations: tuple[MenuContextCitationView, ...]


@dataclass(frozen=True)
class MenuContextCitationView:
    page_number: int
    evidence_excerpt: str


@dataclass(frozen=True)
class ProposalSourceView:
    setting_id: int
    setting_name: str
    manual_id: int
    manual_title: str
    vendor_id: str | None
    vendor_name: str
    page_number: int
    menu_context: MenuContextView | None


@dataclass(frozen=True)
class IdentityProposalView:
    proposal_id: int
    canonical_id: str
    canonical_name: str
    description: str
    status: str
    confidence_basis_points: int
    engine_version: str
    review_note: str | None
    actor: str | None
    approved_canonical_id: str | None
    created_at: str
    updated_at: str
    reviewed_at: str | None
    sources: tuple[ProposalSourceView, ...]
    approval_ready: bool
    readiness_reasons: tuple[str, ...]


def get_menu_context(session: Session, setting_id: int) -> MenuContextView | None:
    row = session.execute(
        select(SettingMenuContext, Manual)
        .join(Setting, Setting.id == SettingMenuContext.setting_id)
        .join(Manual, Manual.id == Setting.manual_id)
        .where(SettingMenuContext.setting_id == setting_id)
    ).first()
    if row is None:
        return None
    return _menu_context_view(session, *row)


def review_menu_context(
    session: Session,
    setting_id: int,
    *,
    menu_path: str,
    source_page_number: int,
    evidence_excerpt: str,
    supporting_citations: tuple[tuple[int, str], ...] = (),
    note: str = "",
    actor: str | None = None,
    commit: bool = True,
) -> SettingMenuContext:
    """Approve or revise a cited menu path for one approved source setting."""
    actor = actor or current_actor()
    setting, manual = _trusted_setting_and_manual(session, setting_id)
    menu_path, normalized_path = _validate_menu_path(menu_path)
    evidence_excerpt = " ".join(evidence_excerpt.split())
    note = note.strip()
    if not 1 <= source_page_number <= manual.page_count:
        raise CanonicalSettingError(
            f"Menu evidence page must be between 1 and {manual.page_count}."
        )
    if not evidence_excerpt:
        raise CanonicalSettingError("A short official-manual evidence excerpt is required.")
    if len(evidence_excerpt) > MAX_EVIDENCE_EXCERPT_LENGTH:
        raise CanonicalSettingError("Menu evidence excerpt is too long.")
    if len(note) > config.MAX_CANONICAL_MAPPING_NOTE_LENGTH:
        raise CanonicalSettingError("Menu context review note is too long.")
    citations = _validate_citations(
        manual,
        primary_page=source_page_number,
        primary_excerpt=evidence_excerpt,
        supporting_citations=supporting_citations,
    )

    now = utc_now_iso()
    row = session.get(SettingMenuContext, setting_id)
    before = _menu_snapshot(row)
    after = {
        "menu_path": menu_path,
        "normalized_menu_path": normalized_path,
        "source_page_number": source_page_number,
        "evidence_excerpt": evidence_excerpt,
        "status": MENU_CONTEXT_ACTIVE,
    }
    existing_citations = (
        tuple(
            (citation.page_number, citation.evidence_excerpt)
            for citation in _event_citations(session, row.current_event_id)
        )
        if row is not None
        else ()
    )
    if before == after and existing_citations == citations:
        return row
    action = "approved" if row is None else "revised"
    event = SettingMenuContextEvent(
        setting_id=setting.id,
        action=action,
        actor=actor,
        detail=json.dumps(
            {
                "before": before,
                "after": after,
                "citations": [
                    {"page_number": page, "evidence_excerpt": excerpt}
                    for page, excerpt in citations
                ],
                "note": note or None,
                "manual_id": manual.id,
                "source_url": manual.source_url,
            },
            sort_keys=True,
        ),
        created_at=now,
    )
    session.add(event)
    session.flush()
    for page_number, excerpt in citations:
        session.add(
            SettingMenuContextCitation(
                setting_id=setting.id,
                event_id=event.id,
                page_number=page_number,
                evidence_excerpt=excerpt,
                created_at=now,
            )
        )
    if row is None:
        row = SettingMenuContext(
            setting_id=setting.id,
            menu_path=menu_path,
            normalized_menu_path=normalized_path,
            source_page_number=source_page_number,
            evidence_excerpt=evidence_excerpt,
            status=MENU_CONTEXT_ACTIVE,
            current_event_id=event.id,
            actor=actor,
            reviewed_at=now,
            updated_at=now,
        )
        session.add(row)
    else:
        row.menu_path = menu_path
        row.normalized_menu_path = normalized_path
        row.source_page_number = source_page_number
        row.evidence_excerpt = evidence_excerpt
        row.status = MENU_CONTEXT_ACTIVE
        row.current_event_id = event.id
        row.actor = actor
        row.reviewed_at = now
        row.updated_at = now
    if commit:
        session.commit()
    return row


def revoke_menu_context(
    session: Session,
    setting_id: int,
    *,
    reason: str,
    actor: str | None = None,
) -> SettingMenuContext:
    actor = actor or current_actor()
    reason = reason.strip()
    if not reason:
        raise CanonicalSettingError("A menu context revocation reason is required.")
    if len(reason) > config.MAX_CANONICAL_MAPPING_NOTE_LENGTH:
        raise CanonicalSettingError("Menu context revocation reason is too long.")
    row = session.get(SettingMenuContext, setting_id)
    if row is None or row.status != MENU_CONTEXT_ACTIVE:
        raise CanonicalSettingError("No active reviewed menu context was found.")
    before = _menu_snapshot(row)
    now = utc_now_iso()
    event = SettingMenuContextEvent(
        setting_id=setting_id,
        action="revoked",
        actor=actor,
        detail=json.dumps(
            {
                "before": before,
                "after": {**before, "status": MENU_CONTEXT_REVOKED},
                "reason": reason,
            },
            sort_keys=True,
        ),
        created_at=now,
    )
    session.add(event)
    session.flush()
    row.status = MENU_CONTEXT_REVOKED
    row.current_event_id = event.id
    row.actor = actor
    row.updated_at = now
    session.commit()
    return row


def list_menu_context_events(
    session: Session,
    setting_id: int,
) -> list[SettingMenuContextEvent]:
    return list(
        session.scalars(
            select(SettingMenuContextEvent)
            .where(SettingMenuContextEvent.setting_id == setting_id)
            .order_by(SettingMenuContextEvent.id)
        )
    )


def list_identity_proposals(
    session: Session,
    *,
    status: str = "",
) -> tuple[IdentityProposalView, ...]:
    statement = select(CanonicalIdentityProposal).order_by(
        CanonicalIdentityProposal.status,
        CanonicalIdentityProposal.canonical_name,
    )
    if status:
        if status not in {PROPOSAL_PENDING, PROPOSAL_APPROVED, PROPOSAL_REJECTED}:
            raise CanonicalSettingError("Canonical identity proposal status is invalid.")
        statement = statement.where(CanonicalIdentityProposal.status == status)
    return tuple(_proposal_view(session, row) for row in session.scalars(statement))


def get_identity_proposal(
    session: Session,
    proposal_id: int,
) -> IdentityProposalView | None:
    proposal = session.get(CanonicalIdentityProposal, proposal_id)
    return _proposal_view(session, proposal) if proposal is not None else None


def approve_identity_proposal(
    session: Session,
    proposal_id: int,
    *,
    note: str,
    actor: str | None = None,
) -> CanonicalSetting:
    """Publish a proposal only while every evidence gate still passes."""
    actor = actor or current_actor()
    note = _required_review_note(note, "An identity approval note is required.")
    proposal = _pending_proposal(session, proposal_id)
    evaluation = canonicalization.evaluate_backlog(session)
    group = next(
        (
            value
            for value in evaluation.proposed_groups
            if value.canonical_id == proposal.canonical_id
        ),
        None,
    )
    proposal_setting_ids = set(_proposal_setting_ids(session, proposal.id))
    if group is None or set(group.setting_ids) != proposal_setting_ids:
        raise CanonicalSettingError(
            "The proposal evidence changed; run the reviewer again before approval."
        )
    if not group.automatic_eligible:
        raise CanonicalSettingError(
            "The proposal does not satisfy every official evidence and menu-context gate."
        )
    if session.get(CanonicalSetting, proposal.canonical_id) is not None:
        raise CanonicalSettingError("That canonical identity already exists.")

    rows = list(
        session.execute(
            select(Setting, Manual)
            .join(Manual, Manual.id == Setting.manual_id)
            .where(Setting.id.in_(group.setting_ids))
            .order_by(Setting.id)
        )
    )
    now = utc_now_iso()
    try:
        identity = canonical_settings.create_identity(
            session,
            name=proposal.canonical_name,
            description=proposal.description,
            canonical_id=proposal.canonical_id,
            actor=actor,
            commit=False,
        )
        for setting, manual in rows:
            source_name = effective_name(setting)
            if normalize_alias(source_name) != normalize_alias(identity.canonical_name):
                canonical_settings.add_alias(
                    session,
                    identity,
                    alias=source_name,
                    vendor_id=manual.vendor_id,
                    actor=actor,
                    commit=False,
                )
            canonical_settings.map_setting(
                session,
                setting,
                canonical_id=identity.canonical_id,
                note=(
                    f"Canonical identity proposal {proposal.id} approved with "
                    "reviewed official menu-path evidence. "
                    f"{note}"
                ),
                actor=actor,
                mapping_method=canonical_settings.MAPPING_REVIEWER,
                commit=False,
            )
        proposal.status = PROPOSAL_APPROVED
        proposal.review_note = note
        proposal.actor = actor
        proposal.approved_canonical_id = identity.canonical_id
        proposal.updated_at = now
        proposal.reviewed_at = now
        session.add(
            CanonicalIdentityProposalEvent(
                proposal_id=proposal.id,
                action="approved",
                actor=actor,
                detail=json.dumps(
                    {
                        "canonical_id": identity.canonical_id,
                        "note": note,
                        "setting_ids": list(group.setting_ids),
                        "confidence_basis_points": group.confidence_basis_points,
                        "reviewed_menu_paths": list(group.reviewed_menu_paths),
                    },
                    sort_keys=True,
                ),
                created_at=now,
            )
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return identity


def reject_identity_proposal(
    session: Session,
    proposal_id: int,
    *,
    reason: str,
    actor: str | None = None,
) -> CanonicalIdentityProposal:
    actor = actor or current_actor()
    reason = _required_review_note(reason, "An identity rejection reason is required.")
    proposal = _pending_proposal(session, proposal_id)
    now = utc_now_iso()
    proposal.status = PROPOSAL_REJECTED
    proposal.review_note = reason
    proposal.actor = actor
    proposal.updated_at = now
    proposal.reviewed_at = now
    session.add(
        CanonicalIdentityProposalEvent(
            proposal_id=proposal.id,
            action="rejected",
            actor=actor,
            detail=json.dumps({"reason": reason}, sort_keys=True),
            created_at=now,
        )
    )
    session.commit()
    return proposal


def reopen_identity_proposal(
    session: Session,
    proposal_id: int,
    *,
    reason: str,
    actor: str | None = None,
) -> CanonicalIdentityProposal:
    actor = actor or current_actor()
    reason = _required_review_note(reason, "A proposal reopening reason is required.")
    proposal = session.get(CanonicalIdentityProposal, proposal_id)
    if proposal is None:
        raise CanonicalSettingError("Canonical identity proposal not found.")
    if proposal.status != PROPOSAL_REJECTED:
        raise CanonicalSettingError("Only rejected identity proposals can be reopened.")
    now = utc_now_iso()
    proposal.status = PROPOSAL_PENDING
    proposal.review_note = None
    proposal.actor = actor
    proposal.updated_at = now
    proposal.reviewed_at = None
    session.add(
        CanonicalIdentityProposalEvent(
            proposal_id=proposal.id,
            action="reopened",
            actor=actor,
            detail=json.dumps({"reason": reason}, sort_keys=True),
            created_at=now,
        )
    )
    session.commit()
    return proposal


def list_identity_proposal_events(
    session: Session,
    proposal_id: int,
) -> list[CanonicalIdentityProposalEvent]:
    return list(
        session.scalars(
            select(CanonicalIdentityProposalEvent)
            .where(CanonicalIdentityProposalEvent.proposal_id == proposal_id)
            .order_by(CanonicalIdentityProposalEvent.id)
        )
    )


def _trusted_setting_and_manual(
    session: Session,
    setting_id: int,
) -> tuple[Setting, Manual]:
    row = session.execute(
        select(Setting, Manual)
        .join(Manual, Manual.id == Setting.manual_id)
        .where(Setting.id == setting_id)
    ).first()
    if row is None:
        raise CanonicalSettingError("Source setting not found.")
    setting, manual = row
    reasons = canonicalization.source_trust_reasons(setting, manual)
    if reasons:
        raise CanonicalSettingError(
            "Menu context requires a trusted approved source: " + " ".join(reasons)
        )
    return setting, manual


def _validate_menu_path(value: str) -> tuple[str, str]:
    segments = tuple(" ".join(segment.split()) for segment in value.split(">"))
    if not 2 <= len(segments) <= MAX_MENU_SEGMENTS or any(not segment for segment in segments):
        raise CanonicalSettingError(
            "Menu path must contain 2 to 16 ordered segments separated by >."
        )
    menu_path = " > ".join(segments)
    if len(menu_path) > MAX_MENU_PATH_LENGTH:
        raise CanonicalSettingError("Menu path is too long.")
    normalized = " > ".join(normalize_alias(segment) for segment in segments)
    return menu_path, normalized


def _validate_citations(
    manual: Manual,
    *,
    primary_page: int,
    primary_excerpt: str,
    supporting_citations: tuple[tuple[int, str], ...],
) -> tuple[tuple[int, str], ...]:
    values = {primary_page: primary_excerpt}
    for page_number, excerpt in supporting_citations:
        excerpt = " ".join(excerpt.split())
        if not 1 <= page_number <= manual.page_count:
            raise CanonicalSettingError(
                f"Menu evidence page must be between 1 and {manual.page_count}."
            )
        if not excerpt:
            raise CanonicalSettingError("Every menu citation requires an evidence excerpt.")
        if len(excerpt) > MAX_EVIDENCE_EXCERPT_LENGTH:
            raise CanonicalSettingError("Menu evidence excerpt is too long.")
        if page_number in values and values[page_number] != excerpt:
            raise CanonicalSettingError(
                "A menu evidence page cannot have conflicting excerpts in one review."
            )
        values[page_number] = excerpt
    return tuple(sorted(values.items()))


def _event_citations(
    session: Session,
    event_id: int,
) -> tuple[SettingMenuContextCitation, ...]:
    return tuple(
        session.scalars(
            select(SettingMenuContextCitation)
            .where(SettingMenuContextCitation.event_id == event_id)
            .order_by(SettingMenuContextCitation.page_number)
        )
    )


def _menu_snapshot(row: SettingMenuContext | None) -> dict[str, object] | None:
    if row is None:
        return None
    return {
        "menu_path": row.menu_path,
        "normalized_menu_path": row.normalized_menu_path,
        "source_page_number": row.source_page_number,
        "evidence_excerpt": row.evidence_excerpt,
        "status": row.status,
    }


def _menu_context_view(
    session: Session,
    context: SettingMenuContext,
    manual: Manual,
) -> MenuContextView:
    return MenuContextView(
        setting_id=context.setting_id,
        menu_path=context.menu_path,
        normalized_menu_path=context.normalized_menu_path,
        source_page_number=context.source_page_number,
        evidence_excerpt=context.evidence_excerpt,
        status=context.status,
        actor=context.actor,
        reviewed_at=context.reviewed_at,
        updated_at=context.updated_at,
        manual_id=manual.id,
        manual_title=manual.document_title,
        source_url=manual.source_url,
        citations=tuple(
            MenuContextCitationView(
                page_number=citation.page_number,
                evidence_excerpt=citation.evidence_excerpt,
            )
            for citation in _event_citations(session, context.current_event_id)
        ),
    )


def _proposal_view(
    session: Session,
    proposal: CanonicalIdentityProposal,
) -> IdentityProposalView:
    rows = list(
        session.execute(
            select(Setting, Manual, SettingMenuContext)
            .join(
                CanonicalIdentityProposalSetting,
                CanonicalIdentityProposalSetting.setting_id == Setting.id,
            )
            .join(Manual, Manual.id == Setting.manual_id)
            .outerjoin(
                SettingMenuContext,
                SettingMenuContext.setting_id == Setting.id,
            )
            .where(CanonicalIdentityProposalSetting.proposal_id == proposal.id)
            .order_by(Setting.id)
        )
    )
    sources = tuple(
        ProposalSourceView(
            setting_id=setting.id,
            setting_name=effective_name(setting),
            manual_id=manual.id,
            manual_title=manual.document_title,
            vendor_id=manual.vendor_id,
            vendor_name=manual.vendor or "Unknown vendor",
            page_number=setting.edited_page_number or setting.page_number,
            menu_context=(
                _menu_context_view(session, context, manual)
                if context is not None
                else None
            ),
        )
        for setting, manual, context in rows
    )
    active_contexts = tuple(
        source.menu_context
        for source in sources
        if source.menu_context is not None
        and source.menu_context.status == MENU_CONTEXT_ACTIVE
    )
    readiness_reasons = []
    if len(sources) < 2:
        readiness_reasons.append("At least two source settings are required.")
    if len({source.manual_id for source in sources}) < 2:
        readiness_reasons.append("Evidence must span at least two manuals.")
    if len({source.vendor_id for source in sources if source.vendor_id}) < 2:
        readiness_reasons.append("Evidence must span at least two vendors.")
    if len(active_contexts) != len(sources):
        readiness_reasons.append("Every source requires active reviewed menu-path evidence.")
    if proposal.confidence_basis_points < canonicalization.CONFIDENCE_THRESHOLD_BP:
        readiness_reasons.append("Confidence is below the 99.50% approval threshold.")
    return IdentityProposalView(
        proposal_id=proposal.id,
        canonical_id=proposal.canonical_id,
        canonical_name=proposal.canonical_name,
        description=proposal.description,
        status=proposal.status,
        confidence_basis_points=proposal.confidence_basis_points,
        engine_version=proposal.engine_version,
        review_note=proposal.review_note,
        actor=proposal.actor,
        approved_canonical_id=proposal.approved_canonical_id,
        created_at=proposal.created_at,
        updated_at=proposal.updated_at,
        reviewed_at=proposal.reviewed_at,
        sources=sources,
        approval_ready=not readiness_reasons,
        readiness_reasons=tuple(readiness_reasons),
    )


def _pending_proposal(
    session: Session,
    proposal_id: int,
) -> CanonicalIdentityProposal:
    proposal = session.get(CanonicalIdentityProposal, proposal_id)
    if proposal is None:
        raise CanonicalSettingError("Canonical identity proposal not found.")
    if proposal.status != PROPOSAL_PENDING:
        raise CanonicalSettingError("This canonical identity proposal is not pending.")
    return proposal


def _proposal_setting_ids(session: Session, proposal_id: int) -> tuple[int, ...]:
    return tuple(
        session.scalars(
            select(CanonicalIdentityProposalSetting.setting_id)
            .where(CanonicalIdentityProposalSetting.proposal_id == proposal_id)
            .order_by(CanonicalIdentityProposalSetting.setting_id)
        )
    )


def _required_review_note(value: str, missing_message: str) -> str:
    value = value.strip()
    if not value:
        raise CanonicalSettingError(missing_message)
    if len(value) > config.MAX_CANONICAL_MAPPING_NOTE_LENGTH:
        raise CanonicalSettingError("Canonical identity review note is too long.")
    return value
