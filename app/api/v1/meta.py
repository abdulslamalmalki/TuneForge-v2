"""API v1 service metadata."""

import logging

from fastapi import APIRouter, Response

from app import config
from app.api.v1.deps import SessionDep
from app.api.v1.schemas import HealthOut, ReadinessOut, ReleaseReadinessOut
from app.auth.deps import AdministratorPrincipal
from app.domain import readiness, release_readiness
from app.extraction.settings_parser import PARSER_VERSION
from app.observability import log_event, metrics

router = APIRouter(tags=["meta"])


@router.get(
    "/health",
    summary="Service health and versions",
    description="Liveness probe: application version and the current parser version.",
)
def health() -> HealthOut:
    return HealthOut(status="ok", app_version=config.APP_VERSION, parser_version=PARSER_VERSION)


@router.get(
    "/ready",
    response_model=ReadinessOut,
    summary="Check database schema and durable storage readiness",
    responses={503: {"description": "A required service component is unavailable"}},
)
def ready(session: SessionDep, response: Response) -> ReadinessOut:
    result = readiness.check(session)
    for check, passed in result.checks.items():
        metrics.increment(
            "readiness_checks_total", check=check, status="pass" if passed else "fail"
        )
    if result.status != "ready":
        response.status_code = 503
        metrics.increment("readiness_failures_total")
        log_event(
            logging.getLogger(__name__),
            "readiness_failed",
            level=logging.ERROR,
            failed_checks=",".join(
                check for check, passed in result.checks.items() if not passed
            ),
        )
    return ReadinessOut(
        status=result.status,
        app_version=config.APP_VERSION,
        database_revision=result.database_revision,
        authentication_initialized=result.authentication_initialized,
        checks=result.checks,
    )


@router.get(
    "/operations/metrics",
    summary="Get process-local privacy-safe operational metrics",
)
def operational_metrics(_administrator: AdministratorPrincipal) -> dict[str, object]:
    return metrics.snapshot()


@router.get(
    "/release-readiness",
    response_model=ReleaseReadinessOut,
    summary="Evaluate focused AM5 product and deployment launch gates",
)
def release_gate(session: SessionDep) -> ReleaseReadinessOut:
    return ReleaseReadinessOut.model_validate(
        release_readiness.build_report(session),
        from_attributes=True,
    )
