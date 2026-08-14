"""Versioned API for evidence-grounded, reviewer-controlled recommendations."""

from fastapi import APIRouter, HTTPException, Query, status

from app.api.v1.deps import SessionDep
from app.api.v1.schemas import (
    ErrorOut,
    RecommendationClaimDetailOut,
    RecommendationClaimIn,
    RecommendationClaimSummaryOut,
    ReviewRecommendationClaimIn,
)
from app.domain import recommendation_claims as domain

router = APIRouter(tags=["recommendation claims"])

_ERRORS = {
    400: {"model": ErrorOut, "description": "Recommendation validation failed"},
    404: {"model": ErrorOut, "description": "Recommendation not found"},
}


@router.get("/recommendation-claims")
def list_recommendation_claims(
    session: SessionDep,
    status_filter: str = Query(default="", alias="status"),
) -> list[RecommendationClaimSummaryOut]:
    try:
        rows = domain.list_claims(session, status=status_filter)
    except domain.RecommendationClaimError as error:
        raise HTTPException(status_code=400, detail=error.message) from None
    return [RecommendationClaimSummaryOut.from_overview(row) for row in rows]


@router.post(
    "/recommendation-claims",
    status_code=status.HTTP_201_CREATED,
    responses=_ERRORS,
)
def create_recommendation_claim(
    body: RecommendationClaimIn,
    session: SessionDep,
) -> RecommendationClaimDetailOut:
    try:
        claim = domain.create_claim(session, **body.domain_values())
    except domain.RecommendationClaimError as error:
        raise HTTPException(status_code=400, detail=error.message) from None
    return _detail_or_error(session, claim.id)


@router.get("/recommendation-claims/{claim_id}", responses={404: _ERRORS[404]})
def recommendation_claim_detail(
    claim_id: int,
    session: SessionDep,
) -> RecommendationClaimDetailOut:
    return _detail_or_error(session, claim_id)


@router.put("/recommendation-claims/{claim_id}", responses=_ERRORS)
def edit_recommendation_claim(
    claim_id: int,
    body: RecommendationClaimIn,
    session: SessionDep,
) -> RecommendationClaimDetailOut:
    claim = domain.get_claim(session, claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail="Recommendation claim not found.")
    try:
        domain.edit_claim(session, claim, **body.domain_values())
    except domain.RecommendationClaimError as error:
        raise HTTPException(status_code=400, detail=error.message) from None
    return _detail_or_error(session, claim_id)


@router.post("/recommendation-claims/{claim_id}/review", responses=_ERRORS)
def review_recommendation_claim(
    claim_id: int,
    body: ReviewRecommendationClaimIn,
    session: SessionDep,
) -> RecommendationClaimDetailOut:
    claim = domain.get_claim(session, claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail="Recommendation claim not found.")
    try:
        domain.review_claim(session, claim, decision=body.decision, note=body.note)
    except domain.RecommendationClaimError as error:
        raise HTTPException(status_code=400, detail=error.message) from None
    return _detail_or_error(session, claim_id)


def _detail_or_error(session, claim_id: int) -> RecommendationClaimDetailOut:
    detail = domain.get_detail(session, claim_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Recommendation claim not found.")
    return RecommendationClaimDetailOut.from_detail(detail)
