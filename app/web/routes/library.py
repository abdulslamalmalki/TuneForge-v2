"""Public server-rendered BIOS Settings Library."""

from typing import Annotated, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.domain import canonical_settings, guidance_read, library
from app.normalization.canonical_settings import RISK_LEVELS
from app.normalization.hardware import CHIPSETS, PLATFORMS
from app.normalization.vendors import VENDORS
from app.web.templating import templates

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/compare", response_class=HTMLResponse)
def compare_vendors(
    request: Request,
    session: SessionDep,
    q: str = Query(default="", max_length=300),
) -> HTMLResponse:
    """Compare only reviewer-confirmed aliases for matching canonical settings."""
    query = " ".join(q.split())
    matches = ()
    featured = ()
    if query:
        page = library.browse(
            session,
            view=library.VIEW_CANONICAL,
            query=query,
            page_size=8,
        )
        matches = tuple(
            detail
            for item in page.items
            if (detail := library.canonical_detail(session, item.canonical_id)) is not None
        )
    else:
        featured = library.browse(
            session,
            view=library.VIEW_CANONICAL,
            page_size=8,
        ).items
    return templates.TemplateResponse(
        request,
        "compare_vendors.html",
        {
            "q": query,
            "matches": matches,
            "featured": featured,
        },
    )


@router.get("/library", response_class=HTMLResponse)
def library_index(
    request: Request,
    session: SessionDep,
    view: Literal["canonical", "source"] = "canonical",
    q: str = Query(default="", max_length=300),
    vendor_id: str = "",
    platform_id: str = "",
    chipset_id: str = "",
    category_id: str = "",
    risk_level: str = "",
    page: int = Query(default=1, ge=1),
) -> HTMLResponse:
    filters = {
        "view": view,
        "q": q,
        "vendor_id": vendor_id,
        "platform_id": platform_id,
        "chipset_id": chipset_id,
        "category_id": category_id,
        "risk_level": risk_level,
    }
    result = library.browse(
        session,
        view=view,
        query=q,
        vendor_id=vendor_id,
        platform_id=platform_id,
        chipset_id=chipset_id,
        category_id=category_id,
        risk_level=risk_level,
        page=page,
    )
    active_filter_count = sum(
        bool(value) for value in (vendor_id, platform_id, chipset_id, category_id, risk_level)
    )
    first_result = (result.page - 1) * result.page_size + 1 if result.total else 0
    last_result = min(result.page * result.page_size, result.total)
    page_numbers = _page_numbers(result.page, result.total_pages)
    return templates.TemplateResponse(
        request,
        "library_index.html",
        {
            **filters,
            "result": result,
            "vendors": VENDORS,
            "platforms": PLATFORMS,
            "chipsets": tuple(
                chipset
                for chipset in CHIPSETS
                if not platform_id or chipset.platform_id == platform_id
            ),
            "categories": canonical_settings.list_categories(session),
            "risk_levels": RISK_LEVELS,
            "approved_source_count": library.approved_source_count(session),
            "canonical_count": library.canonical_count(session),
            "guidance_count": library.guidance_count(session),
            "active_filter_count": active_filter_count,
            "has_search_or_filters": bool(q) or active_filter_count > 0,
            "first_result": first_result,
            "last_result": last_result,
            "canonical_view_url": _library_url({**filters, "view": "canonical", "page": 1}),
            "source_view_url": _library_url({**filters, "view": "source", "page": 1}),
            "reset_url": _library_url({"view": view}),
            "previous_url": (
                _library_url({**filters, "page": result.page - 1}) if result.has_previous else None
            ),
            "next_url": (
                _library_url({**filters, "page": result.page + 1}) if result.has_next else None
            ),
            "page_links": tuple(
                (number, _library_url({**filters, "page": number})) for number in page_numbers
            ),
        },
    )


@router.get("/library/canonical/{canonical_id}", response_class=HTMLResponse)
def canonical_detail(
    request: Request,
    canonical_id: str,
    session: SessionDep,
) -> HTMLResponse:
    detail = library.canonical_detail(session, canonical_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Canonical setting not found.")
    return templates.TemplateResponse(
        request,
        "library_canonical_detail.html",
        {
            "detail": detail,
            "fact_groups": _fact_groups(detail.facts),
        },
    )


@router.get("/library/source/{setting_id}", response_class=HTMLResponse)
def source_detail(
    request: Request,
    setting_id: int,
    session: SessionDep,
) -> HTMLResponse:
    detail = library.source_detail(session, setting_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Approved source setting not found.")
    return templates.TemplateResponse(
        request,
        "library_source_detail.html",
        {
            "detail": detail,
            "fact_groups": _fact_groups(detail.facts),
        },
    )


def _fact_groups(facts) -> tuple[tuple[str, tuple[object, ...]], ...]:
    return tuple(
        (claim_type, tuple(fact for fact in facts if fact.claim_type == claim_type))
        for claim_type in guidance_read.CLAIM_TYPE_ORDER
        if any(fact.claim_type == claim_type for fact in facts)
    )


def _page_numbers(current: int, total: int) -> tuple[int, ...]:
    if total <= 1:
        return ()
    values = {1, total, current - 1, current, current + 1}
    return tuple(sorted(value for value in values if 1 <= value <= total))


def _library_url(values: dict[str, object]) -> str:
    query = urlencode(
        {key: value for key, value in values.items() if value not in {None, "", 1} or key == "view"}
    )
    return f"/library?{query}" if query else "/library"
