"""Read-only citation-first guidance API."""

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from app.api.v1.deps import SessionDep
from app.api.v1.schemas import (
    CitationFirstExplanationOut,
    ErrorOut,
    GuidanceCoverageOut,
    ReferralPointerOut,
    RefusalErrorOut,
)
from app.domain import guidance_read, refusal_gaps
from app.domain import system_context as system_context_domain
from app.domain.errors import GuidanceReadError, SystemContextError

router = APIRouter(tags=["citation-first guidance"])


@router.get(
    "/guidance/explain",
    response_model=CitationFirstExplanationOut,
    summary="Resolve a canonical or mapped vendor name and return cited accepted facts",
    responses={
        400: {"model": ErrorOut, "description": "Lookup validation failed"},
        404: {"model": RefusalErrorOut, "description": "No exact reviewed identity matched"},
    },
)
def explain_setting(
    session: SessionDep,
    setting: str = Query(min_length=1),
    motherboard_model_id: str = "",
    cpu_model_id: str = "",
):
    try:
        context = system_context_domain.resolve_optional(
            session,
            motherboard_model_id=motherboard_model_id,
            cpu_model_id=cpu_model_id,
        )
        explanation = guidance_read.explain_setting(
            session,
            setting,
            system_context=context,
        )
    except SystemContextError as error:
        raise HTTPException(status_code=400, detail=error.message) from None
    except GuidanceReadError as error:
        status_code = 404 if error.message.startswith("No exact") else 400
        if status_code == 404:
            gap = refusal_gaps.try_record_lookup_gap(
                session,
                surface="guidance",
                reason="no_exact_guidance_match",
                query=setting,
                context={
                    "model_id": motherboard_model_id,
                    "cpu_model_id": cpu_model_id,
                },
            )
            matches = (
                gap.nearest_matches
                if gap
                else refusal_gaps.nearest_canonical_matches(session, setting)
            )
            pointers = refusal_gaps.referrals_for_matches(session, matches)
            return JSONResponse(
                status_code=404,
                content={
                    "detail": error.message,
                    "referral_pointers": [
                        ReferralPointerOut.from_view(pointer).model_dump(mode="json")
                        for pointer in pointers
                    ],
                },
            )
        raise HTTPException(status_code=status_code, detail=error.message) from None
    output = CitationFirstExplanationOut.from_explanation(explanation)
    if not explanation.facts:
        gap = refusal_gaps.try_record_lookup_gap(
            session,
            surface="guidance",
            reason="no_accepted_guidance_claims",
            query=setting,
            context={
                "canonical_id": explanation.canonical_id,
                "model_id": motherboard_model_id,
                "cpu_model_id": cpu_model_id,
            },
        )
        pointers = refusal_gaps.referrals_for_matches(
            session,
            gap.nearest_matches if gap else (),
            canonical_id=explanation.canonical_id,
        )
        output.referral_pointers = [ReferralPointerOut.from_view(pointer) for pointer in pointers]
    return output


@router.get(
    "/canonical-settings/{canonical_id}/guidance",
    response_model=CitationFirstExplanationOut,
    summary="Return accepted guidance for one canonical setting ID",
    responses={404: {"model": RefusalErrorOut, "description": "Canonical setting not found"}},
)
def explain_canonical_setting(
    canonical_id: str,
    session: SessionDep,
    motherboard_model_id: str = "",
    cpu_model_id: str = "",
):
    try:
        context = system_context_domain.resolve_optional(
            session,
            motherboard_model_id=motherboard_model_id,
            cpu_model_id=cpu_model_id,
        )
    except SystemContextError as error:
        raise HTTPException(status_code=400, detail=error.message) from None
    explanation = guidance_read.explain_canonical_id(
        session,
        canonical_id,
        system_context=context,
    )
    if explanation is None:
        gap = refusal_gaps.try_record_lookup_gap(
            session,
            surface="guidance",
            reason="no_exact_guidance_match",
            query=canonical_id,
            context={
                "model_id": motherboard_model_id,
                "cpu_model_id": cpu_model_id,
            },
        )
        matches = (
            gap.nearest_matches
            if gap
            else refusal_gaps.nearest_canonical_matches(session, canonical_id)
        )
        pointers = refusal_gaps.referrals_for_matches(session, matches)
        return JSONResponse(
            status_code=404,
            content={
                "detail": "Canonical setting not found.",
                "referral_pointers": [
                    ReferralPointerOut.from_view(pointer).model_dump(mode="json")
                    for pointer in pointers
                ],
            },
        )
    output = CitationFirstExplanationOut.from_explanation(explanation)
    if not explanation.facts:
        gap = refusal_gaps.try_record_lookup_gap(
            session,
            surface="guidance",
            reason="no_accepted_guidance_claims",
            query=canonical_id,
            context={
                "canonical_id": explanation.canonical_id,
                "model_id": motherboard_model_id,
                "cpu_model_id": cpu_model_id,
            },
        )
        pointers = refusal_gaps.referrals_for_matches(
            session,
            gap.nearest_matches if gap else (),
            canonical_id=explanation.canonical_id,
        )
        output.referral_pointers = [ReferralPointerOut.from_view(pointer) for pointer in pointers]
    return output


@router.get(
    "/guidance/coverage",
    summary="List canonical settings and accepted guidance coverage",
)
def guidance_coverage(
    session: SessionDep,
    approved_only: bool = False,
) -> list[GuidanceCoverageOut]:
    return [
        GuidanceCoverageOut.from_row(row)
        for row in guidance_read.list_coverage(session, approved_only=approved_only)
    ]
