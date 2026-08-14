"""Manual upload-acceptance and deletion use-cases (FR-1 to FR-8)."""

import json
import logging
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urlsplit

import pdfplumber
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import config
from app.auth.context import current_actor
from app.db.models import ExtractionRun, Manual, ManualEvent, Setting
from app.domain import eligibility as eligibility_domain
from app.domain import extraction_runs
from app.domain import hardware as hardware_domain
from app.domain.errors import (
    DuplicateManualError,
    ManualDeletionError,
    ManualProvenanceError,
    UploadRejectedError,
)
from app.domain.extraction_runs import utc_now_iso
from app.normalization.vendors import (
    NORMALIZATION_VERSION,
    STATUS_UNKNOWN,
    VendorNormalization,
    normalize_vendor,
)
from app.provenance.official_sources import support_page_error
from app.search import index as search_index
from app.storage import files

logger = logging.getLogger(__name__)

ACTOR = "local-reviewer"
ACTION_PROVENANCE_CREATED = "provenance_created"
ACTION_PROVENANCE_UPDATED = "provenance_updated"
ACTION_VENDOR_NORMALIZED = "vendor_normalized"

_LANGUAGE_TAG = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
_PROVENANCE_FIELDS = (
    "vendor",
    "vendor_id",
    "model_name",
    "document_title",
    "source_url",
    "document_version",
    "published_on",
    "retrieved_at",
    "language",
    "board_revision",
    "chipset",
)


def create_manual(
    session: Session,
    stream: BinaryIO,
    original_filename: str | None,
    vendor: str,
    model_name: str,
    *,
    document_title: str = "",
    source_url: str = "",
    document_version: str = "",
    published_on: str = "",
    language: str = "en",
    board_revision: str = "",
    chipset: str = "",
    eligibility_required: bool = False,
) -> Manual:
    """Validate and store one uploaded PDF; persist and return its Manual row.

    Raises UploadRejectedError (or DuplicateManualError) with a user-safe
    message; on any failure nothing is persisted and no file is left behind.
    """
    original_filename = (original_filename or "").strip()
    if not original_filename:
        raise UploadRejectedError("Choose a PDF file to upload.")
    try:
        provenance, vendor_normalization = _validate_provenance(
            original_filename=original_filename,
            vendor=vendor,
            model_name=model_name,
            document_title=document_title,
            source_url=source_url,
            document_version=document_version,
            published_on=published_on,
            language=language,
            board_revision=board_revision,
            chipset=chipset,
        )
    except ManualProvenanceError as error:
        raise UploadRejectedError(error.message) from None

    try:
        temp_path, sha256, size = files.stage_upload(stream, max_bytes=config.MAX_UPLOAD_BYTES)
    except files.FileTooLargeError:
        raise UploadRejectedError(
            f"The file exceeds the maximum allowed size of {config.MAX_UPLOAD_BYTES:,} bytes."
        ) from None

    try:
        existing_id = session.scalar(select(Manual.id).where(Manual.sha256 == sha256))
        if existing_id is not None:
            raise DuplicateManualError(existing_id=existing_id)
        page_count = _validate_pdf(temp_path)

        now = utc_now_iso()
        manual = Manual(
            original_filename=original_filename,
            stored_filename="",
            sha256=sha256,
            file_size_bytes=size,
            page_count=page_count,
            **provenance,
            eligibility_status=(
                eligibility_domain.STATUS_PENDING
                if eligibility_required
                else eligibility_domain.STATUS_LEGACY
            ),
            retrieved_at=now,
            created_at=now,
        )
        session.add(manual)
        session.flush()  # assigns the id used for the stored filename (FR-5)
        session.add(
            ManualEvent(
                manual_id=manual.id,
                action=ACTION_PROVENANCE_CREATED,
                actor=current_actor(),
                detail=json.dumps(
                    {
                        "after": provenance_snapshot(manual),
                        "vendor_normalization": _normalization_audit(vendor_normalization),
                    },
                    sort_keys=True,
                ),
                created_at=now,
            )
        )
        hardware_domain.sync_manual_applicability(
            session,
            manual,
            actor=current_actor(),
            created_at=now,
        )
        manual.stored_filename = f"manual_{manual.id}.pdf"
        # Same transaction as the manual row: every manual gets exactly one
        # pending extraction run (system invariant, FR-12).
        extraction_runs.create_pending_run(session, manual)
        files.promote_upload(temp_path, manual.stored_filename)
        try:
            session.commit()
        except Exception:
            files.delete_manual_file(manual.stored_filename)
            raise
    except Exception:
        session.rollback()
        files.discard_upload(temp_path)
        raise

    logger.info(
        "upload accepted manual_id=%s pages=%s bytes=%s original=%r",
        manual.id,
        page_count,
        size,
        original_filename,
    )
    return manual


