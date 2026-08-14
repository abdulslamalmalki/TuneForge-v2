"""Trusted tuning-evidence source intake, review, and deterministic extraction."""

import hashlib
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
from app.db.models import (
    EvidenceExtractionRun,
    EvidencePassage,
    EvidenceSource,
    EvidenceSourceEvent,
)
from app.domain.errors import EvidenceSourceError
from app.domain.extraction_runs import utc_now_iso
from app.observability import log_event, metrics
from app.storage import files

ACTOR = "local-reviewer"
EXTRACTOR_VERSION = "evidence-pages-1.0"
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
INTERRUPTED_MESSAGE = (
    "Evidence extraction was interrupted by an application shutdown. "
    "Review and approve the pending source again to retry."
)

logger = logging.getLogger(__name__)

PUBLISHERS = {
    "amd": ("Advanced Micro Devices, Inc.", ("amd.com",)),
    "intel": ("Intel Corporation", ("intel.com",)),
    "asus": ("ASUSTeK Computer Inc.", ("asus.com",)),
    "msi": ("Micro-Star INT'L CO., LTD.", ("msi.com",)),
    "gigabyte": ("GIGABYTE Technology", ("gigabyte.com",)),
    "asrock": ("ASRock Inc.", ("asrock.com",)),
}

_LANGUAGE_TAG = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
_SECTION_HEADING = re.compile(r"^\d+(?:\.\d+)*\s+\S")


@dataclass(frozen=True)
class EvidenceSourceOverview:
    source: EvidenceSource
    extraction_status: str
    passage_count: int


@dataclass(frozen=True)
class EvidenceSourceDetail:
    source: EvidenceSource
    extraction_run: EvidenceExtractionRun
    passages: tuple[EvidencePassage, ...]
    events: tuple[EvidenceSourceEvent, ...]


def create_source(
    session: Session,
    stream: BinaryIO,
    original_filename: str | None,
    *,
    publisher_id: str,
    title: str,
    source_url: str,
    document_id: str = "",
    document_version: str,
    published_on: str,
    language: str = "en",
) -> EvidenceSource:
    """Store one official PDF as a pending, provenance-pinned evidence source."""
    original_filename = (original_filename or "").strip()
    if not original_filename:
        raise EvidenceSourceError("Choose an official PDF to upload.")
    provenance = _validate_provenance(
        publisher_id=publisher_id,
        title=title,
        source_url=source_url,
        document_id=document_id,
        document_version=document_version,
        published_on=published_on,
        language=language,
    )
    try:
        temp_path, sha256, size = files.stage_upload(stream, config.MAX_UPLOAD_BYTES)
    except files.FileTooLargeError:
        raise EvidenceSourceError(
            f"The file exceeds the maximum allowed size of {config.MAX_UPLOAD_BYTES:,} bytes."
        ) from None

    try:
        existing_id = session.scalar(
            select(EvidenceSource.id).where(EvidenceSource.sha256 == sha256)
        )
        if existing_id is not None:
            raise EvidenceSourceError(
                f"This evidence PDF is already stored as source {existing_id}."
            )
        page_count = _validate_pdf(temp_path)
        now = utc_now_iso()
        source = EvidenceSource(
            **provenance,
            source_type="official_document",
            trust_tier="primary_official",
            original_filename=original_filename,
            stored_filename="",
            sha256=sha256,
            file_size_bytes=size,
            page_count=page_count,
            retrieved_at=now,
            status=STATUS_PENDING,
            reviewer=None,
            reviewed_at=None,
            review_note=None,
            created_at=now,
        )
        session.add(source)
        session.flush()
        source.stored_filename = f"evidence_source_{source.id}.pdf"
        session.add(
            EvidenceExtractionRun(
                source_id=source.id,
                status=STATUS_PENDING,
                extractor_version=None,
                pages_processed=0,
                passages_found=0,
                error_message=None,
                started_at=None,
                finished_at=None,
                created_at=now,
            )
        )
        session.add(
            EvidenceSourceEvent(
                source_id=source.id,
                action="source_created",
                actor=current_actor(),
                detail=json.dumps(source_snapshot(source), sort_keys=True),
                created_at=now,
            )
        )
        files.promote_evidence_source_upload(temp_path, source.stored_filename)
        try:
            session.commit()
        except Exception:
            files.delete_evidence_source_file(source.stored_filename)
            raise
        session.refresh(source)
        return source
    except Exception:
        session.rollback()
        files.discard_upload(temp_path)
        raise


