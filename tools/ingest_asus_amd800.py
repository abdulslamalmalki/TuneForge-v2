"""Ingest the pinned ASUS AMD 800 BIOS-guide expansion pilot.

Dry-run is the default. Pass ``--apply`` only after taking the verified
production backup named below. The source hash, page count, provenance,
eligibility result, parser version, and candidate count are pinned so a
different file cannot be ingested accidentally.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from sqlalchemy import func, select

from app.db.models import ExtractionRun, IngestionBatchItem, Manual, Setting
from app.db.session import SessionLocal
from app.domain import batches, eligibility, extraction_pipeline, provenance_discovery
from app.eligibility.classifier import TYPE_BIOS_GUIDE, classify_document, read_bounded_sample
from app.extraction.pdf_text import read_pdf_text
from app.extraction.settings_parser import PARSER_VERSION, parse_settings
from app.provenance.official_sources import support_page_error

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CORPUS_ROOT = PROJECT_ROOT.parent
SOURCE_PATH = (
    CORPUS_ROOT
    / "assets"
    / "manuals"
    / "staging"
    / "ASUS"
    / "Asus_AMD_800_Series_BIOS_manual_EM_WEB.pdf"
)
BACKUP_PATH = PROJECT_ROOT / "data" / "knowledge_builder.pre-asus-amd800.sqlite3.backup"

EXPECTED_SHA256 = "2e7a4b2d504e9f9286b584fcfbe9cba85e0ccd4a061ad545025160408b308213"
EXPECTED_PAGES = 116
EXPECTED_CANDIDATES = 1032
VENDOR = "ASUS"
MODEL_NAME = "PRIME / ProArt / TUF GAMING AMD 800 Series"
DOCUMENT_TITLE = "PRIME / ProArt / TUF GAMING AMD 800 Series BIOS Manual"
SOURCE_URL = "https://www.asus.com/us/supportonly/tuf%20gaming%20x870-plus%20wifi/helpdesk_manual/"
PUBLISHED_ON = "2024-12-20"
CHIPSET = "AMD 800 Series"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _preflight(session) -> None:
    if not SOURCE_PATH.is_file():
        raise RuntimeError(f"Pinned source is missing: {SOURCE_PATH}")
    if _sha256(SOURCE_PATH) != EXPECTED_SHA256:
        raise RuntimeError("Pinned ASUS AMD 800 source hash changed.")
    existing_id = session.scalar(select(Manual.id).where(Manual.sha256 == EXPECTED_SHA256))
    if existing_id is not None:
        raise RuntimeError(
            f"The ASUS AMD 800 guide already exists as manual {existing_id}; "
            "refusing duplicate intake."
        )
    provenance_error = support_page_error(VENDOR, SOURCE_URL)
    if provenance_error:
        raise RuntimeError(provenance_error)

    document = read_pdf_text(SOURCE_PATH)
    if document.page_count != EXPECTED_PAGES:
        raise RuntimeError(f"Expected {EXPECTED_PAGES} pages, found {document.page_count}.")
    candidate_count = len(parse_settings(document))
    if candidate_count != EXPECTED_CANDIDATES:
        raise RuntimeError(
            f"Expected {EXPECTED_CANDIDATES} parser candidates, found {candidate_count}."
        )
    classification = classify_document(
        filename=SOURCE_PATH.name,
        document_title=DOCUMENT_TITLE,
        page_count=document.page_count,
        sample_text=read_bounded_sample(SOURCE_PATH),
    )
    if (
        classification.document_type != TYPE_BIOS_GUIDE
        or not classification.eligible_recommendation
    ):
        raise RuntimeError("The ASUS AMD 800 guide did not pass eligibility preflight.")
    print(
        f"PASS {VENDOR}: sha256={EXPECTED_SHA256}, pages={document.page_count}, "
        f"candidates={candidate_count}, parser={PARSER_VERSION}, "
        f"eligibility={classification.confidence}%"
    )


def _apply(session) -> None:
    if not BACKUP_PATH.is_file():
        raise RuntimeError(f"Verified pre-expansion backup is missing: {BACKUP_PATH}")

    with SOURCE_PATH.open("rb") as stream:
        batch = batches.create_batch(
            session,
            [batches.BatchUpload(filename=SOURCE_PATH.name, stream=stream)],
        )
    item = session.scalar(select(IngestionBatchItem).where(IngestionBatchItem.batch_id == batch.id))
    if batch.accepted_count != 1 or batch.failed_count or item is None or item.manual_id is None:
        raise RuntimeError(f"Batch {batch.id} did not create the expected manual.")

    manual_id = item.manual_id
    eligibility.execute_classification(manual_id)
    session.expire_all()
    manual = session.get(Manual, manual_id)
    if manual is None or manual.document_type != TYPE_BIOS_GUIDE:
        raise RuntimeError("The accepted manual did not classify as a BIOS guide.")

    provenance_discovery.queue_discovery(session, manual)
    provenance_discovery.confirm_discovery(
        session,
        manual,
        vendor=VENDOR,
        model_name=MODEL_NAME,
        document_title=DOCUMENT_TITLE,
        source_url=SOURCE_URL,
        document_version="",
        published_on=PUBLISHED_ON,
        language="en",
        board_revision="",
        chipset=CHIPSET,
    )
    eligibility.review_eligibility(
        session,
        manual,
        document_type=TYPE_BIOS_GUIDE,
        decision=eligibility.STATUS_ELIGIBLE,
        note=(
            "ASUS AMD 800 expansion pilot: pinned local SHA-256 and rendered "
            "116-page BIOS catalog reviewed against the official ASUS support listing."
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
        or run.settings_found != EXPECTED_CANDIDATES
        or draft_count != EXPECTED_CANDIDATES
    ):
        raise RuntimeError("ASUS AMD 800 extraction did not match the pinned result.")
    print(
        f"INGESTED batch={batch.id} manual={manual_id} drafts={draft_count} "
        f"parser={run.parser_version}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Commit the pinned pilot")
    args = parser.parse_args()

    with SessionLocal() as session:
        _preflight(session)
        if not args.apply:
            print("Dry run only; pass --apply after verifying the production database backup.")
            return
        _apply(session)


if __name__ == "__main__":
    main()