def list_manuals(session: Session) -> list[Manual]:
    return list(session.scalars(select(Manual).order_by(Manual.id.desc())))


@dataclass(frozen=True)
class ManualOverview:
    """One manuals-list row: run status and settings counts (FR-27)."""

    manual: Manual
    run_status: str | None  # None only if a manual predates its run
    draft_count: int
    approved_count: int
    rejected_count: int
    applicability: hardware_domain.ApplicabilityView | None


def list_manuals_overview(session: Session) -> list[ManualOverview]:
    status_by_manual = dict(
        session.execute(select(ExtractionRun.manual_id, ExtractionRun.status)).all()
    )
    settings_counts = {
        (manual_id, status): count
        for manual_id, status, count in session.execute(
            select(Setting.manual_id, Setting.status, func.count()).group_by(
                Setting.manual_id, Setting.status
            )
        )
    }
    return [
        ManualOverview(
            manual=manual,
            run_status=status_by_manual.get(manual.id),
            draft_count=settings_counts.get((manual.id, "draft"), 0),
            approved_count=settings_counts.get((manual.id, "approved"), 0),
            rejected_count=settings_counts.get((manual.id, "rejected"), 0),
            applicability=hardware_domain.get_manual_applicability(session, manual.id),
        )
        for manual in list_manuals(session)
    ]


def get_manual(session: Session, manual_id: int) -> Manual | None:
    return session.get(Manual, manual_id)


def provenance_snapshot(manual: Manual) -> dict[str, str | None]:
    """Stable serializable representation used by API/UI and audit events."""
    return {field: getattr(manual, field) for field in _PROVENANCE_FIELDS}


def missing_provenance_fields(manual: Manual) -> list[str]:
    """Fields required before this source can be treated as fully traceable.

    Hardware applicability is expanded in the next knowledge-model milestone;
    this gate intentionally covers source identity only.
    """
    required = {
        "vendor": manual.vendor_id,
        "document_title": manual.document_title,
        "source_url": manual.source_url,
        "retrieved_at": manual.retrieved_at,
        "language": manual.language,
    }
    missing = [field for field, value in required.items() if not value]
    if (
        manual.vendor_id
        and manual.source_url
        and support_page_error(manual.vendor, manual.source_url)
    ):
        missing.append("official_support_page")
    return missing


def list_manual_events(session: Session, manual_id: int) -> list[ManualEvent]:
    query = select(ManualEvent).where(ManualEvent.manual_id == manual_id).order_by(ManualEvent.id)
    return list(session.scalars(query))


