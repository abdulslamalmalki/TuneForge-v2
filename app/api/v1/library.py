"""Public read-only BIOS Settings Library API."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from app.api.v1.deps import SessionDep
from app.api.v1.library_schemas import (
    LibraryCanonicalDetailOut,
    LibraryPageOut,
    LibrarySourceDetailOut,
)
from app.domain import library

router = APIRouter(prefix="/library", tags=["public library"])


@router.get(
    "/settings",
    response_model=LibraryPageOut,
    summary="Browse reviewer-approved BIOS knowledge",
)
def browse_settings(
    session: SessionDep,
    view: Literal["canonical", "source"] = "canonical",
    q: str = Query(default="", max_length=300),
    vendor_id: str = "",
    platform_id: str = "",
    chipset_id: str = "",
    category_id: str = "",
    risk_level: str = "",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=library.DEFAULT_PAGE_SIZE, ge=1, le=library.MAX_PAGE_SIZE),
) -> LibraryPageOut:
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
        page_size=page_size,
    )
    return LibraryPageOut.from_domain(result)


@router.get(
    "/canonical/{canonical_id}",
    response_model=LibraryCanonicalDetailOut,
    summary="Get one public canonical setting knowledge object",
)
def canonical_setting(canonical_id: str, session: SessionDep) -> LibraryCanonicalDetailOut:
    detail = library.canonical_detail(session, canonical_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Canonical setting not found.")
    return LibraryCanonicalDetailOut.from_domain(detail)


@router.get(
    "/source/{setting_id}",
    response_model=LibrarySourceDetailOut,
    summary="Get one reviewer-approved vendor source setting",
)
def source_setting(setting_id: int, session: SessionDep) -> LibrarySourceDetailOut:
    detail = library.source_detail(session, setting_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Approved source setting not found.")
    return LibrarySourceDetailOut.from_domain(detail)
