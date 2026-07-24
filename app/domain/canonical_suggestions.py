"""Persistent reviewer queue for exact canonical-setting alias suggestions."""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import config
from app.auth.context import current_actor
from app.db.models import (
    CanonicalMappingSuggestion,
    CanonicalSetting,
    CanonicalSettingAlias,
    CanonicalSettingCategory,
    CanonicalSettingClassification,
    CanonicalSuggestionEvent,
    Manual,
    Setting,
    SettingCanonicalMapping,
)
from app.domain import canonical_settings
from app.domain.errors import CanonicalSettingError
from app.domain.extraction_runs import utc_now_iso
from app.domain.review import effective_name
from app.normalization.canonical_settings import normalize_alias

STATUS_PENDING = "pending"
STATUS_ACCEPTED = "accepted"
STATUS_DISMISSED = "dismissed"
MATCH_EXACT_ALIAS = "exact_alias"
ACTION_GENERATED = "generated"
ACTION_ACCEPTED = "accepted"
ACTION_DISMISSED = "dismissed"
SUGGESTION_ACTOR = "system-suggestion"


@dataclass(frozen=True)
class SuggestionQueueRow:
    suggestion_id: int
    setting_id: int
    effective_name: str
    manual_id: int
    manual_label: str
    vendor_id: str | None
    page_number: int
    canonical_id: str
    canonical_name: str
    matched_alias: str
    match_type: str
    confidence_basis_points: int
    evidence: dict[str, object]
    engine_version: str
    category_id: str | None
    category_name: str | None
    risk_level: str | None
    created_at: str


def refresh_suggestions(session: Session) -> int:
    """Persist new exact-alias suggestions without reviving prior decisions."""
    alias_by_key = {
        alias.normalized_alias: alias for alias in session.scalars(select(CanonicalSettingAlias))
    }
    existing = {
        (row.setting_id, row.canonical_id)
        for row in session.scalars(select(CanonicalMappingSuggestion))
    }
    mapped_setting_ids = set(session.scalars(select(SettingCanonicalMapping.setting_id)))
    settings = session.scalars(
        select(Setting).where(Setting.status == "approved").order_by(Setting.id)
    )
    now = utc_now_iso()
    created = 0
    for setting in settings:
        if setting.id in mapped_setting_ids:
            continue
        alias = alias_by_key.get(normalize_alias(effective_name(setting)))
        if alias is None or (setting.id, alias.canonical_id) in existing:
            continue
        suggestion = CanonicalMappingSuggestion(
            setting_id=setting.id,
            canonical_id=alias.canonical_id,
            match_type=MATCH_EXACT_ALIAS,
            matched_alias=alias.display_alias,
            status=STATUS_PENDING,
            confidence_basis_points=0,
            evidence_json=json.dumps(
                {
                    "signals": ["exact_existing_alias"],
                    "automatic_eligible": False,
                    "reason": "Legacy exact-alias suggestion requires human review.",
                },
                sort_keys=True,
            ),
            engine_version="exact-alias-1.0",
            review_note=None,
            actor=None,
            created_at=now,
            reviewed_at=None,
        )
        session.add(suggestion)
        session.flush()
        session.add(
            CanonicalSuggestionEvent(
                suggestion_id=suggestion.id,
                action=ACTION_GENERATED,
                actor=SUGGESTION_ACTOR,
                detail=json.dumps(
                    {
                        "setting_name": effective_name(setting),
                        "canonical_id": alias.canonical_id,
                        "matched_alias": alias.display_alias,
                        "match_type": MATCH_EXACT_ALIAS,
                    },
                    sort_keys=True,
                ),
                created_at=now,
            )
        )
        created += 1
    session.commit()
    return created


def list_queue(
    session: Session,
    *,
    canonical_id: str = "",
) -> list[SuggestionQueueRow]:
    query = (
        select(
            CanonicalMappingSuggestion,
            Setting,
            Manual,
            CanonicalSetting,
            CanonicalSettingClassification,
            CanonicalSettingCategory,
        )
        .join(Setting, Setting.id == CanonicalMappingSuggestion.setting_id)
        .join(Manual, Manual.id == Setting.manual_id)
        .join(
            CanonicalSetting,
            CanonicalSetting.canonical_id == CanonicalMappingSuggestion.canonical_id,
        )
        .outerjoin(
            CanonicalSettingClassification,
            CanonicalSettingClassification.canonical_id == CanonicalSetting.canonical_id,
        )
        .outerjoin(
            CanonicalSettingCategory,
            CanonicalSettingCategory.category_id == CanonicalSettingClassification.category_id,
        )
        .outerjoin(
            SettingCanonicalMapping,
            SettingCanonicalMapping.setting_id == Setting.id,
        )
        .where(
            CanonicalMappingSuggestion.status == STATUS_PENDING,
            Setting.status == "approved",
            SettingCanonicalMapping.setting_id.is_(None),
        )
        .order_by(
            CanonicalSetting.canonical_name,
            Manual.vendor,
            Manual.id,
            Setting.page_number,
            Setting.id,
        )
    )
    if canonical_id.strip():
        query = query.where(
            CanonicalMappingSuggestion.canonical_id == canonical_id.strip().casefold()
        )
    rows = session.execute(query)
    return [
        SuggestionQueueRow(
            suggestion_id=suggestion.id,
            setting_id=setting.id,
            effective_name=effective_name(setting),
            manual_id=manual.id,
            manual_label=(f"{manual.vendor or ''} {manual.model_name or ''}").strip()
            or manual.original_filename,
            vendor_id=manual.vendor_id,
            page_number=setting.edited_page_number or setting.page_number,
            canonical_id=identity.canonical_id,
            canonical_name=identity.canonical_name,
            matched_alias=suggestion.matched_alias,
            match_type=suggestion.match_type,
            confidence_basis_points=suggestion.confidence_basis_points,
            evidence=_evidence(suggestion.evidence_json),
            engine_version=suggestion.engine_version,
            category_id=classification.category_id if classification else None,
            category_name=category.canonical_name if category else None,
            risk_level=classification.risk_level if classification else None,
            created_at=suggestion.created_at,
        )
        for suggestion, setting, manual, identity, classification, category in rows
    ]


