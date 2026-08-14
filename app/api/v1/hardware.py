"""Stable hardware identities and read-only exact-system context."""

from fastapi import APIRouter, HTTPException, Query

from app.api.v1.deps import SessionDep
from app.api.v1.schemas import (
    Am5CoverageReportOut,
    Am5QuestionAcceptanceReportOut,
    ErrorOut,
    SystemContextOut,
)
from app.domain import am5_coverage, am5_question_acceptance, system_context
from app.domain.errors import SystemContextError
from app.normalization.hardware import contract

router = APIRouter(prefix="/hardware", tags=["hardware"])


@router.get(
    "",
    summary="Get the hardware identity contract",
    description=(
        "Stable platform, chipset-family, chipset, motherboard-model, and "
        "applicability-scope IDs used by manuals and search filters."
    ),
)
def hardware_contract() -> dict[str, object]:
    return contract()


@router.get(
    "/am5-coverage",
    summary="Audit reviewed AM5 pilot coverage and expansion gates",
)
def am5_coverage_report(session: SessionDep) -> Am5CoverageReportOut:
    return Am5CoverageReportOut.from_report(am5_coverage.build_report(session))


@router.get(
    "/am5-question-acceptance",
    summary="Evaluate the fixed AM5 product question set",
)
def am5_question_acceptance_report(
    session: SessionDep,
) -> Am5QuestionAcceptanceReportOut:
    return Am5QuestionAcceptanceReportOut.from_report(am5_question_acceptance.build_report(session))


@router.get(
    "/system-context",
    summary="Resolve reviewed hardware IDs for the AMD AM5 product pilot",
    responses={400: {"model": ErrorOut, "description": "Hardware context is invalid"}},
)
def resolve_system_context(
    session: SessionDep,
    motherboard_model_id: str = Query(min_length=1),
    cpu_model_id: str = Query(min_length=1),
    bios_version: str = "",
    use_case: str = "",
) -> SystemContextOut:
    try:
        context = system_context.resolve(
            session,
            motherboard_model_id=motherboard_model_id,
            cpu_model_id=cpu_model_id,
            bios_version=bios_version,
            use_case=use_case,
        )
    except SystemContextError as error:
        raise HTTPException(status_code=400, detail=error.message) from None
    return SystemContextOut.from_context(context)
