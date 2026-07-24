"""Ingest the pinned MSI AMD and GIGABYTE Intel controlled-expansion pilot.

Dry-run is the default. Pass ``--apply`` only after taking a verified backup
of the production database. The two source hashes, provenance records,
eligibility outcomes, and parser candidate counts are pinned so this operation
cannot silently ingest a different document.
"""

from __future__ import annotations

import argparse
import hashlib
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import ExtractionRun, IngestionBatchItem, Manual, Setting
from app.db.session import SessionLocal
from app.domain import batches, eligibility, extraction_pipeline, provenance_discovery
from app.eligibility.classifier import TYPE_BIOS_GUIDE, classify_document, read_bounded_sample
from app.extraction.pdf_text import read_pdf_text
from app.extraction.settings_parser import PARSER_VERSION, parse_settings
from app.provenance.official_sources import support_page_error

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CORPUS_ROOT = PROJECT_ROOT.parent
BACKUP_PATH = PROJECT_ROOT / "data" / "knowledge_builder.pre-controlled-expansion.sqlite3.backup"


@dataclass(frozen=True)
class PilotManual:
    relative_path: str
    sha256: str
    page_count: int
    candidate_count: int
    vendor: str
    model_name: str
    document_title: str
    source_url: str
    chipset: str

    @property
    def path(self) -> Path:
        return CORPUS_ROOT / self.relative_path


PILOT = (
    PilotManual(
        relative_path="assets/manuals/staging/MSI/AMDAM5BIOS.pdf",
        sha256="5ad148e9ec850442778a3cc090df281660712919299a1c4127d012f780ae622c",
        page_count=49,
        candidate_count=408,
        vendor="MSI",
        model_name="AMD AM5 series",
        document_title="AMD AM5 series BIOS User Guide",
        source_url="https://www.msi.com/Motherboard/MPG-X870E-CARBON-WIFI/support",
        chipset="AMD AM5 series",
    ),
    PilotManual(
        relative_path=("assets/manuals/staging/Gigabyte/mb_manual_intel800-bios_e_v2.pdf"),
        sha256="b46c52231334bfe914491cc3d6aa9265631914ba9c1de6bb6aa11841b46a2fe5",
        page_count=30,
        candidate_count=221,
        vendor="GIGABYTE",
        model_name="Intel 800 Series",
        document_title="BIOS Setup (Intel 800 Series)",
        source_url=(
            "https://www.gigabyte.com/Motherboard/Z890-AORUS-ELITE-WIFI7/support#support-manual"
        ),
        chipset="Intel 800 Series",
    ),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _preflight(session: Session) -> None:
    for spec in PILOT:
        if not spec.path.is_file():
            raise RuntimeError(f"Pinned source is missing: {spec.path}")
        actual_hash = _sha256(spec.path)
        if actual_hash != spec.sha256:
            raise RuntimeError(f"Pinned source hash changed: {spec.path.name}")
        existing_id = session.scalar(select(Manual.id).where(Manual.sha256 == spec.sha256))
        if existing_id is not None:
            raise RuntimeError(
                f"{spec.path.name} already exists as manual {existing_id}; "
                "refusing duplicate intake."
            )
        provenance_error = support_page_error(spec.vendor, spec.source_url)
        if provenance_error:
            raise RuntimeError(f"{spec.path.name}: {provenance_error}")

        document = read_pdf_text(spec.path)
        if document.page_count != spec.page_count:
            raise RuntimeError(
                f"{spec.path.name}: expected {spec.page_count} pages, found {document.page_count}."
            )
        candidate_count = len(parse_settings(document))
        if candidate_count != spec.candidate_count:
            raise RuntimeError(
                f"{spec.path.name}: expected {spec.candidate_count} parser candidates, "
                f"found {candidate_count}."
            )
        classification = classify_document(
            filename=spec.path.name,
            document_title=spec.path.name,
            page_count=spec.page_count,
            sample_text=read_bounded_sample(spec.path),
        )
        if (
            classification.document_type != TYPE_BIOS_GUIDE
            or not classification.eligible_recommendation
        ):
            raise RuntimeError(f"{spec.path.name}: eligibility classifier did not pass.")
        print(
            f"PASS {spec.vendor}: pages={spec.page_count}, "
            f"candidates={candidate_count}, eligibility={classification.confidence}%"
        )


def _create_batch(session: Session):
    with ExitStack() as stack:
        uploads = [
            batches.BatchUpload(
                filename=spec.path.name,
                stream=stack.enter_context(spec.path.open("rb")),
            )
            for spec in PILOT
        ]
        batch = batches.create_batch(session, uploads)
    if batch.failed_count:
        raise RuntimeError(f"Batch {batch.id} reported {batch.failed_count} failed intake item(s).")
    return batch


def _apply_workflow(session: Session) -> None:
    if not BACKUP_PATH.is_file():
        raise RuntimeError(f"Verified pre-expansion backup is missing: {BACKUP_PATH}")

    batch = _create_batch(session)
    items = list(
        session.scalars(
            select(IngestionBatchItem)
            .where(IngestionBatchItem.batch_id == batch.id)
            .order_by(IngestionBatchItem.id)
        )
    )
    if len(items) != len(PILOT) or any(item.manual_id is None for item in items):
        raise RuntimeError(f"Batch {batch.id} did not create every expected manual.")

    for spec, item in zip(PILOT, items, strict=True):
        assert item.manual_id is not None
        manual_id = item.manual_id
        eligibility.execute_classification(manual_id)
        session.expire_all()
        manual = session.get(Manual, manual_id)
        if manual is None:
            raise RuntimeError(f"Manual {manual_id} disappeared during preflight.")
        if manual.document_type != TYPE_BIOS_GUIDE:
            raise RuntimeError(
                f"Manual {manual_id} classified as {manual.document_type!r}, not a BIOS guide."
            )

        provenance_discovery.queue_discovery(session, manual)
        provenance_discovery.confirm_discovery(
            session,
            manual,
            vendor=spec.vendor,
            model_name=spec.model_name,
            document_title=spec.document_title,
            source_url=spec.source_url,
            document_version="",
            published_on="",
            language="en",
            board_revision="",
            chipset=spec.chipset,
        )
        eligibility.review_eligibility(
            session,
            manual,
            document_type=TYPE_BIOS_GUIDE,
            decision=eligibility.STATUS_ELIGIBLE,
            note=(
                "Controlled-expansion pilot: exact staged hash matched the official "
                "vendor download before reviewer confirmation."
            ),
        )
        extraction_pipeline.execute_pending_run(manual_id)
        session.expire_all()

        run = session.scalar(select(ExtractionRun).where(ExtractionRun.manual_id == manual_id))
        draft_count = session.scalar(
            select(func.count())
            .select_from(Setting)
            .where(Setting.manual_id == manual_id, Setting.status == "draft")
        )
        if (
            run is None
            or run.status != "succeeded"
            or run.parser_version != PARSER_VERSION
            or run.settings_found != spec.candidate_count
            or draft_count != spec.candidate_count
        ):
            raise RuntimeError(f"Manual {manual_id} extraction did not match the pinned result.")
        print(
            f"INGESTED manual={manual_id} vendor={spec.vendor} "
            f"drafts={draft_count} parser={run.parser_version}"
        )

    print(f"Batch {batch.id} complete: {batch.accepted_count} accepted, 0 failed.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Commit the pinned pilot")
    args = parser.parse_args()

    with SessionLocal() as session:
        _preflight(session)
        if not args.apply:
            print("Dry run only; pass --apply after verifying the production database backup.")
            return
        _apply_workflow(session)


if __name__ == "__main__":
    main()
