"""Idempotently ingest the reviewed AMD Ryzen Master 68886 v3.1.0 source."""

import argparse
import hashlib
import json
from pathlib import Path

from sqlalchemy import select

from app.db.models import EvidenceSource
from app.db.session import SessionLocal
from app.domain import evidence_sources

EXPECTED_SHA256 = "95d8fc44602cea07554fb548e11e8ba48dec0073094b82f4265b570b22281f5f"
PROVENANCE = {
    "publisher_id": "amd",
    "title": "Ryzen Master User Guide",
    "source_url": "https://docs.amd.com/r/en-US/68886-ryzen-master-user-guide",
    "document_id": "68886",
    "document_version": "3.1.0",
    "published_on": "2026-05-20",
    "language": "en",
}
REVIEW_NOTE = (
    "Official AMD publication 68886 revision 3.1.0, publication date, source URL, "
    "42-page artifact, text layer, and PBO/Curve Optimizer sections visually verified."
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", type=Path)
    args = parser.parse_args()
    path = args.pdf.resolve()
    if not path.is_file():
        raise SystemExit(f"PDF not found: {path}")
    actual_hash = file_sha256(path)
    if actual_hash != EXPECTED_SHA256:
        raise SystemExit(
            f"Refusing unexpected artifact: expected {EXPECTED_SHA256}, got {actual_hash}"
        )

    with SessionLocal() as session:
        source = session.scalar(
            select(EvidenceSource).where(EvidenceSource.sha256 == EXPECTED_SHA256)
        )
        if source is None:
            with path.open("rb") as stream:
                source = evidence_sources.create_source(
                    session,
                    stream,
                    path.name,
                    **PROVENANCE,
                )
        expected_values = {
            **PROVENANCE,
            "publisher_name": evidence_sources.PUBLISHERS["amd"][0],
            "page_count": 42,
        }
        mismatches = {
            field: {"expected": expected, "actual": getattr(source, field)}
            for field, expected in expected_values.items()
            if getattr(source, field) != expected
        }
        if mismatches:
            raise SystemExit(f"Stored source metadata mismatch: {json.dumps(mismatches)}")
        if source.status == evidence_sources.STATUS_REJECTED:
            raise SystemExit("The pinned source is rejected; refusing to alter the decision.")
        if source.status == evidence_sources.STATUS_PENDING:
            evidence_sources.review_source(
                session,
                source,
                decision=evidence_sources.STATUS_APPROVED,
                note=REVIEW_NOTE,
            )
        detail = evidence_sources.get_detail(session, source.id)
        if detail is None:
            raise SystemExit("The ingested source could not be reloaded.")
        if detail.source.status != evidence_sources.STATUS_APPROVED:
            raise SystemExit("The source is not approved.")
        if detail.extraction_run.status != "succeeded":
            raise SystemExit("The source extraction did not succeed.")
        print(
            json.dumps(
                {
                    "source_id": detail.source.id,
                    "status": detail.source.status,
                    "sha256": detail.source.sha256,
                    "pages_processed": detail.extraction_run.pages_processed,
                    "passages_found": detail.extraction_run.passages_found,
                    "events": [event.action for event in detail.events],
                },
                indent=2,
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    main()
