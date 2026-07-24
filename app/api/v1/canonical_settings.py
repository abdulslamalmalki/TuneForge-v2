"""Canonical BIOS-setting catalog and reviewer-controlled mappings."""

from fastapi import APIRouter, HTTPException

from app.api.v1.deps import SessionDep
from app.api.v1.schemas import (
    CanonicalCategoryOut,
    CanonicalSettingDetailOut,
    CanonicalSettingSummaryOut,
    CanonicalSuggestionDecisionOut,
    CanonicalSuggestionQueueOut,
    CanonicalSuggestionRefreshOut,
    CanonicalSuggestionReviewIn,
    ClassifyCanonicalSettingIn,
    CreateCanonicalSettingIn,
    ErrorOut,
    MapCanonicalSettingIn,
    SettingDetailOut,
    UnmapCanonicalSettingIn,
)
from app.domain import canonical_settings as canonical_domain
from app.domain import canonical_suggestions as suggestion_domain
from app.domain import review as review_domain
from app.domain.errors import CanonicalSettingError

router = APIRouter(tags=["canonical settings"])

_ERRORS = {
    400: {"model": ErrorOut, "description": "Canonical-setting validation failed"},
    404: {"model": ErrorOut, "description": "Resource not found"},
}


@router.get(
    "/canonical-settings",
    summary="List canonical setting identities",
)
def list_canonical_settings(session: SessionDep) -> list[CanonicalSettingSummaryOut]:
    return [
        CanonicalSettingSummaryOut.from_row(row) for row in canonical_domain.list_catalog(session)
    ]


@router.get(
    "/canonical-categories",
    summary="List stable canonical setting categories",
)
def list_canonical_categories(session: SessionDep) -> list[CanonicalCategoryOut]:
    return [
        CanonicalCategoryOut.model_validate(row)
        for row in canonical_domain.list_categories(session)
    ]


@router.get(
    "/canonical-suggestions",
    summary="List pending exact-alias mapping suggestions",
)
def list_canonical_suggestions(
    session: SessionDep,
    canonical_id: str = "",
) -> list[CanonicalSuggestionQueueOut]:
    return [
        CanonicalSuggestionQueueOut.from_row(row)
        for row in suggestion_domain.list_queue(
            session,
            canonical_id=canonical_id,
        )
    ]


@router.post(
    "/canonical-suggestions/refresh",
    summary="Generate new exact-alias suggestions",
)
def refresh_canonical_suggestions(
    session: SessionDep,
) -> CanonicalSuggestionRefreshOut:
    created = suggestion_domain.refresh_suggestions(session)
    return CanonicalSuggestionRefreshOut(
        created=created,
        counts=suggestion_domain.status_counts(session),
    )


@router.post(
    "/canonical-suggestions/{suggestion_id}/accept",
    summary="Accept a suggestion and create an audited mapping",
    responses=_ERRORS,
)
def accept_canonical_suggestion(
    suggestion_id: int,
    body: CanonicalSuggestionReviewIn,
    session: SessionDep,
) -> CanonicalSuggestionDecisionOut:
    try:
        row = suggestion_domain.accept_suggestion(
            session,
            suggestion_id,
            note=body.note,
        )
    except CanonicalSettingError as error:
        raise HTTPException(status_code=400, detail=error.message) from None
    return CanonicalSuggestionDecisionOut.model_validate(row)


@router.post(
    "/canonical-suggestions/{suggestion_id}/dismiss",
    summary="Dismiss a suggestion with an audited reason",
    responses=_ERRORS,
)
def dismiss_canonical_suggestion(
    suggestion_id: int,
    body: CanonicalSuggestionReviewIn,
    session: SessionDep,
) -> CanonicalSuggestionDecisionOut:
    try:
        row = suggestion_domain.dismiss_suggestion(
            session,
            suggestion_id,
            reason=body.reason,
        )
    except CanonicalSettingError as error:
        raise HTTPException(status_code=400, detail=error.message) from None
    return CanonicalSuggestionDecisionOut.model_validate(row)


