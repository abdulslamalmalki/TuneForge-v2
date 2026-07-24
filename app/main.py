import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app import config
from app.api import v1 as api_v1
from app.auth.middleware import AuthBoundaryMiddleware
from app.db.session import SessionLocal
from app.domain import evidence_sources as evidence_sources_domain
from app.domain import extraction_runs
from app.web.routes import (
    acceptance,
    admin_dashboard,
    auth,
    batches,
    canonical_settings,
    canonicalization,
    evidence_sources,
    guidance_claims,
    guidance_read,
    guided_answers,
    library,
    manuals,
    public,
    public_submissions,
    refusal_gaps,
    review,
    search,
    settings,
    system_context,
)
from app.web.templating import templates

logger = logging.getLogger(__name__)


def configure_logging() -> None:
    logging.basicConfig(
        stream=sys.stderr,
        level=config.LOG_LEVEL,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # Startup hooks (plan section 8), idempotent on every start: mark runs
    # interrupted by an unclean shutdown as failed (FR-29), and enforce the
    # invariant that every manual has exactly one extraction run.
    with SessionLocal() as session:
        extraction_runs.fail_interrupted_runs(session)
        evidence_sources_domain.fail_interrupted_runs(session)
        extraction_runs.ensure_every_manual_has_run(session)
    yield


def create_app() -> FastAPI:
    configure_logging()
    config.validate_runtime_config()
    config.ensure_data_dirs()

    app = FastAPI(
        title="TuneForge Knowledge Builder",
        version=config.APP_VERSION,
        description=(
            "Citation-first BIOS settings platform for cross-vendor discovery, official "
            "manual intake, verified answers, and protected human-review workflows. The "
            "versioned JSON API lives under /api/v1; HTML product and curator pages are "
            "outside the API contract."
        ),
        openapi_tags=[
            {"name": "manuals", "description": "Manual upload, metadata, and extraction runs."},
            {"name": "batches", "description": "Persistent multi-manual ingestion progress."},
            {"name": "settings", "description": "Draft review queue and the review workflow."},
            {"name": "search", "description": "Keyword search over approved settings."},
            {
                "name": "hardware",
                "description": "Stable hardware identities and applicability scopes.",
            },
            {
                "name": "canonical settings",
                "description": "Reviewer-controlled cross-vendor setting identities.",
            },
            {
                "name": "evidence sources",
                "description": "Provenance-pinned tuning documents and immutable passages.",
            },
            {
                "name": "guidance claims",
                "description": "Reviewer-controlled factual claims linked to accepted evidence.",
            },
            {
                "name": "citation-first guidance",
                "description": "Read-only accepted facts with exact source and page citations.",
            },
            {
                "name": "public library",
                "description": "Public discovery over reviewer-approved BIOS knowledge.",
            },
            {
                "name": "refusal gaps",
                "description": "Append-only refusal demand and official referral pointers.",
            },
            {
                "name": "interface contract",
                "description": "Provider-neutral rules for validated factual presentation.",
            },
            {"name": "meta", "description": "Service health and versions."},
        ],
        lifespan=_lifespan,
    )
    app.add_middleware(AuthBoundaryMiddleware)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(config.TRUSTED_HOSTS))
    app.mount(
        "/static",
        StaticFiles(directory=Path(__file__).parent / "web" / "static"),
        name="static",
    )
    # HTML pages stay out of the OpenAPI schema: /docs documents the JSON API.
    app.include_router(manuals.router, include_in_schema=False)
    app.include_router(admin_dashboard.router, include_in_schema=False)
    app.include_router(auth.router, include_in_schema=False)
    app.include_router(public.router, include_in_schema=False)
    app.include_router(public_submissions.router, include_in_schema=False)
    app.include_router(batches.router, include_in_schema=False)
    app.include_router(acceptance.router, include_in_schema=False)
    app.include_router(review.router, include_in_schema=False)
    app.include_router(search.router, include_in_schema=False)
    app.include_router(system_context.router, include_in_schema=False)
    app.include_router(settings.router, include_in_schema=False)
    app.include_router(canonical_settings.router, include_in_schema=False)
    app.include_router(canonicalization.router, include_in_schema=False)
    app.include_router(evidence_sources.router, include_in_schema=False)
    app.include_router(guidance_claims.router, include_in_schema=False)
    app.include_router(guidance_read.router, include_in_schema=False)
    app.include_router(guided_answers.router, include_in_schema=False)
    app.include_router(library.router, include_in_schema=False)
    app.include_router(refusal_gaps.router, include_in_schema=False)
    app.include_router(api_v1.router)

    # Failures render a page with a safe message, never a raw stack trace
    # (Implementation Plan section 13, NFR-4). API paths get JSON instead.
    @app.exception_handler(StarletteHTTPException)
    async def render_http_error(request: Request, exc: StarletteHTTPException) -> Response:
        message = exc.detail or "Something went wrong."
        if _is_api_request(request):
            return JSONResponse({"detail": message}, status_code=exc.status_code)
        context = {"status_code": exc.status_code, "message": message}
        return templates.TemplateResponse(
            request, "error.html", context, status_code=exc.status_code
        )

    @app.exception_handler(RequestValidationError)
    async def render_validation_error(request: Request, exc: RequestValidationError) -> Response:
        if _is_api_request(request):
            # FastAPI's standard 422 shape, so clients see field-level errors.
            return JSONResponse({"detail": jsonable_encoder(exc.errors())}, status_code=422)
        context = {"status_code": 400, "message": "The request was invalid."}
        return templates.TemplateResponse(request, "error.html", context, status_code=400)

    @app.exception_handler(Exception)
    async def render_unexpected_error(request: Request, exc: Exception) -> Response:
        # Last-resort handler (NFR-4): log the traceback, never expose it.
        logger.exception("unhandled error on %s %s", request.method, request.url.path)
        if _is_api_request(request):
            return JSONResponse({"detail": "Internal server error."}, status_code=500)
        context = {"status_code": 500, "message": "Something went wrong."}
        return templates.TemplateResponse(request, "error.html", context, status_code=500)

    return app


def _is_api_request(request: Request) -> bool:
    return request.url.path.startswith("/api/")


app = create_app()
