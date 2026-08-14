"""Reviewer API for detecting and resolving approved-claim disagreements."""

from fastapi import APIRouter, HTTPException, Query, status

from app.api.v1.deps import SessionDep
from app.api.v1.schemas import (
    GuidanceConflictDetailOut,
    GuidanceConflictOverviewOut,
    GuidanceConflictScanOut,
    RegisterGuidanceConflictIn,
    ReviewGuidanceConflictIn,
)
from app.domain import guidance_conflicts
from app.domain.errors import GuidanceClaimError

router = APIRouter(tags=["guidance conflicts"])


@router.get("/guidance-conflicts")
def list_guidance_conflicts(
    session: SessionDep,
    status_filter: str = Query(default="", alias="status"),
) -> list[GuidanceConflictOverviewOut]:
    try:
        rows = guidance_conflicts.list_conflicts(session, status=status_filter)
    except GuidanceClaimError as error:
        raise HTTPException(status_code=400, detail=error.message) from None
    return [GuidanceConflictOverviewOut.from_overview(row) for row in rows]


@router.post(
    "/guidance-conflicts",
    status_code=status.HTTP_201_CREATED,
    summary="Register a possible conflict between two approved claims",
)
def register_guidance_conflict(
    body: RegisterGuidanceConflictIn,
    session: SessionDep,
) -> GuidanceConflictDetailOut:
    try:
        row = guidance_conflicts.register_conflict(
            session,
            left_claim_id=body.left_claim_id,
            right_claim_id=body.right_claim_id,
            detection_reason=body.detection_reason,
        )
    except GuidanceClaimError as error:
        raise HTTPException(status_code=400, detail=error.message) from None
    return _detail(session, row.id)


@router.post("/guidance-conflicts/scan")
def scan_guidance_conflicts(session: SessionDep) -> GuidanceConflictScanOut:
    return GuidanceConflictScanOut.from_report(
        guidance_conflicts.scan_approved_claims(session)
    )


@router.get("/guidance-conflicts/{conflict_id}")
def guidance_conflict_detail(
    conflict_id: int,
    session: SessionDep,
) -> GuidanceConflictDetailOut:
    return _detail(session, conflict_id)


@router.post("/guidance-conflicts/{conflict_id}/review")
def review_guidance_conflict(
    conflict_id: int,
    body: ReviewGuidanceConflictIn,
    session: SessionDep,
) -> GuidanceConflictDetailOut:
    row = session.get(guidance_conflicts.GuidanceClaimConflict, conflict_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Knowledge conflict not found.")
    try:
        guidance_conflicts.review_conflict(
            session,
            row,
            decision=body.decision,
            note=body.note,
        )
    except GuidanceClaimError as error:
        raise HTTPException(status_code=400, detail=error.message) from None
    return _detail(session, conflict_id)


def _detail(session, conflict_id: int) -> GuidanceConflictDetailOut:
    detail = guidance_conflicts.get_detail(session, conflict_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Knowledge conflict not found.")
    return GuidanceConflictDetailOut.from_detail(detail)