def update_manual_provenance(
    session: Session,
    manual: Manual,
    *,
    vendor: str,
    model_name: str,
    document_title: str,
    source_url: str,
    document_version: str,
    published_on: str,
    language: str,
    board_revision: str,
    chipset: str,
) -> bool:
    """Replace editable provenance fields and append one immutable event.

    Returns False for an exact no-op. Retrieval time, upload metadata, hash,
    and stored filename are ingestion facts and cannot be edited here.
    """
    values, vendor_normalization = _validate_provenance(
        original_filename=manual.original_filename,
        vendor=vendor,
        model_name=model_name,
        document_title=document_title,
        source_url=source_url,
        document_version=document_version,
        published_on=published_on,
        language=language,
        board_revision=board_revision,
        chipset=chipset,
    )
    before = provenance_snapshot(manual)
    after = {**before, **values}
    if before == after:
        if vendor_normalization.changed:
            session.add(
                ManualEvent(
                    manual_id=manual.id,
                    action=ACTION_VENDOR_NORMALIZED,
                    actor=current_actor(),
                    detail=json.dumps(
                        _normalization_audit(vendor_normalization),
                        sort_keys=True,
                    ),
                    created_at=utc_now_iso(),
                )
            )
            session.commit()
        return False

    for field, value in values.items():
        setattr(manual, field, value)
    # A certified acceptance report is tied to the source metadata that was
    # inspected. Any provenance change requires re-certification.
    # Import here to keep provenance validation usable without introducing a
    # module-import cycle. The invalidation event commits atomically with the
    # provenance and hardware-applicability updates below.
    from app.domain import acceptance as acceptance_domain

    acceptance_domain.invalidate_certification(
        session,
        manual,
        reason="manual_provenance_changed",
        actor=current_actor(),
    )
    session.add(
        ManualEvent(
            manual_id=manual.id,
            action=ACTION_PROVENANCE_UPDATED,
            actor=current_actor(),
            detail=json.dumps(
                {
                    "before": before,
                    "after": after,
                    "vendor_normalization": _normalization_audit(vendor_normalization),
                },
                sort_keys=True,
            ),
            created_at=utc_now_iso(),
        )
    )
    hardware_domain.sync_manual_applicability(session, manual, actor=current_actor())
    session.commit()
    return True


def delete_manual(session: Session, manual: Manual) -> None:
    """Delete the manual row (children cascade) and its stored file (FR-8).

    Two-phase: the file is renamed aside first so a database failure can
    restore it; the rename is only finalized after the commit succeeds. On
    failure the manual record is preserved and ManualDeletionError is raised.
    """
    manual_id, stored_filename = manual.id, manual.stored_filename
    try:
        staged_path = files.stage_file_deletion(stored_filename)
    except OSError:
        logger.exception(
            "deletion aborted: stored file could not be staged manual_id=%s file=%s",
            manual_id,
            stored_filename,
        )
        raise ManualDeletionError(
            "The manual was not deleted because its stored PDF file could not be "
            "removed. Try again."
        ) from None
    if staged_path is None:
        logger.warning(
            "stored file already missing during deletion manual_id=%s file=%s",
            manual_id,
            stored_filename,
        )

    try:
        # The FTS index has no foreign key to cascade: drop this manual's
        # search entries in the same transaction as the row deletion.
        search_index.remove_manual_settings(session, manual_id)
        session.delete(manual)
        session.commit()
    except Exception:
        session.rollback()
        if staged_path is not None:
            try:
                files.restore_staged_deletion(staged_path, stored_filename)
            except OSError:
                logger.exception(
                    "failed to restore staged file after database error manual_id=%s staged=%s",
                    manual_id,
                    staged_path,
                )
        logger.exception("database deletion failed manual_id=%s", manual_id)
        raise ManualDeletionError("The manual could not be deleted. Try again.") from None

    if staged_path is not None:
        try:
            files.finalize_staged_deletion(staged_path)
        except OSError:
            logger.exception(
                "manual deleted but staged file could not be removed manual_id=%s staged=%s",
                manual_id,
                staged_path,
            )
    logger.info("manual deleted manual_id=%s file=%s", manual_id, stored_filename)


def _clean_label(field_name: str, value: str) -> str | None:
    value = " ".join(value.split())
    if len(value) > config.MAX_LABEL_LENGTH:
        raise UploadRejectedError(
            f"{field_name} must be {config.MAX_LABEL_LENGTH} characters or fewer."
        )
    return value or None


