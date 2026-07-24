"""Reviewer-gated automatic provenance discovery and its immutable audit events."""

import json
import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.context import current_actor
from app.db.models import Manual, ManualEvent
from app.db.session import SessionLocal
from app.domain import manuals as manuals_domain
from app.domain.errors import ManualProvenanceError
from app.domain.extraction_runs import utc_now_iso
from app.provenance.metadata import MetadataSuggestion, extract_metadata
from app.provenance.official_sources import support_page_error
from app.provenance.search import SearchUnavailableError, discover_support_pages
from app.storage import files

logger = logging.getLogger(__name__)

ACTOR = "provenance-discovery"
ACTION_QUEUED = "provenance_discovery_queued"
ACTION_COMPLETED = "provenance_discovery_completed"
ACTION_FAILED = "provenance_discovery_failed"
ACTION_CONFIRMED = "provenance_discovery_confirmed"
_DISCOVERY_ACTIONS = (ACTION_QUEUED, ACTION_COMPLETED, ACTION_FAILED, ACTION_CONFIRMED)


@dataclass(frozen=True)
class DiscoveryState:
    status: str
    event_id: int | None
    metadata: dict[str, str]
    candidates: list[dict[str, str | int]]
    query: str
    message: str


def queue_discovery(session: Session, manual: Manual) -> None:
    session.add(
        ManualEvent(
            manual_id=manual.id,
            action=ACTION_QUEUED,
            actor=ACTOR,
            detail=json.dumps({"status": "pending"}, sort_keys=True),
            created_at=utc_now_iso(),
        )
    )
    session.commit()


def execute_discovery(manual_id: int) -> None:
    """Background entry point: extract metadata, search, and append a result event."""
    with SessionLocal() as session:
        manual = session.get(Manual, manual_id)
        if manual is None:
            return
        try:
            extracted = extract_metadata(
                files.manual_file_path(manual.stored_filename), manual.original_filename
            )
            metadata = _merge_existing_metadata(manual, extracted)
            try:
                query, candidates = discover_support_pages(_metadata_from_dict(metadata))
                status = "suggested" if candidates else "no_match"
                message = (
                    "Review and confirm an official support page."
                    if candidates
                    else (
                        "No verified support page was found. Review the metadata and enter one "
                        "manually."
                    )
                )
            except SearchUnavailableError:
                query, candidates = "", []
                status = "search_unavailable"
                message = "Automatic search was unavailable. Review the metadata and try again."
            detail = {
                "status": status,
                "metadata": metadata,
                "candidates": [candidate.as_dict() for candidate in candidates],
                "query": query,
                "message": message,
            }
            action = ACTION_COMPLETED
        except Exception:
            logger.exception("provenance discovery failed manual_id=%s", manual_id)
            detail = {
                "status": "failed",
                "metadata": _existing_metadata(manual),
                "candidates": [],
                "query": "",
                "message": "The PDF metadata could not be analyzed. Enter provenance manually.",
            }
            action = ACTION_FAILED
        session.add(
            ManualEvent(
                manual_id=manual.id,
                action=action,
                actor=ACTOR,
                detail=json.dumps(detail, ensure_ascii=True, sort_keys=True),
                created_at=utc_now_iso(),
            )
        )
        session.commit()


def latest_discovery(session: Session, manual_id: int) -> DiscoveryState | None:
    event = session.scalar(
        select(ManualEvent)
        .where(ManualEvent.manual_id == manual_id, ManualEvent.action.in_(_DISCOVERY_ACTIONS))
        .order_by(ManualEvent.id.desc())
        .limit(1)
    )
    if event is None:
        return None
    try:
        detail = json.loads(event.detail or "{}")
    except json.JSONDecodeError:
        detail = {}
    return DiscoveryState(
        status=str(detail.get("status") or "pending"),
        event_id=event.id,
        metadata={str(key): str(value or "") for key, value in detail.get("metadata", {}).items()},
        candidates=list(detail.get("candidates", [])),
        query=str(detail.get("query") or ""),
        message=str(detail.get("message") or ""),
    )


def confirm_discovery(
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
) -> None:
    error = support_page_error(vendor, source_url)
    if error:
        raise ManualProvenanceError(error)
    manuals_domain.update_manual_provenance(
        session,
        manual,
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
    session.add(
        ManualEvent(
            manual_id=manual.id,
            action=ACTION_CONFIRMED,
            actor=current_actor(),
            detail=json.dumps(
                {
                    "status": "confirmed",
                    "source_url": source_url,
                    "submitted_vendor": vendor,
                    "vendor_id": manual.vendor_id,
                },
                sort_keys=True,
            ),
            created_at=utc_now_iso(),
        )
    )
    session.commit()


def _merge_existing_metadata(manual: Manual, extracted: MetadataSuggestion) -> dict[str, str]:
    extracted_values = extracted.as_dict()
    extracted_values.pop("extractor_version", None)
    existing = _existing_metadata(manual)
    merged = {}
    for field, suggestion in extracted_values.items():
        current = existing.get(field, "")
        if field == "document_title" and current == manual.original_filename:
            current = ""
        merged[field] = current or suggestion
    return merged


def _existing_metadata(manual: Manual) -> dict[str, str]:
    fields = (
        "vendor",
        "model_name",
        "document_title",
        "document_version",
        "published_on",
        "language",
        "board_revision",
        "chipset",
    )
    return {field: str(getattr(manual, field) or "") for field in fields}


def _metadata_from_dict(values: dict[str, str]) -> MetadataSuggestion:
    return MetadataSuggestion(
        vendor=values.get("vendor", ""),
        model_name=values.get("model_name", ""),
        document_title=values.get("document_title", ""),
        document_version=values.get("document_version", ""),
        published_on=values.get("published_on", ""),
        language=values.get("language", "en"),
        board_revision=values.get("board_revision", ""),
        chipset=values.get("chipset", ""),
    )
