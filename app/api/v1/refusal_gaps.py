"""Reviewer worklist for refusals and official referral-pointer registry."""

from fastapi import APIRouter, HTTPException, Query

from app.api.v1.deps import SessionDep
from app.api.v1.schemas import (
    ErrorOut,
    LookupGapOut,
    LookupGapSummaryOut,
    ReferralPointerCreateIn,
    ReferralPointerOut,
    ReferralPointerRetireIn,
)
from app.domain import refusal_gaps
from app.domain.errors import ReferralPointerError

router = APIRouter(tags=["refusal gaps"])


@router.get(
    "/refusal-gaps",
    summary="List demand-ranked groups of citation-first refusals",
)
def list_refusal_gap_worklist(
    session: SessionDep,
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[LookupGapSummaryOut]:
    return [
        LookupGapSummaryOut.from_summary(row)
        for row in refusal_gaps.list_gap_worklist(session, limit=limit)
    ]


@router.get(
    "/refusal-gaps/events",
    summary="List recent immutable refusal events",
)
def list_refusal_gap_events(
    session: SessionDep,
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[LookupGapOut]:
    return [
        LookupGapOut.from_gap(row) for row in refusal_gaps.list_lookup_gaps(session, limit=limit)
    ]


@router.get(
    "/referral-pointers",
    summary="List reviewer-registered official referral pointers",
)
def list_referral_pointers(
    session: SessionDep,
    active_only: bool = False,
) -> list[ReferralPointerOut]:
    return [
        ReferralPointerOut.from_view(row)
        for row in refusal_gaps.list_referral_pointers(session, active_only=active_only)
    ]


@router.post(
    "/referral-pointers",
    summary="Register an official referral pointer after reviewer verification",
    responses={400: {"model": ErrorOut}},
)
def register_referral_pointer(
    payload: ReferralPointerCreateIn,
    session: SessionDep,
) -> ReferralPointerOut:
    try:
        pointer = refusal_gaps.register_referral_pointer(
            session,
            publisher_id=payload.publisher_id,
            title=payload.title,
            source_url=payload.source_url,
            document_id=payload.document_id,
            document_version=payload.document_version,
            canonical_ids=payload.canonical_ids,
            review_note=payload.review_note,
        )
    except ReferralPointerError as error:
        raise HTTPException(status_code=400, detail=error.message) from None
    return ReferralPointerOut.from_view(pointer)


@router.post(
    "/referral-pointers/{pointer_id}/retire",
    summary="Retire an official referral without deleting its audit history",
    responses={400: {"model": ErrorOut}},
)
def retire_referral_pointer(
    pointer_id: int,
    payload: ReferralPointerRetireIn,
    session: SessionDep,
) -> ReferralPointerOut:
    try:
        pointer = refusal_gaps.retire_referral_pointer(
            session,
            pointer_id,
            review_note=payload.review_note,
        )
    except ReferralPointerError as error:
        raise HTTPException(status_code=400, detail=error.message) from None
    return ReferralPointerOut.from_view(pointer)
