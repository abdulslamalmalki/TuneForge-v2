"""Privacy-limited refusal demand log and reviewed official referral registry."""

import json
import logging
from dataclasses import dataclass
from difflib import SequenceMatcher

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import config
from app.auth.context import current_actor
from app.db.models import (
    CanonicalSetting,
    CanonicalSettingAlias,
    ReferralPointer,
    ReferralPointerEvent,
    ReferralPointerSetting,
    Setting,
    SettingCanonicalMapping,
    UnansweredLookup,
)
from app.domain import evidence_sources
from app.domain.errors import EvidenceSourceError, ReferralPointerError
from app.domain.extraction_runs import utc_now_iso
from app.normalization.canonical_settings import normalize_alias

ACTOR = "local-reviewer"
MAX_NEAREST_MATCHES = 3
MIN_NEAREST_SCORE = 0.25
VALID_SURFACES = {"search", "guidance"}
VALID_REASONS = {
    "no_approved_search_results",
    "no_exact_guidance_match",
    "no_accepted_guidance_claims",
}
CONTEXT_KEYS = (
    "vendor_id",
    "platform_id",
    "chipset_family_id",
    "chipset_id",
    "model_id",
    "canonical_id",
    "category_id",
    "risk_level",
    "cpu_model_id",
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NearestCanonicalMatch:
    canonical_id: str
    canonical_name: str
    matched_name: str
    match_kind: str
    score: float


@dataclass(frozen=True)
class LookupGap:
    id: int
    surface: str
    reason: str
    query_text: str
    normalized_query: str
    nearest_matches: tuple[NearestCanonicalMatch, ...]
    context: dict[str, str]
    created_at: str


@dataclass(frozen=True)
class LookupGapSummary:
    surface: str
    reason: str
    query_text: str
    normalized_query: str
    occurrence_count: int
    first_seen_at: str
    last_seen_at: str
    nearest_matches: tuple[NearestCanonicalMatch, ...]
    context: dict[str, str]


@dataclass(frozen=True)
class ReferralPointerEventView:
    id: int
    action: str
    actor: str
    detail: str
    created_at: str


@dataclass(frozen=True)
class ReferralPointerView:
    id: int
    publisher_id: str
    publisher_name: str
    title: str
    source_url: str
    document_id: str | None
    document_version: str
    status: str
    reviewer: str
    review_note: str
    canonical_ids: tuple[str, ...]
    created_at: str
    retired_at: str | None
    events: tuple[ReferralPointerEventView, ...]


def nearest_canonical_matches(
    session: Session,
    query: str,
) -> tuple[NearestCanonicalMatch, ...]:
    """Return deterministic candidates from reviewed identities, aliases, and mappings."""

    normalized_query = normalize_alias(_clean_query(query))
    if not normalized_query:
        return ()
    identities = {
        row.canonical_id: row
        for row in session.scalars(select(CanonicalSetting).order_by(CanonicalSetting.canonical_id))
    }
    candidates: list[tuple[str, str, str]] = []
    for identity in identities.values():
        candidates.extend(
            (
                (identity.canonical_id, identity.canonical_name, "canonical_name"),
                (identity.canonical_id, identity.canonical_id, "canonical_id"),
            )
        )
    for alias in session.scalars(select(CanonicalSettingAlias)):
        if alias.canonical_id in identities:
            candidates.append((alias.canonical_id, alias.display_alias, "reviewed_alias"))
    mapped_rows = session.execute(
        select(
            SettingCanonicalMapping.canonical_id,
            func.coalesce(Setting.edited_name, Setting.extracted_name),
        )
        .join(Setting, Setting.id == SettingCanonicalMapping.setting_id)
        .where(Setting.status == "approved")
    )
    for canonical_id, setting_name in mapped_rows:
        if canonical_id in identities:
            candidates.append((canonical_id, setting_name, "mapped_vendor_name"))

    best_by_canonical: dict[str, NearestCanonicalMatch] = {}
    for canonical_id, display_name, kind in candidates:
        normalized_candidate = normalize_alias(display_name)
        if not normalized_candidate:
            continue
        score = SequenceMatcher(None, normalized_query, normalized_candidate).ratio()
        match = NearestCanonicalMatch(
            canonical_id=canonical_id,
            canonical_name=identities[canonical_id].canonical_name,
            matched_name=display_name,
            match_kind=kind,
            score=round(score, 3),
        )
        previous = best_by_canonical.get(canonical_id)
        if previous is None or (-match.score, match.matched_name) < (
            -previous.score,
            previous.matched_name,
        ):
            best_by_canonical[canonical_id] = match
    ranked = sorted(
        (match for match in best_by_canonical.values() if match.score >= MIN_NEAREST_SCORE),
        key=lambda item: (-item.score, item.canonical_name.casefold(), item.canonical_id),
    )
    return tuple(ranked[:MAX_NEAREST_MATCHES])


def record_lookup_gap(
    session: Session,
    *,
    surface: str,
    reason: str,
    query: str,
    context: dict[str, str] | None = None,
) -> LookupGap | None:
    """Append one immutable refusal record; blank lookups are intentionally ignored."""

    query_text = _clean_query(query)
    if not query_text:
        return None
    if surface not in VALID_SURFACES or reason not in VALID_REASONS:
        raise ValueError("Unsupported refusal surface or reason.")
    nearest = nearest_canonical_matches(session, query_text)
    safe_context = _clean_context(context or {})
    row = UnansweredLookup(
        surface=surface,
        reason=reason,
        query_text=query_text,
        normalized_query=normalize_alias(query_text) or query_text.casefold(),
        nearest_matches_json=json.dumps(
            [_nearest_snapshot(match) for match in nearest], sort_keys=True
        ),
        context_json=json.dumps(safe_context, sort_keys=True),
        created_at=utc_now_iso(),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return _gap_view(row)


def try_record_lookup_gap(
    session: Session,
    *,
    surface: str,
    reason: str,
    query: str,
    context: dict[str, str] | None = None,
) -> LookupGap | None:
    """Append a gap without allowing audit-storage failure to break the safe refusal."""

    try:
        return record_lookup_gap(
            session,
            surface=surface,
            reason=reason,
            query=query,
            context=context,
        )
    except SQLAlchemyError:
        session.rollback()
        logger.exception("could not append %s refusal gap", surface)
        return None


def list_lookup_gaps(session: Session, *, limit: int = 200) -> list[LookupGap]:
    safe_limit = max(1, min(limit, 1000))
    rows = session.scalars(
        select(UnansweredLookup).order_by(UnansweredLookup.id.desc()).limit(safe_limit)
    )
    return [_gap_view(row) for row in rows]


def list_gap_worklist(session: Session, *, limit: int = 200) -> list[LookupGapSummary]:
    """Aggregate immutable events into a demand-ranked reviewer worklist."""

    safe_limit = max(1, min(limit, 1000))
    grouped = list(
        session.execute(
            select(
                UnansweredLookup.surface,
                UnansweredLookup.reason,
                UnansweredLookup.normalized_query,
                UnansweredLookup.context_json,
                func.count(UnansweredLookup.id),
                func.min(UnansweredLookup.created_at),
                func.max(UnansweredLookup.created_at),
                func.max(UnansweredLookup.id),
            )
            .group_by(
                UnansweredLookup.surface,
                UnansweredLookup.reason,
                UnansweredLookup.normalized_query,
                UnansweredLookup.context_json,
            )
            .order_by(
                func.count(UnansweredLookup.id).desc(),
                func.max(UnansweredLookup.created_at).desc(),
                func.max(UnansweredLookup.id).desc(),
            )
            .limit(safe_limit)
        )
    )
    latest_ids = [row[7] for row in grouped]
    latest_by_id = (
        {
            row.id: row
            for row in session.scalars(
                select(UnansweredLookup).where(UnansweredLookup.id.in_(latest_ids))
            )
        }
        if latest_ids
        else {}
    )
    summaries = []
    for (
        surface,
        reason,
        normalized_query,
        context_json,
        count,
        first_seen,
        last_seen,
        latest_id,
    ) in grouped:
        latest = latest_by_id[latest_id]
        summaries.append(
            LookupGapSummary(
                surface=surface,
                reason=reason,
                query_text=latest.query_text,
                normalized_query=normalized_query,
                occurrence_count=count,
                first_seen_at=first_seen,
                last_seen_at=last_seen,
                nearest_matches=_parse_nearest(latest.nearest_matches_json),
                context=_parse_context(context_json),
            )
        )
    return summaries


def register_referral_pointer(
    session: Session,
    *,
    publisher_id: str,
    title: str,
    source_url: str,
    document_id: str = "",
    document_version: str = "",
    canonical_ids: list[str] | tuple[str, ...] = (),
    review_note: str,
) -> ReferralPointerView:
    title = " ".join(title.split())
    if not title or len(title) > config.MAX_DOCUMENT_TITLE_LENGTH:
        raise ReferralPointerError(
            f"Title is required and must be {config.MAX_DOCUMENT_TITLE_LENGTH} characters or fewer."
        )
    try:
        publisher_id, publisher_name, source_url = evidence_sources.validate_official_source_url(
            publisher_id, source_url
        )
    except EvidenceSourceError as error:
        raise ReferralPointerError(error.message) from None
    document_id = " ".join(document_id.split())
    document_version = " ".join(document_version.split())
    if (
        len(document_id) > config.MAX_LABEL_LENGTH
        or len(document_version) > config.MAX_LABEL_LENGTH
    ):
        raise ReferralPointerError("Document ID and version must be 100 characters or fewer.")
    review_note = " ".join(review_note.split())
    if not review_note or len(review_note) > config.MAX_EVIDENCE_REVIEW_NOTE_LENGTH:
        raise ReferralPointerError("A concise reviewer note is required.")
    canonical_ids = tuple(dict.fromkeys(item.strip() for item in canonical_ids if item.strip()))
    missing = [item for item in canonical_ids if session.get(CanonicalSetting, item) is None]
    if missing:
        raise ReferralPointerError(f"Unknown canonical setting: {missing[0]}.")
    duplicate = session.scalar(
        select(ReferralPointer.id).where(
            ReferralPointer.publisher_id == publisher_id,
            ReferralPointer.title == title,
            ReferralPointer.source_url == source_url,
            func.coalesce(ReferralPointer.document_version, "") == document_version,
        )
    )
    if duplicate is not None:
        raise ReferralPointerError(f"This official referral is already registered as {duplicate}.")
    now = utc_now_iso()
    pointer = ReferralPointer(
        publisher_id=publisher_id,
        publisher_name=publisher_name,
        title=title,
        source_url=source_url,
        document_id=document_id or None,
        document_version=document_version,
        status="active",
        reviewer=current_actor(),
        review_note=review_note,
        created_at=now,
        retired_at=None,
    )
    session.add(pointer)
    session.flush()
    for canonical_id in canonical_ids:
        session.add(ReferralPointerSetting(pointer_id=pointer.id, canonical_id=canonical_id))
    session.add(
        ReferralPointerEvent(
            pointer_id=pointer.id,
            action="registered",
            actor=current_actor(),
            detail=json.dumps(
                {
                    "canonical_ids": canonical_ids,
                    "document_id": pointer.document_id,
                    "document_version": pointer.document_version,
                    "publisher_id": publisher_id,
                    "review_note": review_note,
                    "source_url": source_url,
                    "title": title,
                },
                sort_keys=True,
            ),
            created_at=now,
        )
    )
    session.commit()
    return get_referral_pointer(session, pointer.id)  # type: ignore[return-value]


def retire_referral_pointer(
    session: Session,
    pointer_id: int,
    *,
    review_note: str,
) -> ReferralPointerView:
    pointer = session.get(ReferralPointer, pointer_id)
    if pointer is None:
        raise ReferralPointerError("Official referral pointer not found.")
    if pointer.status != "active":
        raise ReferralPointerError("Official referral pointer is already retired.")
    review_note = " ".join(review_note.split())
    if not review_note or len(review_note) > config.MAX_EVIDENCE_REVIEW_NOTE_LENGTH:
        raise ReferralPointerError("A concise retirement note is required.")
    now = utc_now_iso()
    pointer.status = "retired"
    pointer.retired_at = now
    session.add(
        ReferralPointerEvent(
            pointer_id=pointer.id,
            action="retired",
            actor=current_actor(),
            detail=json.dumps({"review_note": review_note}, sort_keys=True),
            created_at=now,
        )
    )
    session.commit()
    return get_referral_pointer(session, pointer.id)  # type: ignore[return-value]


def get_referral_pointer(session: Session, pointer_id: int) -> ReferralPointerView | None:
    pointer = session.get(ReferralPointer, pointer_id)
    return _pointer_view(session, pointer) if pointer else None


def list_referral_pointers(
    session: Session,
    *,
    active_only: bool = False,
) -> list[ReferralPointerView]:
    statement = select(ReferralPointer)
    if active_only:
        statement = statement.where(ReferralPointer.status == "active")
    pointers = session.scalars(
        statement.order_by(ReferralPointer.publisher_name, ReferralPointer.id)
    )
    return [_pointer_view(session, pointer) for pointer in pointers]


def referrals_for_matches(
    session: Session,
    matches: tuple[NearestCanonicalMatch, ...] = (),
    *,
    canonical_id: str | None = None,
    limit: int = 5,
) -> list[ReferralPointerView]:
    canonical_ids = [match.canonical_id for match in matches]
    if canonical_id:
        canonical_ids.insert(0, canonical_id)
    canonical_ids = list(dict.fromkeys(canonical_ids))
    linked_ids: list[int] = []
    if canonical_ids:
        linked_ids = list(
            session.scalars(
                select(ReferralPointerSetting.pointer_id)
                .join(ReferralPointer, ReferralPointer.id == ReferralPointerSetting.pointer_id)
                .where(
                    ReferralPointerSetting.canonical_id.in_(canonical_ids),
                    ReferralPointer.status == "active",
                )
                .order_by(ReferralPointerSetting.pointer_id)
            )
        )
    general_ids = list(
        session.scalars(
            select(ReferralPointer.id)
            .outerjoin(
                ReferralPointerSetting,
                ReferralPointerSetting.pointer_id == ReferralPointer.id,
            )
            .where(ReferralPointer.status == "active", ReferralPointerSetting.pointer_id.is_(None))
            .order_by(ReferralPointer.publisher_name, ReferralPointer.id)
        )
    )
    pointer_ids = list(dict.fromkeys([*linked_ids, *general_ids]))[: max(1, min(limit, 20))]
    return [
        view
        for pointer_id in pointer_ids
        if (view := get_referral_pointer(session, pointer_id)) is not None
    ]


def _clean_query(query: str) -> str:
    return " ".join(query.split())[: config.MAX_MISSED_SETTING_NAME_LENGTH]


def _clean_context(context: dict[str, str]) -> dict[str, str]:
    return {
        key: value.strip()[: config.MAX_LABEL_LENGTH]
        for key in CONTEXT_KEYS
        if (value := str(context.get(key, ""))).strip()
    }


def _nearest_snapshot(match: NearestCanonicalMatch) -> dict[str, str | float]:
    return {
        "canonical_id": match.canonical_id,
        "canonical_name": match.canonical_name,
        "match_kind": match.match_kind,
        "matched_name": match.matched_name,
        "score": match.score,
    }


def _parse_nearest(value: str) -> tuple[NearestCanonicalMatch, ...]:
    return tuple(NearestCanonicalMatch(**item) for item in json.loads(value))


def _parse_context(value: str) -> dict[str, str]:
    return {str(key): str(item) for key, item in json.loads(value).items()}


def _gap_view(row: UnansweredLookup) -> LookupGap:
    return LookupGap(
        id=row.id,
        surface=row.surface,
        reason=row.reason,
        query_text=row.query_text,
        normalized_query=row.normalized_query,
        nearest_matches=_parse_nearest(row.nearest_matches_json),
        context=_parse_context(row.context_json),
        created_at=row.created_at,
    )


def _pointer_view(session: Session, pointer: ReferralPointer) -> ReferralPointerView:
    canonical_ids = tuple(
        session.scalars(
            select(ReferralPointerSetting.canonical_id)
            .where(ReferralPointerSetting.pointer_id == pointer.id)
            .order_by(ReferralPointerSetting.canonical_id)
        )
    )
    events = tuple(
        ReferralPointerEventView(
            id=event.id,
            action=event.action,
            actor=event.actor,
            detail=event.detail,
            created_at=event.created_at,
        )
        for event in session.scalars(
            select(ReferralPointerEvent)
            .where(ReferralPointerEvent.pointer_id == pointer.id)
            .order_by(ReferralPointerEvent.id)
        )
    )
    return ReferralPointerView(
        id=pointer.id,
        publisher_id=pointer.publisher_id,
        publisher_name=pointer.publisher_name,
        title=pointer.title,
        source_url=pointer.source_url,
        document_id=pointer.document_id,
        document_version=pointer.document_version,
        status=pointer.status,
        reviewer=pointer.reviewer,
        review_note=pointer.review_note,
        canonical_ids=canonical_ids,
        created_at=pointer.created_at,
        retired_at=pointer.retired_at,
        events=events,
    )
