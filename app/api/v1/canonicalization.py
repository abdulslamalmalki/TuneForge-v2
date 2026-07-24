"""Protected canonicalization backlog and deterministic reviewer API."""

from fastapi import APIRouter, HTTPException, Query

from app.api.v1.canonicalization_schemas import (
    CanonicalIdentityStatusIn,
    CanonicalIdentityStatusOut,
    CanonicalizationBacklogOut,
    CanonicalizationEntryOut,
    CanonicalizationReverseIn,
    CanonicalizationReverseOut,
    CanonicalizationRunReportOut,
    IdentityProposalOut,
    IdentityProposalReviewIn,
    MenuContextOut,
    MenuContextReviewIn,
    MenuContextRevokeIn,
    ProposedCanonicalGroupOut,
)
from app.api.v1.deps import SessionDep
from app.db.models import CanonicalSetting
from app.domain import canonical_identity_review, canonical_settings, canonicalization
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


@router.get(
    "/canonicalization/identity-proposals",
    summary="List durable canonical identity proposals",
)
def list_identity_proposals(
    session: SessionDep,
    status: str = "",
) -> list[IdentityProposalOut]:
    try:
        values = canonical_identity_review.list_identity_proposals(
            session,
            status=status,
        )
    except CanonicalSettingError as error:
        raise HTTPException(status_code=400, detail=error.message) from None
    return [IdentityProposalOut.from_domain(value) for value in values]


@router.put(
    "/canonicalization/settings/{setting_id}/menu-context",
    summary="Approve or revise cited BIOS menu-path evidence",
)
def review_menu_context(
    setting_id: int,
    body: MenuContextReviewIn,
    session: SessionDep,
) -> MenuContextOut:
    try:
        canonical_identity_review.review_menu_context(
            session,
            setting_id,
            menu_path=body.menu_path,
            source_page_number=body.source_page_number,
            evidence_excerpt=body.evidence_excerpt,
            supporting_citations=tuple(
                (citation.page_number, citation.evidence_excerpt)
                for citation in body.supporting_citations
            ),
            note=body.note,
        )
    except CanonicalSettingError as error:
        raise HTTPException(status_code=400, detail=error.message) from None
    view = canonical_identity_review.get_menu_context(session, setting_id)
    if view is None:
        raise RuntimeError("Reviewed menu context could not be reloaded.")
    return MenuContextOut.from_domain(view)


@router.post(
    "/canonicalization/settings/{setting_id}/menu-context/revoke",
    summary="Revoke menu-path evidence without deleting its audit history",
)
def revoke_menu_context(
    setting_id: int,
    body: MenuContextRevokeIn,
    session: SessionDep,
) -> MenuContextOut:
    try:
        canonical_identity_review.revoke_menu_context(
            session,
            setting_id,
            reason=body.reason,
        )
    except CanonicalSettingError as error:
        raise HTTPException(status_code=400, detail=error.message) from None
    view = canonical_identity_review.get_menu_context(session, setting_id)
    if view is None:
        raise RuntimeError("Revoked menu context could not be reloaded.")
    return MenuContextOut.from_domain(view)


@router.post(
    "/canonicalization/identity-proposals/{proposal_id}/approve",
    summary="Approve a fully evidenced canonical identity proposal",
)
def approve_identity_proposal(
    proposal_id: int,
    body: IdentityProposalReviewIn,
    session: SessionDep,
) -> IdentityProposalOut:
    try:
        canonical_identity_review.approve_identity_proposal(
            session,
            proposal_id,
            note=body.note,
        )
    except CanonicalSettingError as error:
        raise HTTPException(status_code=400, detail=error.message) from None
    value = canonical_identity_review.get_identity_proposal(session, proposal_id)
    if value is None:
        raise RuntimeError("Approved identity proposal could not be reloaded.")
    return IdentityProposalOut.from_domain(value)


@router.post(
    "/canonicalization/identity-proposals/{proposal_id}/reject",
    summary="Reject a canonical identity proposal with an audited reason",
)
def reject_identity_proposal(
    proposal_id: int,
    body: IdentityProposalReviewIn,
    session: SessionDep,
) -> IdentityProposalOut:
    try:
        canonical_identity_review.reject_identity_proposal(
            session,
            proposal_id,
            reason=body.reason,
        )
    except CanonicalSettingError as error:
        raise HTTPException(status_code=400, detail=error.message) from None
    value = canonical_identity_review.get_identity_proposal(session, proposal_id)
    if value is None:
        raise RuntimeError("Rejected identity proposal could not be reloaded.")
    return IdentityProposalOut.from_domain(value)


@router.post(
    "/canonicalization/identity-proposals/{proposal_id}/reopen",
    summary="Reopen a rejected identity proposal with an audited reason",
)
def reopen_identity_proposal(
    proposal_id: int,
    body: IdentityProposalReviewIn,
    session: SessionDep,
) -> IdentityProposalOut:
    try:
        canonical_identity_review.reopen_identity_proposal(
            session,
            proposal_id,
            reason=body.reason,
        )
    except CanonicalSettingError as error:
        raise HTTPException(status_code=400, detail=error.message) from None
    value = canonical_identity_review.get_identity_proposal(session, proposal_id)
    if value is None:
        raise RuntimeError("Reopened identity proposal could not be reloaded.")
    return IdentityProposalOut.from_domain(value)


@router.post(
    "/canonicalization/identities/{canonical_id}/deprecate",
    summary="Deprecate a canonical identity without deleting it",
)
def deprecate_identity(
    canonical_id: str,
    body: CanonicalIdentityStatusIn,
    session: SessionDep,
) -> CanonicalIdentityStatusOut:
    return _set_identity_status(
        session,
        canonical_id,
        canonical_settings.IDENTITY_DEPRECATED,
        body.reason,
    )


@router.post(
    "/canonicalization/identities/{canonical_id}/reactivate",
    summary="Reactivate a deprecated canonical identity",
)
def reactivate_identity(
    canonical_id: str,
    body: CanonicalIdentityStatusIn,
    session: SessionDep,
) -> CanonicalIdentityStatusOut:
    return _set_identity_status(
        session,
        canonical_id,
        canonical_settings.IDENTITY_ACTIVE,
        body.reason,
    )


def _set_identity_status(
    session: SessionDep,
    canonical_id: str,
    status: str,
    reason: str,
) -> CanonicalIdentityStatusOut:
    identity = session.get(CanonicalSetting, canonical_id)
    if identity is None:
        raise HTTPException(status_code=404, detail="Canonical setting not found.")
    try:
        canonical_settings.set_identity_status(
            session,
            identity,
            status=status,
            reason=reason,
        )
    except CanonicalSettingError as error:
        raise HTTPException(status_code=400, detail=error.message) from None
    return CanonicalIdentityStatusOut(
        canonical_id=identity.canonical_id,
        status=identity.status,
    )