def list_sources(session: Session) -> list[EvidenceSourceOverview]:
    rows = session.execute(
        select(
            EvidenceSource,
            EvidenceExtractionRun.status,
            func.count(EvidencePassage.id),
        )
        .join(EvidenceExtractionRun, EvidenceExtractionRun.source_id == EvidenceSource.id)
        .outerjoin(EvidencePassage, EvidencePassage.source_id == EvidenceSource.id)
        .group_by(EvidenceSource.id, EvidenceExtractionRun.status)
        .order_by(EvidenceSource.id.desc())
    )
    return [
        EvidenceSourceOverview(source=source, extraction_status=status, passage_count=count)
        for source, status, count in rows
    ]


def get_detail(session: Session, source_id: int) -> EvidenceSourceDetail | None:
    source = session.get(EvidenceSource, source_id)
    if source is None:
        return None
    run = session.scalar(
        select(EvidenceExtractionRun).where(EvidenceExtractionRun.source_id == source_id)
    )
    if run is None:
        raise RuntimeError(f"Evidence source {source_id} has no extraction run.")
    passages = tuple(
        session.scalars(
            select(EvidencePassage)
            .where(EvidencePassage.source_id == source_id)
            .order_by(EvidencePassage.ordinal)
        )
    )
    events = tuple(
        session.scalars(
            select(EvidenceSourceEvent)
            .where(EvidenceSourceEvent.source_id == source_id)
            .order_by(EvidenceSourceEvent.id)
        )
    )
    return EvidenceSourceDetail(source=source, extraction_run=run, passages=passages, events=events)


def fail_interrupted_runs(session: Session) -> int:
    """Fail evidence runs left running by an unclean shutdown.

    Sources remain pending, so the existing reviewed approval action is the
    explicit retry path. Each recovery is append-only audited and the function
    is idempotent because only running rows are selected.
    """
    interrupted = list(
        session.scalars(
            select(EvidenceExtractionRun).where(EvidenceExtractionRun.status == "running")
        )
    )
    now = utc_now_iso()
    for run in interrupted:
        run.status = "failed"
        run.error_message = INTERRUPTED_MESSAGE
        run.finished_at = now
        session.add(
            EvidenceSourceEvent(
                source_id=run.source_id,
                action="extraction_interrupted",
                actor="system",
                detail=json.dumps({"error": INTERRUPTED_MESSAGE}, sort_keys=True),
                created_at=now,
            )
        )
    session.commit()
    if interrupted:
        logger.info(
            "marked %s interrupted evidence extraction run(s) as failed at startup",
            len(interrupted),
        )
    return len(interrupted)


def review_source(
    session: Session,
    source: EvidenceSource,
    *,
    decision: str,
    note: str,
) -> EvidenceSource:
    decision = decision.strip().lower()
    if decision not in {STATUS_APPROVED, STATUS_REJECTED}:
        raise EvidenceSourceError("Decision must be approved or rejected.")
    if source.status != STATUS_PENDING:
        raise EvidenceSourceError("Only pending evidence sources can be reviewed.")
    note = " ".join(note.split())
    if not note:
        raise EvidenceSourceError("Record a reviewer note for this decision.")
    if len(note) > config.MAX_EVIDENCE_REVIEW_NOTE_LENGTH:
        raise EvidenceSourceError(
            f"Reviewer note must be {config.MAX_EVIDENCE_REVIEW_NOTE_LENGTH} characters or fewer."
        )

    if decision == STATUS_REJECTED:
        now = utc_now_iso()
        source.status = STATUS_REJECTED
        actor = current_actor()
        source.reviewer = actor
        source.reviewed_at = now
        source.review_note = note
        session.add(
            EvidenceSourceEvent(
                source_id=source.id,
                action="source_rejected",
                actor=actor,
                detail=json.dumps({"note": note}, sort_keys=True),
                created_at=now,
            )
        )
        session.commit()
        session.refresh(source)
        return source

    _extract_approved_source(session, source, note=note)
    session.refresh(source)
    return source


def source_snapshot(source: EvidenceSource) -> dict[str, object]:
    return {
        "publisher_id": source.publisher_id,
        "publisher_name": source.publisher_name,
        "title": source.title,
        "source_type": source.source_type,
        "trust_tier": source.trust_tier,
        "source_url": source.source_url,
        "document_id": source.document_id,
        "document_version": source.document_version,
        "published_on": source.published_on,
        "retrieved_at": source.retrieved_at,
        "language": source.language,
        "sha256": source.sha256,
        "file_size_bytes": source.file_size_bytes,
        "page_count": source.page_count,
    }


