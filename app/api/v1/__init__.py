"""Versioned JSON API. Routes translate HTTP to domain calls and back —
business rules stay in app.domain and app.search."""

from fastapi import APIRouter

from app.api.v1 import (
    acceptance,
    batches,
    canonical_settings,
    canonicalization,
    evidence_sources,
    guidance_claims,
    guidance_conflicts,
    guidance_read,
    hardware,
    interface_contract,
    library,
    manuals,
    meta,
    refusal_gaps,
    search,
    settings,
    tuning,
)

router = APIRouter(prefix="/api/v1")
router.include_router(manuals.router)
router.include_router(batches.router)
router.include_router(acceptance.router)
router.include_router(settings.router)
router.include_router(search.router)
router.include_router(hardware.router)
router.include_router(canonical_settings.router)
router.include_router(canonicalization.router)
router.include_router(evidence_sources.router)
router.include_router(guidance_claims.router)
router.include_router(guidance_conflicts.router)
router.include_router(guidance_read.router)
router.include_router(refusal_gaps.router)
router.include_router(interface_contract.router)
router.include_router(library.router)
router.include_router(tuning.router)
router.include_router(meta.router)
