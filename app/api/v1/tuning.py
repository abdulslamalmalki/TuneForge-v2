"""Versioned vocabulary for trusted tuning guidance."""

from fastapi import APIRouter

from app.normalization.tuning import tuning_contract

router = APIRouter(tags=["tuning"])


@router.get(
    "/tuning-contract",
    summary="Get the trusted-guidance tuning vocabulary",
)
def get_tuning_contract() -> dict[str, object]:
    return tuning_contract()
