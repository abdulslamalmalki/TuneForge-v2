"""Protected reviewer queue for evidence-grounded recommendation claims."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.domain import guidance_claims
from app.domain import recommendation_claims as domain
from app.web.templating import templates

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/recommendation-claims", response_class=HTMLResponse)
def claim_queue(
    request: Request,
    session: SessionDep,
    status: str = "draft",
) -> HTMLResponse:
    try:
        claims = domain.list_claims(session, status=status)
    except domain.RecommendationClaimError as error:
        return templates.TemplateResponse(
            request,
            "recommendation_claims.html",
            {
                "claims": [],
                "counts": domain.status_counts(session),
                "status_filter": status,
                "error": error.message,
            },
            status_code=400,
        )
    return templates.TemplateResponse(
        request,
        "recommendation_claims.html",
        {
            "claims": claims,
            "counts": domain.status_counts(session),
            "status_filter": status,
            "error": None,
        },
    )


@router.get("/recommendation-claims/{claim_id}", response_class=HTMLResponse)
def claim_detail(request: Request, claim_id: int, session: SessionDep) -> HTMLResponse:
    detail = domain.get_detail(session, claim_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Recommendation claim not found.")
    return _render_detail(request, detail)


@router.post("/recommendation-claims/{claim_id}/edit")
def edit_claim(
    request: Request,
    claim_id: int,
    session: SessionDep,
    canonical_id: Annotated[str, Form()] = "",
    recommendation_text: Annotated[str, Form()] = "",
    recommended_action: Annotated[str, Form()] = "",
    exact_value: Annotated[str, Form()] = "",
    minimum_value: Annotated[str, Form()] = "",
    maximum_value: Annotated[str, Form()] = "",
    value_unit: Annotated[str, Form()] = "",
    use_case: Annotated[str, Form()] = "",
    risk_level: Annotated[str, Form()] = "",
    prerequisites: Annotated[str, Form()] = "",
    confidence_level: Annotated[str, Form()] = "",
    conflict_state: Annotated[str, Form()] = "",
    passage_ids: Annotated[str, Form()] = "",
    source_setting_ids: Annotated[str, Form()] = "",
    applicability_targets: Annotated[str, Form()] = "",
    bios_scopes: Annotated[str, Form()] = "",
):
    claim = domain.get_claim(session, claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail="Recommendation claim not found.")
    try:
        domain.edit_claim(
            session,
            claim,
            canonical_id=canonical_id,
            recommendation_text=recommendation_text,
            recommended_action=recommended_action,
            exact_value=exact_value,
            minimum_value=minimum_value,
            maximum_value=maximum_value,
            value_unit=value_unit,
            use_case=use_case,
            risk_level=risk_level,
            prerequisites=_parse_lines(prerequisites),
            confidence_level=confidence_level,
            conflict_state=conflict_state,
            passage_ids=_parse_ids(passage_ids),
            source_setting_ids=_parse_ids(source_setting_ids),
            applicability_targets=_parse_targets(applicability_targets),
            bios_scopes=_parse_bios_scopes(bios_scopes),
        )
    except domain.RecommendationClaimError as error:
        return _detail_error(request, session, claim_id, error.message)
    return RedirectResponse(f"/recommendation-claims/{claim_id}", status_code=303)


@router.post("/recommendation-claims/{claim_id}/review")
def review_claim(
    request: Request,
    claim_id: int,
    session: SessionDep,
    decision: Annotated[str, Form()] = "",
    note: Annotated[str, Form()] = "",
):
    claim = domain.get_claim(session, claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail="Recommendation claim not found.")
    try:
        domain.review_claim(session, claim, decision=decision, note=note)
    except domain.RecommendationClaimError as error:
        return _detail_error(request, session, claim_id, error.message)
    return RedirectResponse(f"/recommendation-claims/{claim_id}", status_code=303)


def _parse_ids(value: str) -> list[int]:
    try:
        return [int(item) for item in value.replace(",", " ").split()]
    except ValueError:
        raise domain.RecommendationClaimError("Evidence IDs must be numbers.") from None


def _parse_lines(value: str) -> list[str]:
    return [item.strip() for item in value.splitlines() if item.strip()]


def _parse_targets(value: str) -> list[guidance_claims.ApplicabilityTargetInput]:
    targets = []
    for line in _parse_lines(value):
        identity, separator, basis = line.partition("|")
        target_type, colon, target_id = identity.strip().partition(":")
        if not separator or not colon:
            raise domain.RecommendationClaimError(
                "Hardware targets must use: target_type:target_id | evidence basis."
            )
        targets.append(
            guidance_claims.ApplicabilityTargetInput(
                target_type=target_type,
                target_id=target_id,
                evidence_basis=basis,
            )
        )
    return targets


def _parse_bios_scopes(value: str) -> list[guidance_claims.BiosScopeInput]:
    scopes = []
    allowed = {
        "exact": "bios_version_exact",
        "from": "valid_from",
        "until": "valid_until",
        "introduced": "introduced_in",
        "changed": "changed_in",
        "deprecated": "deprecated_in",
        "mode": "comparison_mode",
        "notes": "version_notes",
        "basis": "evidence_basis",
    }
    for line in _parse_lines(value):
        parts = [part.strip() for part in line.split("|")]
        context_type, colon, context_id = parts[0].partition(":")
        if not colon or not context_id.strip():
            raise domain.RecommendationClaimError(
                "BIOS scopes must start with motherboard_model:id or vendor:id."
            )
        fields = {
            "context_type": context_type,
            "context_id": context_id,
            "comparison_mode": "opaque",
        }
        for part in parts[1:]:
            key, separator, item = part.partition("=")
            key = key.strip().casefold()
            if not separator or key not in allowed:
                raise domain.RecommendationClaimError(
                    "BIOS scope fields must use exact/from/until/introduced/changed/"
                    "deprecated/mode/notes/basis=value."
                )
            fields[allowed[key]] = item.strip()
        scopes.append(guidance_claims.BiosScopeInput(**fields))
    return scopes


def _render_detail(request: Request, detail, *, error: str | None = None) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "recommendation_claim_detail.html",
        {
            "detail": detail,
            "actions": domain.ACTIONS,
            "use_cases": domain.USE_CASES,
            "risk_levels": domain.RISK_LEVELS,
            "confidence_levels": domain.CONFIDENCE_LEVELS,
            "conflict_states": domain.CONFLICT_STATES,
            "target_types": guidance_claims.TARGET_TYPES,
            "error": error,
        },
        status_code=400 if error else 200,
    )


def _detail_error(
    request: Request,
    session: Session,
    claim_id: int,
    message: str,
) -> HTMLResponse:
    detail = domain.get_detail(session, claim_id)
    if detail is None:
        raise RuntimeError("Recommendation claim disappeared during review.")
    return _render_detail(request, detail, error=message)