def _validate_provenance(
    *,
    original_filename: str,
    vendor: str,
    model_name: str,
    document_title: str,
    source_url: str,
    document_version: str,
    published_on: str,
    language: str,
    board_revision: str,
    chipset: str,
) -> tuple[dict[str, str | None], VendorNormalization]:
    title = " ".join(document_title.split()) or original_filename
    if len(title) > config.MAX_DOCUMENT_TITLE_LENGTH:
        raise ManualProvenanceError(
            f"Document title must be {config.MAX_DOCUMENT_TITLE_LENGTH} characters or fewer."
        )
    clean_vendor = _clean_label("Vendor", vendor)
    vendor_normalization = normalize_vendor(vendor if clean_vendor is not None else None)
    values = {
        "vendor": vendor_normalization.canonical_name,
        "vendor_id": vendor_normalization.vendor_id,
        "model_name": _clean_label("Model name", model_name),
        "document_title": title,
        "source_url": _clean_source_url(source_url),
        "document_version": _clean_label("Document version", document_version),
        "published_on": _clean_published_on(published_on),
        "language": _clean_language(language),
        "board_revision": _clean_label("Board revision", board_revision),
        "chipset": _clean_label("Chipset", chipset),
    }
    if vendor_normalization.status == STATUS_UNKNOWN:
        raise ManualProvenanceError(
            "Choose one of the supported vendors: ASUS, MSI, GIGABYTE, or ASRock."
        )
    return values, vendor_normalization


def _normalization_audit(result: VendorNormalization) -> dict[str, object]:
    return {
        "normalization_version": NORMALIZATION_VERSION,
        **result.as_dict(),
    }


def _clean_source_url(value: str) -> str | None:
    if any(ord(character) < 32 for character in value):
        raise ManualProvenanceError(
            "Source URL must not contain whitespace, control characters, or backslashes."
        )
    value = value.strip()
    if not value:
        return None
    if len(value) > config.MAX_SOURCE_URL_LENGTH:
        raise ManualProvenanceError(
            f"Source URL must be {config.MAX_SOURCE_URL_LENGTH} characters or fewer."
        )
    if "\\" in value or any(
        character.isspace() for character in value
    ):
        raise ManualProvenanceError(
            "Source URL must not contain whitespace, control characters, or backslashes."
        )
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ManualProvenanceError("Source URL must be a valid HTTP or HTTPS URL.") from None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ManualProvenanceError("Source URL must be a valid HTTP or HTTPS URL.")
    if parsed.username is not None or parsed.password is not None:
        raise ManualProvenanceError("Source URL must not contain credentials.")
    expected_port = 443 if parsed.scheme.lower() == "https" else 80
    if port not in (None, expected_port):
        raise ManualProvenanceError("Source URL must use the standard HTTP or HTTPS port.")
    return value


def _clean_published_on(value: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ManualProvenanceError("Published date must use YYYY-MM-DD format.") from None
    if parsed.isoformat() != value:
        raise ManualProvenanceError("Published date must use YYYY-MM-DD format.")
    return value


def _clean_language(value: str) -> str:
    value = value.strip() or "en"
    if len(value) > config.MAX_LANGUAGE_LENGTH or _LANGUAGE_TAG.fullmatch(value) is None:
        raise ManualProvenanceError("Language must be a valid tag such as 'en' or 'en-US'.")
    parts = value.split("-")
    return "-".join(
        [parts[0].lower(), *[part.upper() if len(part) == 2 else part for part in parts[1:]]]
    )


def _validate_pdf(path: Path) -> int:
    """Content-based PDF validation (FR-2) and page count (FR-6)."""
    if not files.has_pdf_header(path):
        raise UploadRejectedError(
            "The file could not be read as a PDF. Upload a PDF motherboard manual."
        )
    try:
        with pdfplumber.open(path) as pdf:
            page_count = len(pdf.pages)
    except Exception:
        raise UploadRejectedError(
            "The file could not be read as a PDF. Upload a PDF motherboard manual."
        ) from None
    if page_count < 1:
        raise UploadRejectedError("The PDF contains no pages.")
    if page_count > config.MAX_PDF_PAGES:
        raise UploadRejectedError(
            f"The PDF exceeds the maximum of {config.MAX_PDF_PAGES:,} pages."
        )
    return page_count
