"""Ingest the pinned GIGABYTE AM4 and Intel 600 controlled-expansion wave.

Dry-run is the default. Pass ``--apply`` only after taking the verified
production backup named below. Source hashes, page counts, provenance,
eligibility outcomes, parser version, candidate counts, and normalized
hardware applicability are pinned so a different document cannot enter the
accepted corpus silently.
"""

from __future__ import annotations

import argparse
import hashlib
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select

from app.db.models import ExtractionRun, IngestionBatchItem, Manual, Setting
from app.db.session import SessionLocal
from app.domain import (
    batches,
    eligibility,
    extraction_pipeline,
    hardware,
    provenance_discovery,
)
from app.eligibility.classifier import TYPE_BIOS_GUIDE, classify_document, read_bounded_sample
from app.extraction.pdf_text import read_pdf_text
from app.extraction.settings_parser import PARSER_VERSION, parse_settings
from app.provenance.official_sources import support_page_error

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CORPUS_ROOT = PROJECT_ROOT.parent
BACKUP_PATH = PROJECT_ROOT / "data" / "knowledge_builder.pre-gigabyte-legacy-wave.sqlite3.backup"


@dataclass(frozen=True)
class WaveManual:
    relative_path: str
    sha256: str
    page_count: int
    candidate_count: int
    model_name: str
    document_title: str
    source_url: str
    official_download_url: str
    document_version: str
    chipset: str
    expected_scope: str
    expected_targets: tuple[str, ...]
    verification_note: str

    @property
    def path(self) -> Path:
        return CORPUS_ROOT / self.relative_path


WAVE = (
    WaveManual(
        relative_path=("assets/manuals/staging/Gigabyte/mb_manual_amd-b550_a520-bios_e.pdf"),
        sha256="14814b731f11f56522bea71ea506b4182de44e3ad729169b81a075fc84f8bb90",
        page_count=19,
        candidate_count=142,
        model_name="AMD B550 / A520 Series",
        document_title="BIOS Setup (AMD B550/A520 Series)",
        source_url=(
            "https://www.gigabyte.com/Motherboard/"
            "B550-AORUS-ELITE-AX-V2-rev-10/support#support-manual"
        ),
        official_download_url=(
            "https://www.gigabyte.com/FileUpload/Global/WebPage/954/images/"
            "B550_A520%20BIOS_e_Web.pdf"
        ),
        document_version="",
        chipset="B550; A520",
        expected_scope="chipset_set",
        expected_targets=("amd-b550", "amd-a520"),
        verification_note=(
            "The official GIGABYTE PDF endpoint exposes the same 19-page title, "
            "contents, and rendered source pages as the pinned staged file."
        ),
    ),
    WaveManual(
        relative_path=("assets/manuals/staging/Gigabyte/mb_manual_600series-bios_e_v3.pdf"),
        sha256="17b5d03f17f9280f024b9ac491c9043b1ed3faa0987ea25a3cbacbf8c631b907",
        page_count=28,
        candidate_count=186,
        model_name="Intel 600 Series",
        document_title="BIOS Setup (Intel 600 Series)",
        source_url=(
            "https://www.gigabyte.com/Motherboard/Z690-AORUS-ELITE-AX-rev-14/support#support-manual"
        ),
        official_download_url=(
            "https://download.gigabyte.com/FileList/Manual/mb_manual_600series-bios_e_v3.pdf"
        ),
        document_version="v3",
        chipset="Intel 600 Series",
        expected_scope="chipset_family",
        expected_targets=("intel-600-series",),
        verification_note=(
            "The pinned staged file is byte-for-byte identical to the current "
            "official GIGABYTE download."
        ),
    ),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _preflight(session) -> None:
    for spec in WAVE:
        if not spec.path.is_file():
            raise RuntimeError(f"Pinned source is missing: {spec.path}")
        if _sha256(spec.path) != spec.sha256:
            raise RuntimeError(f"Pinned source hash changed: {spec.path.name}")
        existing_id = session.scalar(select(Manual.id).where(Manual.sha256 == spec.sha256))
        if existing_id is not None:
            raise RuntimeError(
                f"{spec.path.name} already exists as manual {existing_id}; "
                "refusing duplicate intake."
            )
        provenance_error = support_page_error("GIGABYTE", spec.source_url)
        if provenance_error:
            raise RuntimeError(f"{spec.path.name}: {provenance_error}")

        document = read_pdf_text(spec.path)
        if document.page_count != spec.page_count:
            raise RuntimeError(
                f"{spec.path.name}: expected {spec.page_count} pages, found {document.page_count}."
            )
        candidates = parse_settings(document)
        if len(candidates) != spec.candidate_count:
            raise RuntimeError(
                f"{spec.path.name}: expected {spec.candidate_count} parser candidates, "
                f"found {len(candidates)}."
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
            f"PASS {spec.path.name}: sha256={spec.sha256}, pages={spec.page_count}, "
            f"candidates={len(candidates)}, parser={PARSER_VERSION}, "
            f"eligibility={classification.confidence}%"
        )


def _create_batch(session):
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
        raise RuntimeError(f"Batch {batch.id} reported failed intake items.")
    return batch


def _apply(session) -> None:
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
    if len(items) != len(WAVE) or any(item.manual_id is None for item in items):
        raise RuntimeError(f"Batch {batch.id} did not create every expected manual.")

    for spec, item in zip(WAVE, items, strict=True):
        assert item.manual_id is not None
        manual_id = item.manual_id
        eligibility.execute_classification(manual_id)
        session.expire_all()
        manual = session.get(Manual, manual_id)
        if manual is None or manual.document_type != TYPE_BIOS_GUIDE:
            raise RuntimeError(f"Manual {manual_id} did not classify as a BIOS guide.")

        provenance_discovery.queue_discovery(session, manual)
        provenance_discovery.confirm_discovery(
            session,
            manual,
            vendor="GIGABYTE",
            model_name=spec.model_name,
            document_title=spec.document_title,
            source_url=spec.source_url,
            document_version=spec.document_version,
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
                "Controlled GIGABYTE legacy-platform expansion. "
                f"{spec.verification_note} Official PDF: {spec.official_download_url}"
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
        applicability = hardware.get_manual_applicability(session, manual_id)
        if (
            run is None
            or run.status != "succeeded"
            or run.parser_version != PARSER_VERSION
            or run.settings_found != spec.candidate_count
            or draft_count != spec.candidate_count
            or applicability is None
            or applicability.scope != spec.expected_scope
            or applicability.target_ids != spec.expected_targets
            or applicability.warnings
        ):
            raise RuntimeError(
                f"Manual {manual_id} extraction or hardware applicability "
                "did not match the pinned result."
            )
        print(
            f"INGESTED manual={manual_id} drafts={draft_count} parser={run.parser_version} "
            f"applicability={applicability.scope}:{','.join(applicability.target_ids)}"
        )

    print(f"Batch {batch.id} complete: {batch.accepted_count} accepted, 0 failed.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Commit the pinned wave")
    args = parser.parse_args()

    with SessionLocal() as session:
        _preflight(session)
        if not args.apply:
            print("Dry run only; pass --apply after verifying the production database backup.")
            return
        _apply(session)


if __name__ == "__main__":
    main()