def _extract_approved_source(session: Session, source: EvidenceSource, *, note: str) -> None:
    run = session.scalar(
        select(EvidenceExtractionRun).where(EvidenceExtractionRun.source_id == source.id)
    )
    if run is None:
        raise RuntimeError(f"Evidence source {source.id} has no extraction run.")
    if run.status not in {STATUS_PENDING, "failed"}:
        raise EvidenceSourceError(
            "This source extraction cannot be started from its current state."
        )

    started_at = utc_now_iso()
    run.status = "running"
    run.extractor_version = EXTRACTOR_VERSION
    run.error_message = None
    run.started_at = started_at
    run.finished_at = None
    session.commit()

    try:
        passage_rows, pages_processed, total_chars = _read_passages(
            files.evidence_source_file_path(source.stored_filename)
        )
        if total_chars < config.MIN_EXTRACTABLE_TEXT_CHARS:
            raise EvidenceSourceError(
                "The PDF does not contain enough extractable text for trusted evidence."
            )
        now = utc_now_iso()
        run = session.scalar(
            select(EvidenceExtractionRun).where(EvidenceExtractionRun.source_id == source.id)
        )
        if run is None:
            raise RuntimeError(f"Evidence source {source.id} has no extraction run.")
        session.add_all(
            EvidencePassage(
                source_id=source.id,
                extraction_run_id=run.id,
                ordinal=ordinal,
                page_number=page_number,
                section_heading=heading,
                passage_text=text,
                text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                created_at=now,
            )
            for ordinal, page_number, heading, text in passage_rows
        )
        run.status = "succeeded"
        run.pages_processed = pages_processed
        run.passages_found = len(passage_rows)
        run.finished_at = now
        source.status = STATUS_APPROVED
        actor = current_actor()
        source.reviewer = actor
        source.reviewed_at = now
        source.review_note = note
        session.add(
            EvidenceSourceEvent(
                source_id=source.id,
                action="source_approved",
                actor=actor,
                detail=json.dumps(
                    {
                        "extractor_version": EXTRACTOR_VERSION,
                        "pages_processed": pages_processed,
                        "passages_found": len(passage_rows),
                        "note": note,
                    },
                    sort_keys=True,
                ),
                created_at=now,
            )
        )
        session.commit()
        metrics.increment("evidence_extraction_runs_total", status="succeeded")
    except Exception as error:
        session.rollback()
        run = session.scalar(
            select(EvidenceExtractionRun).where(EvidenceExtractionRun.source_id == source.id)
        )
        if run is not None:
            now = utc_now_iso()
            run.status = "failed"
            run.error_message = str(error)[:2000]
            run.finished_at = now
            session.add(
                EvidenceSourceEvent(
                    source_id=source.id,
                    action="extraction_failed",
                    actor="system",
                    detail=json.dumps({"error": run.error_message}, sort_keys=True),
                    created_at=now,
                )
            )
            session.commit()
            metrics.increment("evidence_extraction_runs_total", status="failed")
            log_event(
                logger,
                "evidence_extraction_failed",
                level=logging.WARNING,
                source_id=source.id,
            )
        if isinstance(error, EvidenceSourceError):
            raise
        raise EvidenceSourceError("The PDF text could not be extracted safely.") from None


def _read_passages(path: Path) -> tuple[list[tuple[int, int, str | None, str]], int, int]:
    rows: list[tuple[int, int, str | None, str]] = []
    total_chars = 0
    ordinal = 1
    try:
        with pdfplumber.open(path) as pdf:
            for page_number, page in enumerate(pdf.pages, start=1):
                page_text = page.extract_text() or ""
                lines = [" ".join(line.split()) for line in page_text.splitlines()]
                lines = [line for line in lines if line]
                total_chars += sum(len(line) for line in lines)
                heading = _heading_from_lines(lines)
                for passage in _chunk_lines(lines):
                    rows.append((ordinal, page_number, heading, passage))
                    ordinal += 1
            return rows, len(pdf.pages), total_chars
    except EvidenceSourceError:
        raise
    except Exception as error:
        raise EvidenceSourceError("The uploaded file could not be read as a PDF.") from error


def _chunk_lines(lines: list[str]) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    current_size = 0
    for line in lines:
        remaining = line
        while len(remaining) > config.MAX_EVIDENCE_PASSAGE_CHARS:
            if current:
                chunks.append("\n".join(current))
                current = []
                current_size = 0
            chunks.append(remaining[: config.MAX_EVIDENCE_PASSAGE_CHARS])
            remaining = remaining[config.MAX_EVIDENCE_PASSAGE_CHARS :]
        if not remaining:
            continue
        added_size = len(remaining) + (1 if current else 0)
        if current and current_size + added_size > config.MAX_EVIDENCE_PASSAGE_CHARS:
            chunks.append("\n".join(current))
            current = []
            current_size = 0
        current.append(remaining)
        current_size += len(remaining) + (1 if len(current) > 1 else 0)
    if current:
        chunks.append("\n".join(current))
    return chunks


