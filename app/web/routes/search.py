"""Search page (FR-24 to FR-25, plan section 12 item 6)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.domain import canonical_settings as canonical_domain
from app.domain import refusal_gaps
from app.domain import system_context as system_context_domain
from app.domain.errors import SystemContextError
from app.normalization.canonical_settings import RISK_LEVELS
from app.normalization.hardware import CHIPSET_FAMILIES, CHIPSETS, MOTHERBOARD_MODELS, PLATFORMS
from app.normalization.vendors import VENDORS
from app.search import query as search_query
from app.web.templating import templates

router = APIRouter()

SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/search", response_class=HTMLResponse)
def search_page(
    request: Request,
    session: SessionDep,
    q: str = "",
    vendor_id: str = "",
    platform_id: str = "",
    chipset_family_id: str = "",
    chipset_id: str = "",
    model_id: str = "",
    canonical_id: str = "",
    category_id: str = "",
    risk_level: str = "",
    cpu_model_id: str = "",
) -> HTMLResponse:
    searched = bool(q.strip())
    resolved_system = None
    context_error = None
    if cpu_model_id.strip():
        try:
            resolved_system = system_context_domain.resolve(
                session,
                motherboard_model_id=model_id,
                cpu_model_id=cpu_model_id,
            )
            system_context_domain.validate_vendor_filter(resolved_system, vendor_id)
        except SystemContextError as exception:
            context_error = exception.message
    results = []
    referral_pointers = []
    if searched and context_error is None:
        results = search_query.search_approved(
            session,
            q,
            vendor_id=vendor_id,
            platform_id=platform_id,
            chipset_family_id=chipset_family_id,
            chipset_id=chipset_id,
            model_id=model_id,
            canonical_id=canonical_id,
            category_id=category_id,
            risk_level=risk_level,
            system_context=resolved_system,
        )
        if not results:
            gap = refusal_gaps.try_record_lookup_gap(
                session,
                surface="search",
                reason="no_approved_search_results",
                query=q,
                context={
                    "vendor_id": vendor_id,
                    "platform_id": platform_id,
                    "chipset_family_id": chipset_family_id,
                    "chipset_id": chipset_id,
                    "model_id": model_id,
                    "canonical_id": canonical_id,
                    "category_id": category_id,
                    "risk_level": risk_level,
                    "cpu_model_id": cpu_model_id,
                },
            )
            if gap is not None:
                referral_pointers = refusal_gaps.referrals_for_matches(session, gap.nearest_matches)
            else:
                referral_pointers = refusal_gaps.referrals_for_matches(
                    session, refusal_gaps.nearest_canonical_matches(session, q)
                )
    context = {
        "query": q,
        "vendor_id": vendor_id,
        "vendors": VENDORS,
        "platform_id": platform_id,
        "platforms": PLATFORMS,
        "chipset_family_id": chipset_family_id,
        "chipset_families": CHIPSET_FAMILIES,
        "chipset_id": chipset_id,
        "chipsets": CHIPSETS,
        "model_id": model_id,
        "motherboard_models": MOTHERBOARD_MODELS,
        "cpu_model_id": cpu_model_id,
        "system_cpus": system_context_domain.list_pilot_cpus(session),
        "system_context": resolved_system,
        "context_error": context_error,
        "canonical_id": canonical_id,
        "canonical_settings": canonical_domain.list_catalog(session),
        "category_id": category_id,
        "categories": canonical_domain.list_categories(session),
        "risk_level": risk_level,
        "risk_levels": RISK_LEVELS,
        "searched": searched,
        "results": results,
        "referral_pointers": referral_pointers,
    }
    return templates.TemplateResponse(
        request,
        "search.html",
        context,
        status_code=400 if context_error else 200,
    )
