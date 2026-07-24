"""Extraction acceptance metrics and missed-setting ground truth."""

from fastapi import APIRouter, HTTPException

from app.api.v1.deps import SessionDep
from app.api.v1.schemas import (
    AcceptanceDetailOut,
    AddMissedSettingIn,
    CertifyAcceptanceIn,
    DismissMissedSettingIn,
    ErrorOut,
    PageReferenceReviewIn,
)
from app.domain import acceptance as acceptance_domain
from app.domain import manuals as manuals_domain
from app.domain.errors import AcceptanceError

router = APIRouter(prefix="/manuals/{manual_id}", tags=["manuals"])

_ERROR_RESPONSES = {
    400: {"model": ErrorOut, "description": "Acceptance validation failed"},
    404: {"model": ErrorOut, "description": "Manual not found"},
}


@router.get(
    "/acceptance",
    summary="Get extraction acceptance report",
    description=(
        "Review completion and provisional precision/recall for one parser run,"
        " plus curator-recorded settings the parser missed."
    ),
    responses={404: _ERROR_RESPONSES[404]},
)
def acceptance_report(manual_id: int, session: SessionDep) -> AcceptanceDetailOut:
    manual = _manual_or_404(session, manual_id)
    return _detail(session, manual)


@router.post(
    "/settings/{setting_id}/page-reference",
    summary="Verify or correct an approved setting's PDF page",
    responses=_ERROR_RESPONSES,
)
def review_page_reference(
    manual_id: int,
    setting_id: int,
    body: PageReferenceReviewIn,
    session: SessionDep,
) -> AcceptanceDetailOut:
    manual = _manual_or_404(session, manual_id)
    try:
        acceptance_domain.review_page_reference(
            session, manual, setting_id, page_number=body.page_number
        )
    except AcceptanceError as error:
        raise HTTPException(status_code=400, detail=error.message) from None
    return _detail(session, manual)


@router.post(
    "/missed-settings",
    status_code=201,
    summary="Record a missed setting",
    responses=_ERROR_RESPONSES,
)
def add_missed_setting(
    manual_id: int, body: AddMissedSettingIn, session: SessionDep
) -> AcceptanceDetailOut:
    manual = _manual_or_404(session, manual_id)
    try:
        acceptance_domain.add_missed_setting(
            session,
            manual,
            name=body.name,
            page_number=body.page_number,
            description=body.description,
            note=body.note,
        )
    except AcceptanceError as error:
        raise HTTPException(status_code=400, detail=error.message) from None
    return _detail(session, manual)


@router.post(
    "/missed-settings/{missed_setting_id}/dismiss",
    summary="Dismiss a missed-setting record",
    responses=_ERROR_RESPONSES,
)
def dismiss_missed_setting(
    manual_id: int,
    missed_setting_id: int,
    body: DismissMissedSettingIn,
    session: SessionDep,
) -> AcceptanceDetailOut:
    manual = _manual_or_404(session, manual_id)
    try:
        acceptance_domain.dismiss_missed_setting(
            session, manual, missed_setting_id, reason=body.reason
        )
    except AcceptanceError as error:
        raise HTTPException(status_code=400, detail=error.message) from None
    return _detail(session, manual)


@router.post(
    "/acceptance/certify",
    summary="Certify the manual ground-truth review",
    description=(
        "Certification requires successful extraction, complete provenance,"
        " no remaining draft candidates, and page-reference review for every"
        " approved candidate. Later review or source changes"
        " invalidate it automatically."
    ),
    responses=_ERROR_RESPONSES,
)
def certify_acceptance(
    manual_id: int, body: CertifyAcceptanceIn, session: SessionDep
) -> AcceptanceDetailOut:
    manual = _manual_or_404(session, manual_id)
    try:
        acceptance_domain.certify_ground_truth(session, manual, notes=body.notes)
    except AcceptanceError as error:
        raise HTTPException(status_code=400, detail=error.message) from None
    return _detail(session, manual)


def _manual_or_404(session, manual_id: int):
    manual = manuals_domain.get_manual(session, manual_id)
    if manual is None:
        raise HTTPException(status_code=404, detail="Manual not found.")
    return manual


def _detail(session, manual) -> AcceptanceDetailOut:
    report = acceptance_domain.build_report(session, manual)
    missed = acceptance_domain.list_missed_settings(session, manual.id)
    page_references = acceptance_domain.list_page_references(session, manual.id)
    return AcceptanceDetailOut.from_domain(report, missed, page_references)
