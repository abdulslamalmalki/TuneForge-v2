"""Seed the reviewed v0.28 official referral-pointer manifest.

Dry-run is the default. Pointers contain metadata and official links only; they
do not approve documents, passages, claims, or recommendations.
"""

import argparse
import json
from dataclasses import asdict, dataclass

from sqlalchemy import select

from app.db.models import (
    EvidencePassage,
    EvidenceSource,
    GuidanceClaim,
    GuidanceClaimEvidence,
    GuidanceClaimSetting,
    Manual,
    ReferralPointer,
)
from app.db.session import SessionLocal
from app.domain import refusal_gaps

EVIDENCE_SOURCE_IDS = (1, 2)
GENERAL_MANUAL_IDS = (2, 6, 1)
REVIEW_NOTE = (
    "Release 0.28 official referral metadata, publisher domain, document identity, and "
    "canonical scope reviewed against the existing accepted TuneForge record. Pointer "
    "registration does not admit content or claims."
)


@dataclass(frozen=True)
class PointerSeed:
    publisher_id: str
    title: str
    source_url: str
    document_id: str
    document_version: str
    canonical_ids: tuple[str, ...]


def build_manifest(session) -> tuple[PointerSeed, ...]:
    seeds: list[PointerSeed] = []
    for source_id in EVIDENCE_SOURCE_IDS:
        source = session.get(EvidenceSource, source_id)
        if source is None or source.status != "approved":
            raise RuntimeError(f"Expected approved evidence source {source_id} is unavailable.")
        canonical_ids = tuple(
            session.scalars(
                select(GuidanceClaimSetting.canonical_id)
                .join(GuidanceClaim, GuidanceClaim.id == GuidanceClaimSetting.claim_id)
                .join(
                    GuidanceClaimEvidence,
                    GuidanceClaimEvidence.claim_id == GuidanceClaim.id,
                )
                .join(
                    EvidencePassage,
                    EvidencePassage.id == GuidanceClaimEvidence.passage_id,
                )
                .where(
                    GuidanceClaim.status == "approved",
                    EvidencePassage.source_id == source.id,
                )
                .distinct()
                .order_by(GuidanceClaimSetting.canonical_id)
            )
        )
        seeds.append(
            PointerSeed(
                publisher_id=source.publisher_id,
                title=source.title,
                source_url=source.source_url,
                document_id=source.document_id or "",
                document_version=source.document_version,
                canonical_ids=canonical_ids,
            )
        )
    for manual_id in GENERAL_MANUAL_IDS:
        manual = session.get(Manual, manual_id)
        if manual is None or not manual.vendor_id or not manual.source_url:
            raise RuntimeError(f"Expected official manual {manual_id} is unavailable.")
        seeds.append(
            PointerSeed(
                publisher_id=manual.vendor_id,
                title=manual.document_title,
                source_url=manual.source_url,
                document_id="",
                document_version=manual.document_version or "",
                canonical_ids=(),
            )
        )
    return tuple(seeds)


def apply_manifest(session, manifest: tuple[PointerSeed, ...]) -> tuple[int, int]:
    created = 0
    reused = 0
    for seed in manifest:
        existing = session.scalar(
            select(ReferralPointer.id).where(
                ReferralPointer.publisher_id == seed.publisher_id,
                ReferralPointer.title == seed.title,
                ReferralPointer.source_url == seed.source_url,
            )
        )
        if existing is not None:
            reused += 1
            continue
        refusal_gaps.register_referral_pointer(
            session,
            publisher_id=seed.publisher_id,
            title=seed.title,
            source_url=seed.source_url,
            document_id=seed.document_id,
            document_version=seed.document_version,
            canonical_ids=seed.canonical_ids,
            review_note=REVIEW_NOTE,
        )
        created += 1
    return created, reused


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Persist the reviewed manifest.")
    args = parser.parse_args()
    with SessionLocal() as session:
        manifest = build_manifest(session)
        print(json.dumps([asdict(seed) for seed in manifest], indent=2))
        if not args.apply:
            print("Dry-run only; pass --apply to register these official pointers.")
            return
        created, reused = apply_manifest(session, manifest)
        print(f"Registered {created} official pointers; reused {reused} existing pointers.")


if __name__ == "__main__":
    main()