@router.post(
    "/canonical-settings",
    summary="Create a canonical setting identity",
    responses=_ERRORS,
)
def create_canonical_setting(
    body: CreateCanonicalSettingIn,
    session: SessionDep,
) -> CanonicalSettingDetailOut:
    try:
        identity = canonical_domain.create_identity(
            session,
            name=body.name,
            description=body.description,
            aliases=tuple(body.aliases),
            canonical_id=body.canonical_id,
        )
    except CanonicalSettingError as error:
        raise HTTPException(status_code=400, detail=error.message) from None
    detail = canonical_domain.get_detail(session, identity.canonical_id)
    if detail is None:
        raise RuntimeError("Created canonical identity could not be reloaded.")
    return CanonicalSettingDetailOut.from_detail(detail)


@router.get(
    "/canonical-settings/{canonical_id}",
    summary="Get a canonical identity and all mapped source settings",
    responses={404: {"model": ErrorOut, "description": "Canonical setting not found"}},
)
def canonical_setting_detail(
    canonical_id: str,
    session: SessionDep,
) -> CanonicalSettingDetailOut:
    detail = canonical_domain.get_detail(session, canonical_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Canonical setting not found.")
    return CanonicalSettingDetailOut.from_detail(detail)


@router.put(
    "/canonical-settings/{canonical_id}/classification",
    summary="Classify a canonical identity with an audited rationale",
    responses=_ERRORS,
)
def classify_canonical_setting(
    canonical_id: str,
    body: ClassifyCanonicalSettingIn,
    session: SessionDep,
) -> CanonicalSettingDetailOut:
    identity = canonical_domain.get_detail(session, canonical_id)
    if identity is None:
        raise HTTPException(status_code=404, detail="Canonical setting not found.")
    try:
        canonical_domain.classify_identity(
            session,
            identity.identity,
            category_id=body.category_id,
            risk_level=body.risk_level,
            risk_rationale=body.risk_rationale,
        )
    except CanonicalSettingError as error:
        raise HTTPException(status_code=400, detail=error.message) from None
    detail = canonical_domain.get_detail(session, canonical_id)
    if detail is None:
        raise RuntimeError("Classified canonical identity could not be reloaded.")
    return CanonicalSettingDetailOut.from_detail(detail)


@router.put(
    "/settings/{setting_id}/canonical",
    summary="Map an approved setting to a canonical identity",
    responses=_ERRORS,
)
def map_setting(
    setting_id: int,
    body: MapCanonicalSettingIn,
    session: SessionDep,
) -> SettingDetailOut:
    setting = review_domain.get_setting(session, setting_id)
    if setting is None:
        raise HTTPException(status_code=404, detail="Setting not found.")
    try:
        canonical_domain.map_setting(
            session,
            setting,
            canonical_id=body.canonical_id,
            note=body.note,
        )
    except CanonicalSettingError as error:
        raise HTTPException(status_code=400, detail=error.message) from None
    return _setting_detail(session, setting)


@router.post(
    "/settings/{setting_id}/canonical/unmap",
    summary="Remove a canonical mapping with an audited reason",
    responses=_ERRORS,
)
def unmap_setting(
    setting_id: int,
    body: UnmapCanonicalSettingIn,
    session: SessionDep,
) -> SettingDetailOut:
    setting = review_domain.get_setting(session, setting_id)
    if setting is None:
        raise HTTPException(status_code=404, detail="Setting not found.")
    try:
        canonical_domain.unmap_setting(session, setting, reason=body.reason)
    except CanonicalSettingError as error:
        raise HTTPException(status_code=400, detail=error.message) from None
    return _setting_detail(session, setting)


def _setting_detail(session, setting) -> SettingDetailOut:
    return SettingDetailOut.from_setting(
        setting,
        review_domain.list_review_events(session, setting.id),
        canonical_domain.get_mapping(session, setting.id),
        canonical_domain.suggest_for_setting(session, setting),
        canonical_domain.list_mapping_events(session, setting.id),
    )