def status_counts(session: Session) -> dict[str, int]:
    rows = session.execute(
        select(
            CanonicalMappingSuggestion.status,
            func.count(CanonicalMappingSuggestion.id),
        ).group_by(CanonicalMappingSuggestion.status)
    )
    result = {
        STATUS_PENDING: 0,
        STATUS_ACCEPTED: 0,
        STATUS_DISMISSED: 0,
    }
    result.update({status: count for status, count in rows})
    return result


def accept_suggestion(
    session: Session,
    suggestion_id: int,
    *,
    note: str = "",
    actor: str | None = None,
) -> CanonicalMappingSuggestion:
    actor = actor or current_actor()
    suggestion = _pending_suggestion(session, suggestion_id)
    setting = session.get(Setting, suggestion.setting_id)
    if setting is None or setting.status != "approved":
        raise CanonicalSettingError("Only suggestions for approved settings can be accepted.")
    note = note.strip() or "Exact alias and source description reviewed."
    _check_review_note(note)
    existing = session.get(SettingCanonicalMapping, setting.id)
    if existing is not None and existing.canonical_id != suggestion.canonical_id:
        raise CanonicalSettingError(
            "The setting is already mapped to a different canonical identity."
        )
    if existing is None:
        canonical_settings.map_setting(
            session,
            setting,
            canonical_id=suggestion.canonical_id,
            note=note,
            actor=actor,
            commit=False,
        )
    now = utc_now_iso()
    suggestion.status = STATUS_ACCEPTED
    suggestion.review_note = note
    suggestion.actor = actor
    suggestion.reviewed_at = now
    session.add(
        CanonicalSuggestionEvent(
            suggestion_id=suggestion.id,
            action=ACTION_ACCEPTED,
            actor=actor,
            detail=json.dumps(
                {
                    "canonical_id": suggestion.canonical_id,
                    "note": note,
                },
                sort_keys=True,
            ),
            created_at=now,
        )
    )
    session.commit()
    return suggestion


def dismiss_suggestion(
    session: Session,
    suggestion_id: int,
    *,
    reason: str,
    actor: str | None = None,
) -> CanonicalMappingSuggestion:
    actor = actor or current_actor()
    suggestion = _pending_suggestion(session, suggestion_id)
    reason = reason.strip()
    if not reason:
        raise CanonicalSettingError("A dismissal reason is required.")
    _check_review_note(reason)
    now = utc_now_iso()
    suggestion.status = STATUS_DISMISSED
    suggestion.review_note = reason
    suggestion.actor = actor
    suggestion.reviewed_at = now
    session.add(
        CanonicalSuggestionEvent(
            suggestion_id=suggestion.id,
            action=ACTION_DISMISSED,
            actor=actor,
            detail=json.dumps({"reason": reason}, sort_keys=True),
            created_at=now,
        )
    )
    session.commit()
    return suggestion


def list_events(
    session: Session,
    suggestion_id: int,
) -> list[CanonicalSuggestionEvent]:
    return list(
        session.scalars(
            select(CanonicalSuggestionEvent)
            .where(CanonicalSuggestionEvent.suggestion_id == suggestion_id)
            .order_by(CanonicalSuggestionEvent.id)
        )
    )


def _pending_suggestion(
    session: Session,
    suggestion_id: int,
) -> CanonicalMappingSuggestion:
    suggestion = session.get(CanonicalMappingSuggestion, suggestion_id)
    if suggestion is None:
        raise CanonicalSettingError("Canonical mapping suggestion not found.")
    if suggestion.status != STATUS_PENDING:
        raise CanonicalSettingError("This suggestion has already been reviewed.")
    return suggestion


def _check_review_note(value: str) -> None:
    if len(value) > config.MAX_CANONICAL_MAPPING_NOTE_LENGTH:
        raise CanonicalSettingError("Suggestion review note is too long.")


def _evidence(value: str) -> dict[str, object]:
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
