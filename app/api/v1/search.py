"""API v1 search — same domain query as the HTML page."""

from fastapi import APIRouter, HTTPException, Response

from app.api.v1.deps import SessionDep
from app.api.v1.pagination import PAGINATED_200, PageDep, paginate
from app.api.v1.schemas import SearchResultOut
from app.domain import refusal_gaps, system_context
from app.domain.errors import SystemContextError
from app.search import query as search_query

router = APIRouter(tags=["search"])


@router.get(
    "/search",
    summary="Search approved settings",
    description=(
        "Keyword search over approved settings' effective name and"
        " description (FR-24). Hostile FTS5 syntax is treated as literal"
        " keywords; a blank query returns an empty list. Snippets are"
        " HTML-escaped with <mark> around matches. Mapped results include"
        " cited equivalent vendor names; exact unambiguous canonical names"
        " and reviewed aliases expand to their approved mapped settings."
    ),
    responses=PAGINATED_200,
)
def search(
    session: SessionDep,
    response: Response,
    page: PageDep,
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
    bios_version: str = "",
) -> list[SearchResultOut]:
    resolved_context = None
    if cpu_model_id.strip():
        try:
            resolved_context = system_context.resolve(
                session,
                motherboard_model_id=model_id,
                cpu_model_id=cpu_model_id,
                bios_version=bios_version,
            )
            system_context.validate_vendor_filter(resolved_context, vendor_id)
        except SystemContextError as error:
            raise HTTPException(status_code=400, detail=error.message) from None
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
        system_context=resolved_context,
    )
    if q.strip() and not results:
        refusal_gaps.try_record_lookup_gap(
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
    return paginate([SearchResultOut.from_result(result) for result in results], page, response)
