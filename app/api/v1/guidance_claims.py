"""Versioned API for reviewer-controlled, evidence-grounded guidance claims."""

from fastapi import APIRouter, HTTPException, Query, status

from app.api.v1.deps import SessionDep
from app.api.v1.schemas import (
    CreateGuidanceClaimIn,
    EditGuidanceClaimIn,
    ErrorOut,
    GuidanceClaimDetailOut,
    GuidanceClaimSummaryOut,
    ReviewGuidanceClaimIn,
)
from app.domain import guidance_claims as claim_domain
from app.domain.errors import GuidanceClaimError

router = APIRouter(tags=["guidance claims"])

_ERRORS = {
    400: {"model": ErrorOut, "description": "Guidance claim validation failed"},
    404: {"model": ErrorOut, "description": "Guidance claim not found"},
}


@router.get("/guidance-claims", summary="List guidance claims and review states")
def list_guidance_claims(
    session: SessionDep,
    status_filter: str = Query(default="", alias="status"),
) -> list[GuidanceClaimSummaryOut]:
    try:
        rows = claim_domain.list_claims(session, status=status_filter)
    except GuidanceClaimError as error:
        raise HTTPException(status_code=400, detail=error.message) from None
    return [GuidanceClaimSummaryOut.from_overview(row) for row in rows]


@router.post(
    "/guidance-claims",
    summary="Create a draft guidance claim from approved evidence",
    status_code=status.HTTP_201_CREATED,
    responses=_ERRORS,
)
def create_guidance_claim(
    body: CreateGuidanceClaimIn,
    session: SessionDep,
) -> GuidanceClaimDetailOut:
    try:
        claim = claim_domain.create_claim(
            session,
            claim_text=body.claim_text,
            claim_type=body.claim_type,
            passage_ids=body.passage_ids,
            source_setting_ids=body.source_setting_ids,
            canonical_ids=body.canonical_ids,
            applicability_scope=body.applicability_scope,
            cpu_model_id=body.cpu_model_id,
            applicability_targets=(
                tuple(target.to_domain() for target in body.applicability_targets)
                if body.applicability_targets
                else None
            ),
            bios_scopes=(
                tuple(scope.to_domain() for scope in body.bios_scopes)
                if body.bios_scopes
                else None
            ),
        )
    except GuidanceClaimError as error:
        raise HTTPException(status_code=400, detail=error.message) from None
    return _detail_or_error(session, claim.id)


@router.get(
    "/guidance-claims/{claim_id}",
    summary="Get a guidance claim with citations, settings, and audit history",
    responses={404: _ERRORS[404]},
)
def guidance_claim_detail(claim_id: int, session: SessionDep) -> GuidanceClaimDetailOut:
    return _detail_or_error(session, claim_id)


@router.put(
    "/guidance-claims/{claim_id}",
    summary="Edit a draft guidance claim and its links",
    responses=_ERRORS,
)
def edit_guidance_claim(
    claim_id: int,
    body: EditGuidanceClaimIn,
    session: SessionDep,
) -> GuidanceClaimDetailOut:
    claim = claim_domain.get_claim(session, claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail="Guidance claim not found.")
    try:
        claim_domain.edit_claim(
            session,
            claim,
            claim_text=body.claim_text,
            claim_type=body.claim_type,
            passage_ids=body.passage_ids,
            source_setting_ids=body.source_setting_ids,
            canonical_ids=body.canonical_ids,
            applicability_scope=body.applicability_scope,
            cpu_model_id=body.cpu_model_id,
            applicability_targets=(
                tuple(target.to_domain() for target in body.applicability_targets)
                if body.applicability_targets
                else None
            ),
            bios_scopes=(
                tuple(scope.to_domain() for scope in body.bios_scopes)
                if body.bios_scopes
                else None
            ),
        )
    except GuidanceClaimError as error:
        raise HTTPException(status_code=400, detail=error.message) from None
    return _detail_or_error(session, claim_id)


@router.post(
    "/guidance-claims/{claim_id}/review",
    summary="Approve or reject a draft guidance claim",
    responses=_ERRORS,
)
def review_guidance_claim(
    claim_id: int,
    body: ReviewGuidanceClaimIn,
    session: SessionDep,
) -> GuidanceClaimDetailOut:
    claim = claim_domain.get_claim(session, claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail="Guidance claim not found.")
    try:
        claim_domain.review_claim(
            session,
            claim,
            decision=body.decision,
            note=body.note,
        )
    except GuidanceClaimError as error:
        raise HTTPException(status_code=400, detail=error.message) from None
    return _detail_or_error(session, claim_id)


def _detail_or_error(session, claim_id: int) -> GuidanceClaimDetailOut:
    detail = claim_domain.get_detail(session, claim_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Guidance claim not found.")
    return GuidanceClaimDetailOut.from_detail(detail)
