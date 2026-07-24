"""Protected canonicalization backlog and deterministic reviewer API."""

from fastapi import APIRouter, HTTPException, Query

from app.api.v1.canonicalization_schemas import (
    CanonicalizationBacklogOut,
    CanonicalizationEntryOut,
    CanonicalizationReverseIn,
    CanonicalizationReverseOut,
    CanonicalizationRunReportOut,
    ProposedCanonicalGroupOut,
)
from app.api.v1.deps import SessionDep
from app.domain import canonicalization
from app.domain.errors import CanonicalSettingError

router = APIRouter(tags=["canonical settings"])


@router.get(
    "/canonicalization/backlog",
    summary="List approved source settings requiring canonical review",
)
def canonicalization_backlog(
    session: SessionDep,
    query: str = "",
    vendor_id: str = "",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> CanonicalizationBacklogOut:
    result = canonicalization.backlog_page(
        session,
        query=query,
        vendor_id=vendor_id,
        page=page,
        page_size=page_size,
    )
    return CanonicalizationBacklogOut(
        items=[CanonicalizationEntryOut.from_domain(item) for item in result.items],
        total=result.total,
        page=result.page,
        page_size=result.page_size,
        pages=result.pages,
        proposed_groups=[
            ProposedCanonicalGroupOut.from_domain(item) for item in result.proposed_groups
        ],
    )


@router.post(
    "/canonicalization/review",
    summary="Run the conservative automatic canonicalization reviewer",
)
def run_canonicalization_reviewer(
    session: SessionDep,
) -> CanonicalizationRunReportOut:
    return CanonicalizationRunReportOut.from_domain(
        canonicalization.run_reviewer(session)
    )


@router.post(
    "/canonicalization/settings/{setting_id}/reverse",
    summary="Reverse an automatic canonical mapping",
)
def reverse_automatic_mapping(
    setting_id: int,
    body: CanonicalizationReverseIn,
    session: SessionDep,
) -> CanonicalizationReverseOut:
    try:
        canonicalization.reverse_automatic_mapping(
            session,
            setting_id,
            reason=body.reason,
        )
    except CanonicalSettingError as error:
        raise HTTPException(status_code=400, detail=error.message) from None
    return CanonicalizationReverseOut(setting_id=setting_id, reversed=True)