def _heading_from_lines(lines: list[str]) -> str | None:
    for line in lines[:12]:
        if _SECTION_HEADING.match(line):
            return line[:300]
    return lines[0][:300] if lines else None


def _validate_pdf(path: Path) -> int:
    if not files.has_pdf_header(path):
        raise EvidenceSourceError("The uploaded file could not be read as a PDF.")
    try:
        with pdfplumber.open(path) as pdf:
            page_count = len(pdf.pages)
    except Exception:
        raise EvidenceSourceError("The uploaded file could not be read as a PDF.") from None
    if page_count < 1:
        raise EvidenceSourceError("The PDF contains no pages.")
    if page_count > config.MAX_PDF_PAGES:
        raise EvidenceSourceError(
            f"The PDF exceeds the maximum of {config.MAX_PDF_PAGES:,} pages."
        )
    return page_count


def _validate_provenance(
    *,
    publisher_id: str,
    title: str,
    source_url: str,
    document_id: str,
    document_version: str,
    published_on: str,
    language: str,
) -> dict[str, str | None]:
    publisher_id, publisher_name, source_url = validate_official_source_url(
        publisher_id, source_url
    )
    title = " ".join(title.split())
    if not title or len(title) > config.MAX_DOCUMENT_TITLE_LENGTH:
        raise EvidenceSourceError(
            f"Title is required and must be {config.MAX_DOCUMENT_TITLE_LENGTH} characters or fewer."
        )
    document_id = " ".join(document_id.split())
    document_version = " ".join(document_version.split())
    if len(document_id) > config.MAX_LABEL_LENGTH:
        raise EvidenceSourceError("Document ID is too long.")
    if not document_version or len(document_version) > config.MAX_LABEL_LENGTH:
        raise EvidenceSourceError(
            "Document version is required and must be 100 characters or fewer."
        )
    try:
        parsed_date = date.fromisoformat(published_on.strip())
    except ValueError:
        raise EvidenceSourceError("Published date must use YYYY-MM-DD format.") from None
    published_on = parsed_date.isoformat()
    language = language.strip() or "en"
    if len(language) > config.MAX_LANGUAGE_LENGTH or _LANGUAGE_TAG.fullmatch(language) is None:
        raise EvidenceSourceError("Language must be a valid tag such as 'en' or 'en-US'.")
    language_parts = language.split("-")
    language = "-".join(
        [
            language_parts[0].lower(),
            *[part.upper() if len(part) == 2 else part for part in language_parts[1:]],
        ]
    )
    return {
        "publisher_id": publisher_id,
        "publisher_name": publisher_name,
        "title": title,
        "source_url": source_url,
        "document_id": document_id or None,
        "document_version": document_version,
        "published_on": published_on,
        "language": language,
    }


def validate_official_source_url(publisher_id: str, source_url: str) -> tuple[str, str, str]:
    """Return normalized publisher metadata after strict official-domain validation."""

    publisher_id = publisher_id.strip().lower()
    publisher = PUBLISHERS.get(publisher_id)
    if publisher is None:
        raise EvidenceSourceError("Choose a supported official publisher.")
    publisher_name, official_domains = publisher
    if any(ord(character) < 32 for character in source_url):
        raise EvidenceSourceError("Source URL is invalid.")
    source_url = source_url.strip()
    if (
        len(source_url) > config.MAX_SOURCE_URL_LENGTH
        or "\\" in source_url
        or any(character.isspace() for character in source_url)
    ):
        raise EvidenceSourceError("Source URL is invalid.")
    try:
        parsed = urlsplit(source_url)
        host = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
    except ValueError:
        raise EvidenceSourceError("Source URL is invalid.") from None
    if parsed.scheme.lower() != "https" or not any(
        host == domain or host.endswith(f".{domain}") for domain in official_domains
    ):
        domains = ", ".join(official_domains)
        raise EvidenceSourceError(f"Source URL must use the official HTTPS domain: {domains}.")
    if parsed.username is not None or parsed.password is not None:
        raise EvidenceSourceError("Source URL must not contain credentials.")
    if port not in (None, 443):
        raise EvidenceSourceError("Source URL must use the standard HTTPS port.")
    return publisher_id, publisher_name, source_url
