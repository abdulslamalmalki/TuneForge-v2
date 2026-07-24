"""Ingest the pinned ASUS Intel 600/700 controlled expansion wave.

Dry-run is the default. Pass ``--apply`` only after creating the verified
production backup named below. Source hashes, page counts, official support
pages, eligibility outcomes, and parser candidate totals are pinned so a
different document cannot be ingested silently.
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
BACKUP_PATH = PROJECT_ROOT / "data" / "knowledge_builder.pre-asus-intel-legacy.sqlite3.backup"


@dataclass(frozen=True)
class WaveManual:
    relative_path: str
    sha256: str
    page_count: int
    candidate_count: int
    model_name: str
    document_title: str
    source_url: str
    document_version: str
    chipset: str

    @property
    def path(self) -> Path:
        return CORPUS_ROOT / self.relative_path


WAVE = (
    WaveManual(
        relative_path=(
            "assets/manuals/staging/ASUS/"
            "PRIME_PROART_TUF_GAMING_Intel_600_Series_BIOS_EM_WEB_EN.pdf"
        ),
        sha256="4e3f39d81ae255a6dc8d48a52a31d0be2e496d457e3c6a17f7c689eb54cb57cc",
        page_count=88,
        candidate_count=640,
        model_name="PRIME / ProArt / TUF GAMING Intel 600 Series",
        document_title="PRIME / ProArt / TUF GAMING Intel 600 Series BIOS Manual",
        source_url=(
            "https://www.asus.com/us/motherboards-components/motherboards/prime/"
            "prime-b660-plus-d4/helpdesk_manual?model2Name=PRIME-B660-PLUS-D4"
        ),
        document_version="First Edition",
        chipset="Intel 600 Series",
    ),
    WaveManual(
        relative_path=(
            "assets/manuals/staging/ASUS/"
            "PRIME_PROART_TUF_GAMING_INTEL_700_Series_BIOS_EM_WEB_EN.pdf"
        ),
        sha256="49d55cb253e29b523cb69a78934d1df0f8160dd7feb9c68a76ceecb42927da37",
        page_count=94,
        candidate_count=663,
        model_name="PRIME / ProArt / TUF GAMING Intel 700 Series",
        document_title="PRIME / ProArt / TUF GAMING Intel 700 Series BIOS Manual",
        source_url=(
            "https://www.asus.com/motherboards-components/motherboards/tuf-gaming/"
            "tuf-gaming-b760m-plus/helpdesk_manual?model2Name=TUF-GAMING-B760M-PLUS"
        ),
        document_version="3",
        chipset="Intel 700 Series",
    ),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _preflight(session: Session, *, resume: bool) -> None:
    for spec in WAVE:
        if not spec.path.is_file():
            raise RuntimeError(f"Pinned source is missing: {spec.path}")
        if _sha256(spec.path) != spec.sha256:
            raise RuntimeError(f"Pinned source hash changed: {spec.path.name}")
        existing_id = session.scalar(select(Manual.id).where(Manual.sha256 == spec.sha256))
        if existing_id is not None:
            if not resume:
                raise RuntimeError(
                    f"{spec.path.name} already exists as manual {existing_id}; "
                    "use --resume only for the held batch created by this tool."
                )
            run = session.scalar(
                select(ExtractionRun).where(ExtractionRun.manual_id == existing_id)
            )
            setting_count = session.scalar(
                select(func.count()).select_from(Setting).where(Setting.manual_id == existing_id)
            )
            if run is None or run.status != "pending" or setting_count:
                raise RuntimeError(
                    f"Manual {existing_id} is not an untouched pending intake; refusing resume."
                )
        provenance_error = support_page_error("ASUS", spec.source_url)
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
            document_title=spec.document_title,
            page_count=spec.page_count,
            sample_text=read_bounded_sample(spec.path),
        )
        if (
            classification.document_type != TYPE_BIOS_GUIDE
            or not classification.eligible_recommendation
        ):
            raise RuntimeError(f"{spec.path.name}: eligibility classifier did not pass.")
        print(
            f"PASS ASUS {spec.chipset}: pages={spec.page_count}, "
            f"candidates={candidate_count}, eligibility={classification.confidence}%"
        )


def _create_batch(session: Session):
    with ExitStack() as stack:
        uploads = [
            batches.BatchUpload(
                filename=spec.path.name,
                stream=stack.enter_context(spec.path.open("rb")),
            )
            for spec in WAVE
        ]
        batch = batches.create_batch(session, uploads)
    if batch.failed_count:
        raise RuntimeError(f"Batch {batch.id} reported {batch.failed_count} failed item(s).")
    return batch


def _apply_workflow(session: Session, *, resume: bool) -> None:
    if not BACKUP_PATH.is_file():
        raise RuntimeError(f"Verified pre-ingestion backup is missing: {BACKUP_PATH}")

    batch = None
    if resume:
        manual_ids = [
            session.scalar(select(Manual.id).where(Manual.sha256 == spec.sha256)) for spec in WAVE
        ]
        if any(manual_id is None for manual_id in manual_ids):
            raise RuntimeError("The pinned held wave is incomplete; refusing resume.")
    else:
        batch = _create_batch(session)
        items = list(
            session.scalars(
                select(IngestionBatchItem)
                .where(IngestionBatchItem.batch_id == batch.id)
                .order_by(IngestionBatchItem.id)
            )
        )
        if len(items) != len(WAVE) or any(item.manual_id is None for item in items):
            raise RuntimeError(f"Batch {batch.id} did not create every expected manual.")
        manual_ids = [item.manual_id for item in items]

    for spec, manual_id in zip(WAVE, manual_ids, strict=True):
        assert manual_id is not None
        manual = session.get(Manual, manual_id)
        if manual is None:
            raise RuntimeError(f"Manual {manual_id} disappeared during the wave.")

        if not manual.source_url:
            provenance_discovery.queue_discovery(session, manual)
            provenance_discovery.confirm_discovery(
                session,
                manual,
                vendor="ASUS",
                model_name=spec.model_name,
                document_title=spec.document_title,
                source_url=spec.source_url,
                document_version=spec.document_version,
                published_on="",
                language="en",
                board_revision="",
                chipset=spec.chipset,
            )
        eligibility.execute_classification(manual_id)
        session.expire_all()
        manual = session.get(Manual, manual_id)
        if manual is None or manual.document_type != TYPE_BIOS_GUIDE:
            raise RuntimeError(f"Manual {manual_id} failed BIOS-guide classification.")
        eligibility.review_eligibility(
            session,
            manual,
            document_type=TYPE_BIOS_GUIDE,
            decision=eligibility.STATUS_ELIGIBLE,
            note=(
                "ASUS Intel legacy wave: exact staged SHA-256 matched the official ASUS "
                "download before reviewer confirmation."
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
            f"INGESTED manual={manual_id} chipset={spec.chipset} "
            f"drafts={draft_count} parser={run.parser_version}"
        )

    if batch is not None:
        print(f"Batch {batch.id} complete: {batch.accepted_count} accepted, 0 failed.")
    else:
        print("Resumed held batch complete: 2 manuals extracted, 0 failed.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Commit the pinned wave")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume only the untouched pending batch created by this tool",
    )
    args = parser.parse_args()

    with SessionLocal() as session:
        _preflight(session, resume=args.resume)
        if not args.apply:
            print("Dry run only; pass --apply after verifying the production database backup.")
            return
        _apply_workflow(session, resume=args.resume)


if __name__ == "__main__":
    main()
