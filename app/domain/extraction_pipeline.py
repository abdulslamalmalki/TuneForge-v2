"""Extraction pipeline orchestration (plan section 10).

Coordinates Manual -> stored PDF -> page-text reader -> settings parser,
executing a manual's pending run in the background with status transitions.
A succeeded run has parsed every page: its candidates are persisted as
Draft settings, and the run records settings_found plus the parser_version
that produced them, all in one transaction with the success status.
parser_version stays NULL until a parser actually processes the document
(failed runs never reach parsing).
"""

import logging

from sqlalchemy.orm import Session

from app import config
from app.db.models import ExtractionRun
from app.db.session import SessionLocal
from app.domain import eligibility, extraction_runs
from app.domain import manuals as manuals_domain
from app.domain import settings as settings_domain
from app.domain.errors import ManualNotFoundError
from app.extraction import pdf_text, settings_parser
from app.observability import log_event, metrics
from app.storage import files

logger = logging.getLogger(__name__)

UNEXPECTED_FAILURE_MESSAGE = "Extraction failed unexpectedly. Check the application logs."
NO_TEXT_LAYER_MESSAGE = (
    "No usable text layer - this looks like a scanned PDF; OCR is out of scope for v0.1."
)


def extract_manual_pages(session: Session, manual_id: int) -> pdf_text.PdfText:
    """Read the stored PDF of the given manual and return its page text.

    Raises ManualNotFoundError for an unknown manual id; PdfReadError from
    the reader propagates unchanged (structured reason + safe message).
    """
    manual = manuals_domain.get_manual(session, manual_id)
    if manual is None:
        raise ManualNotFoundError(manual_id)
    path = files.manual_file_path(manual.stored_filename)
    return pdf_text.read_pdf_text(path)


def execute_pending_run(manual_id: int) -> None:
    """Execute the manual's pending extraction run (background task, FR-11).

    Owns its own session because it outlives the upload request. Never
    raises: any failure is recorded on the run with a safe message (NFR-4).
    """
    with SessionLocal() as session:
        run = extraction_runs.get_run_for_manual(session, manual_id)
        if run is None:
            logger.warning("no extraction run to execute for manual_id=%s", manual_id)
            return
        if run.status != extraction_runs.STATUS_PENDING:
            logger.warning(
                "extraction run %s for manual_id=%s is %s, not pending; skipping",
                run.id,
                manual_id,
                run.status,
            )
            return
        manual = manuals_domain.get_manual(session, manual_id)
        if manual is None:
            logger.warning("manual disappeared before extraction manual_id=%s", manual_id)
            return
        if not eligibility.permits_extraction(manual):
            logger.info(
                "extraction held by eligibility gate manual_id=%s status=%s",
                manual_id,
                manual.eligibility_status,
            )
            return

        run.status = extraction_runs.STATUS_RUNNING
        run.started_at = extraction_runs.utc_now_iso()
        session.commit()
        logger.info("extraction run %s started manual_id=%s", run.id, manual_id)

        try:
            result = extract_manual_pages(session, manual_id)
        except (pdf_text.PdfReadError, ManualNotFoundError) as error:
            _fail_run(session, run, error.message)
            return
        except Exception:
            logger.exception("unexpected extraction failure run=%s manual_id=%s", run.id, manual_id)
            _fail_run(session, run, UNEXPECTED_FAILURE_MESSAGE)
            return

        run.pages_processed = result.page_count
        total_text_chars = sum(len(page.text) for page in result.pages)
        if total_text_chars < config.MIN_EXTRACTABLE_TEXT_CHARS:
            # FR-10: scanned/image-only manuals fail with an explicit reason.
            _fail_run(session, run, NO_TEXT_LAYER_MESSAGE)
            return

        try:
            candidates = settings_parser.parse_settings(result)
            settings_domain.create_draft_settings(
                session,
                manual_id=manual_id,
                extraction_run_id=run.id,
                candidates=candidates,
            )
        except Exception:
            # Plan section 10, step 7: any exception fails the run. Roll back
            # so no partial drafts ride along with the failure commit.
            logger.exception("parsing failed run=%s manual_id=%s", run.id, manual_id)
            session.rollback()
            run.pages_processed = result.page_count  # re-apply after rollback
            _fail_run(session, run, UNEXPECTED_FAILURE_MESSAGE)
            return

        # Drafts, counters, parser provenance, and the success status commit
        # as one transaction (plan section 10, step 6).
        run.settings_found = len(candidates)
        run.parser_version = settings_parser.PARSER_VERSION
        run.status = extraction_runs.STATUS_SUCCEEDED
        run.finished_at = extraction_runs.utc_now_iso()
        session.commit()
        metrics.increment("manual_extraction_runs_total", status="succeeded")
        logger.info(
            "extraction run %s succeeded manual_id=%s pages=%s settings=%s",
            run.id,
            manual_id,
            result.page_count,
            len(candidates),
        )


def _fail_run(session: Session, run: ExtractionRun, message: str) -> None:
    run.status = extraction_runs.STATUS_FAILED
    run.error_message = message
    run.finished_at = extraction_runs.utc_now_iso()
    session.commit()
    metrics.increment("manual_extraction_runs_total", status="failed")
    log_event(
        logger,
        "manual_extraction_failed",
        level=logging.WARNING,
        run_id=run.id,
        manual_id=run.manual_id,
    )
    logger.info("extraction run %s failed manual_id=%s: %s", run.id, run.manual_id, message)
