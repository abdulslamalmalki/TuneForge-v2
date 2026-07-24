"""Ingest the pinned ASUS TUF GAMING X870-PLUS WIFI exact-model manual.

Dry-run is the default. Pass ``--apply`` only after creating the verified
production backup named below. This full user manual is admitted through an
explicit reviewer exception because its short BIOS chapter contains a narrow
set of exact-model BIOS entries; it is never reclassified as a BIOS guide.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from sqlalchemy import func, select

from app.db.models import (
    ExtractionRun,
    IngestionBatchItem,
    Manual,
    ManualApplicability,
    ManualApplicabilityTarget,
    Setting,
)
from app.db.session import SessionLocal
from app.domain import batches, eligibility, extraction_pipeline, hardware, provenance_discovery
from app.domain.extraction_runs import utc_now_iso
from app.eligibility.classifier import TYPE_FULL_MANUAL, classify_document, read_bounded_sample
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
    / "E25811_TUF_GAMING_X870-PLUS_WIFI_EM_V2_WEB.pdf"
)
BACKUP_PATH = PROJECT_ROOT / "data" / "knowledge_builder.pre-asus-tuf-x870.sqlite3.backup"

EXPECTED_SHA256 = "d2ae999ad28ab15b3aa3e639df1c8025e5be055908b67f0e342894a265fb4300"
EXPECTED_PAGES = 64
EXPECTED_CANDIDATES = 345
EXPECTED_MODEL_ID = "asus-tuf-gaming-x870-plus-wifi"

VENDOR = "ASUS"
MODEL_NAME = "TUF GAMING X870-PLUS WIFI"
DOCUMENT_TITLE = "TUF GAMING X870-PLUS WIFI User Manual"
DOCUMENT_VERSION = "E25811 / V2"
SOURCE_URL = (
    "https://www.asus.com/us/supportonly/"
    "tuf%20gaming%20x870-plus%20wifi/helpdesk_manual/"
)
CHIPSET = "AMD X870"


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
        raise RuntimeError("Pinned ASUS exact-model source hash changed.")
    existing_id = session.scalar(select(Manual.id).where(Manual.sha256 == EXPECTED_SHA256))
    if existing_id is not None:
        raise RuntimeError(
            f"The ASUS exact-model manual already exists as manual {existing_id}; "
            "refusing duplicate intake."
        )
    provenance_error = support_page_error(VENDOR, SOURCE_URL)
    if provenance_error:
        raise RuntimeError(provenance_error)

    document = read_pdf_text(SOURCE_PATH)
    if document.page_count != EXPECTED_PAGES:
        raise RuntimeError(f"Expected {EXPECTED_PAGES} pages, found {document.page_count}.")
    candidates = parse_settings(document)
    if len(candidates) != EXPECTED_CANDIDATES:
        raise RuntimeError(
            f"Expected {EXPECTED_CANDIDATES} parser candidates, found {len(candidates)}."
        )
    classification = classify_document(
        filename=SOURCE_PATH.name,
        document_title=DOCUMENT_TITLE,
        page_count=document.page_count,
        sample_text=read_bounded_sample(SOURCE_PATH),
    )
    if classification.document_type != TYPE_FULL_MANUAL:
        raise RuntimeError(
            f"Expected a full-manual classification, found {classification.document_type!r}."
        )
    print(
        f"PASS {MODEL_NAME}: sha256={EXPECTED_SHA256}, pages={document.page_count}, "
        f"candidates={len(candidates)}, parser={PARSER_VERSION}, "
        f"classification={classification.document_type} ({classification.confidence}%)"
    )


def _apply(session) -> None:
    if not BACKUP_PATH.is_file():
        raise RuntimeError(f"Verified pre-ingestion backup is missing: {BACKUP_PATH}")

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
    if manual is None or manual.document_type != TYPE_FULL_MANUAL:
        raise RuntimeError("The accepted source did not retain its full-manual classification.")

    provenance_discovery.queue_discovery(session, manual)
    provenance_discovery.confirm_discovery(
        session,
        manual,
        vendor=VENDOR,
        model_name=MODEL_NAME,
        document_title=DOCUMENT_TITLE,
        source_url=SOURCE_URL,
        document_version=DOCUMENT_VERSION,
        published_on="",
        language="en",
        board_revision="",
        chipset=CHIPSET,
    )
    eligibility.review_eligibility(
        session,
        manual,
        document_type=TYPE_FULL_MANUAL,
        decision=eligibility.STATUS_ELIGIBLE,
        note=(
            "Exact-model AM5 validation exception. The official 64-page user manual "
            "contains a visually reviewed BIOS chapter on pages 45-47 and model-specific "
            "EXPO/AEMP capability on page 8. Eligibility does not imply that hardware "
            "chapters are BIOS evidence."
        ),
    )
    session.expire_all()
    manual = session.get(Manual, manual_id)
    if manual is None:
        raise RuntimeError("The ASUS source disappeared after eligibility review.")
    hardware.sync_manual_applicability(
        session,
        manual,
        actor="local-reviewer",
        created_at=utc_now_iso(),
    )
    extraction_pipeline.execute_pending_run(manual_id)
    session.expire_all()

    applicability = session.get(ManualApplicability, manual_id)
    targets = list(
        session.scalars(
            select(ManualApplicabilityTarget)
            .where(ManualApplicabilityTarget.manual_id == manual_id)
            .order_by(ManualApplicabilityTarget.ordinal)
        )
    )
    if (
        applicability is None
        or applicability.scope != "motherboard_model"
        or len(targets) != 1
        or targets[0].motherboard_model_id != EXPECTED_MODEL_ID
    ):
        raise RuntimeError("The ASUS source did not normalize to the pinned exact model.")

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
        raise RuntimeError("ASUS exact-model extraction did not match the pinned result.")
    print(
        f"INGESTED batch={batch.id} manual={manual_id} scope={applicability.scope} "
        f"target={EXPECTED_MODEL_ID} drafts={draft_count} parser={run.parser_version}"
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
