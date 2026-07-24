"""Read-only product and deployment launch gates for the focused AM5 release."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import config
from app.db.models import EvidenceExtractionRun, ExtractionRun, UserAccount
from app.domain import am5_coverage, am5_question_acceptance, readiness
from app.domain import library as public_library
from app.operations.backup import BackupError, inspect_backup

STATUS_READY = "ready"
STATUS_HOLD = "hold"


@dataclass(frozen=True)
class ReleaseCheck:
    check_id: str
    group: str
    label: str
    passed: bool
    required: bool
    detail: str
    href: str | None = None


@dataclass(frozen=True)
class RecoveryCheckpoint:
    passed: bool
    detail: str
    archive_name: str | None = None
    created_at: str | None = None


@dataclass(frozen=True)
class ReleaseReadiness:
    report_version: str
    app_version: str
    product_status: str
    deployment_status: str
    product_passed_count: int
    product_required_count: int
    deployment_passed_count: int
    deployment_required_count: int
    checks: tuple[ReleaseCheck, ...]


def build_report(session: Session) -> ReleaseReadiness:
    """Project launch readiness without mutating data or validating generated prose."""

    infrastructure = readiness.check(session)
    coverage = am5_coverage.build_report(session)
    questions = am5_question_acceptance.build_report(session)
    failed_extractions = _failed_extraction_count(session)
    administrator_ready = _active_administrator_count(session) > 0
    library_source_count = public_library.approved_source_count(session)
    library_canonical_count = public_library.canonical_count(session)
    checkpoint = _recovery_checkpoint(config.BACKUPS_DIR, config.DATABASE_PATH)
    production_configuration = _production_configuration_ready()

    product_checks = (
        ReleaseCheck(
            "am5-coverage",
            "product",
            "Reviewed AM5 coverage gates",
            coverage.summary.expansion_status == am5_coverage.EXPANSION_READY,
            True,
            (
                f"{sum(gate.passed for gate in coverage.gates)} of "
                f"{len(coverage.gates)} reviewed gates pass."
            ),
            "/am5-coverage",
        ),
        ReleaseCheck(
            "am5-questions",
            "product",
            "Fixed AM5 question acceptance",
            questions.summary.acceptance_status == am5_question_acceptance.ACCEPTANCE_READY,
            True,
            (
                f"{questions.summary.passing_question_count} of "
                f"{questions.summary.total_question_count} questions behave as specified."
            ),
            "/am5-question-acceptance",
        ),
        ReleaseCheck(
            "extraction-pipeline",
            "product",
            "Extraction pipelines",
            failed_extractions == 0,
            True,
            (
                "No failed manual or evidence extraction runs are active."
                if failed_extractions == 0
                else f"{failed_extractions} failed extraction run(s) require review."
            ),
            "/manuals",
        ),
        ReleaseCheck(
            "provider-free-core",
            "product",
            "Provider-free public answers",
            True,
            True,
            (
                "The v1 public experience uses deterministic routing and reviewed claims; "
                "no paid LLM provider is required."
            ),
            "/api/v1/interface-contract",
        ),
        ReleaseCheck(
            "public-library",
            "product",
            "Public BIOS Settings Library",
            library_source_count > 0 and library_canonical_count > 0,
            True,
            (
                f"{library_source_count} approved source settings and "
                f"{library_canonical_count} canonical settings are publicly discoverable."
                if library_source_count > 0 and library_canonical_count > 0
                else "Approved source and canonical settings are required for Library launch."
            ),
            "/library",
        ),
    )
    deployment_checks = (
        ReleaseCheck(
            "infrastructure",
            "deployment",
            "Schema and durable storage",
            infrastructure.status == "ready",
            True,
            _infrastructure_detail(infrastructure),
            "/api/v1/ready",
        ),
        ReleaseCheck(
            "administrator",
            "deployment",
            "Administrator account",
            administrator_ready,
            True,
            (
                "At least one active administrator account exists."
                if administrator_ready
                else "Create the first administrator through /setup before launch."
            ),
            "/admin/users" if administrator_ready else "/setup",
        ),
        ReleaseCheck(
            "production-configuration",
            "deployment",
            "Production security configuration",
            production_configuration,
            True,
            (
                "Production mode, required authentication, secure cookies, and explicit "
                "trusted hosts are enabled."
                if production_configuration
                else (
                    "Local preview configuration is active; apply the production "
                    "environment settings before public launch."
                )
            ),
        ),
        ReleaseCheck(
            "recovery-checkpoint",
            "deployment",
            "Current recovery checkpoint",
            checkpoint.passed,
            True,
            checkpoint.detail,
        ),
    )
    checks = product_checks + deployment_checks
    return _summarize(checks)


def _summarize(checks: tuple[ReleaseCheck, ...]) -> ReleaseReadiness:
    product = tuple(check for check in checks if check.group == "product" and check.required)
    deployment = tuple(
        check for check in checks if check.group in {"product", "deployment"} and check.required
    )
    product_passed = sum(check.passed for check in product)
    deployment_passed = sum(check.passed for check in deployment)
    return ReleaseReadiness(
        report_version="1.0",
        app_version=config.APP_VERSION,
        product_status=STATUS_READY if product_passed == len(product) else STATUS_HOLD,
        deployment_status=(STATUS_READY if deployment_passed == len(deployment) else STATUS_HOLD),
        product_passed_count=product_passed,
        product_required_count=len(product),
        deployment_passed_count=deployment_passed,
        deployment_required_count=len(deployment),
        checks=checks,
    )


def _failed_extraction_count(session: Session) -> int:
    manual = session.scalar(
        select(func.count()).select_from(ExtractionRun).where(ExtractionRun.status == "failed")
    )
    evidence = session.scalar(
        select(func.count())
        .select_from(EvidenceExtractionRun)
        .where(EvidenceExtractionRun.status == "failed")
    )
    return (manual or 0) + (evidence or 0)


def _active_administrator_count(session: Session) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(UserAccount)
            .where(UserAccount.role == "administrator", UserAccount.status == "active")
        )
        or 0
    )


def _production_configuration_ready() -> bool:
    return (
        config.ENVIRONMENT == "production"
        and config.AUTH_MODE == "required"
        and config.AUTH_COOKIE_SECURE
        and bool(config.TRUSTED_HOSTS)
        and "*" not in config.TRUSTED_HOSTS
    )


def _infrastructure_detail(result: readiness.Readiness) -> str:
    passed = sum(result.checks.values())
    return f"{passed} of {len(result.checks)} database, schema, and storage checks pass."


def _recovery_checkpoint(backup_dir: Path, database_path: Path) -> RecoveryCheckpoint:
    """Detect a structurally valid backup made after the current database write."""

    archives = tuple(backup_dir.glob("*.zip")) if backup_dir.is_dir() else ()
    if not archives:
        return RecoveryCheckpoint(
            False,
            f"No TuneForge backup archive is present in {backup_dir}.",
        )
    latest = max(archives, key=lambda path: path.stat().st_mtime)
    try:
        inspection = inspect_backup(latest)
    except BackupError:
        return RecoveryCheckpoint(False, f"Latest backup {latest.name} has an invalid manifest.")
    structurally_valid = inspection.database_revision == config.EXPECTED_DATABASE_REVISION
    current = database_path.is_file() and latest.stat().st_mtime >= database_path.stat().st_mtime
    if not structurally_valid:
        return RecoveryCheckpoint(False, f"Latest backup {latest.name} has an invalid manifest.")
    if not current:
        return RecoveryCheckpoint(
            False,
            f"Latest backup {latest.name} predates the current database; create a new checkpoint.",
            latest.name,
            inspection.created_at,
        )
    return RecoveryCheckpoint(
        True,
        (
            f"Current backup {latest.name} has the expected format and schema. "
            "A restore drill remains an operator release step."
        ),
        latest.name,
        inspection.created_at,
    )
