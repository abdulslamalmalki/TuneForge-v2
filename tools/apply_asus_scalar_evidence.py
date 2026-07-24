"""Apply the reviewed ASUS PBO Scalar evidence and behavior claim.

Dry-run is the default. The source and claim are pinned to the already reviewed
production manual artifact, exact page text, and canonical setting. Repeated
application is idempotent and never creates a recommendation.
"""

import argparse
import hashlib
import json
from dataclasses import dataclass

from sqlalchemy import func, select

from app import config
from app.db.models import (
    CanonicalSetting,
    EvidencePassage,
    EvidenceSource,
    EvidenceSourceEvent,
    GuidanceClaim,
    GuidanceClaimEvent,
    Manual,
)
from app.db.session import SessionLocal
from app.domain import am5_question_acceptance, evidence_sources, guidance_claims

RELEASE = "0.27"
MANUAL_SHA256 = "2e7a4b2d504e9f9286b584fcfbe9cba85e0ccd4a061ad545025160408b308213"
PUBLISHER_ID = "asus"
TITLE = "PRIME / ProArt / TUF GAMING AMD 800 Series BIOS Manual"
SOURCE_URL = "https://www.asus.com/us/supportonly/tuf%20gaming%20x870-plus%20wifi/helpdesk_manual/"
DOCUMENT_ID = "E25269"
DOCUMENT_VERSION = "First Edition"
PUBLISHED_ON = "2024-12-20"
LANGUAGE = "en"
PAGE_NUMBER = 30
EVIDENCE_PHRASE = (
    "The larger the value entered the larger the boost voltage used and the longer that "
    "voltage will be maintained."
)
CANONICAL_ID = "pbo-scalar"
CLAIM_TEXT = (
    "In the documented ASUS AMD 800 Series BIOS, a larger Customized PBO Scalar value "
    "uses a larger boost voltage above the part's specified maximum and maintains that "
    "voltage longer."
)
SOURCE_REVIEW_NOTE = (
    "Release 0.27 official ASUS support URL, E25269 First Edition metadata, and page 30 "
    "PBO Scalar passage visually reviewed against the accepted manual artifact."
)
CLAIM_REVIEW_NOTE = (
    "Release 0.27 claim meaning, source-scope applicability, exact page 30 evidence, and "
    "PBO Scalar canonical link reviewed. No value recommendation or cross-vendor "
    "applicability is inferred."
)


@dataclass(frozen=True)
class ApplyResult:
    mode: str
    manual_id: int
    source_id: int | None
    source_action: str
    passage_id: int | None
    claim_id: int | None
    claim_action: str
    question_status: str
    passing_questions: int
    total_questions: int
    unexpected_gaps: int
    recommendations_created: int


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    with SessionLocal() as session:
        result = apply_scalar_evidence(session, apply=args.apply)
        print(json.dumps(result.__dict__, indent=2, sort_keys=True))


def apply_scalar_evidence(session, *, apply: bool) -> ApplyResult:
    manual = _pinned_manual(session)
    _verify_manual_file(manual)
    if session.get(CanonicalSetting, CANONICAL_ID) is None:
        raise SystemExit(f"Missing canonical setting: {CANONICAL_ID}")

    source = session.scalar(select(EvidenceSource).where(EvidenceSource.sha256 == MANUAL_SHA256))
    source_action = "reuse"
    if source is None:
        source_action = "create_and_approve"
        if apply:
            source = _create_and_approve_source(session, manual)
    else:
        _verify_source(source)
        if source.status == evidence_sources.STATUS_REJECTED:
            raise SystemExit(f"Pinned evidence source {source.id} is rejected.")
        if source.status == evidence_sources.STATUS_PENDING:
            source_action = "approve"
            if apply:
                evidence_sources.review_source(
                    session,
                    source,
                    decision=evidence_sources.STATUS_APPROVED,
                    note=SOURCE_REVIEW_NOTE,
                )

    passage = (
        _resolve_passage(session, source.id)
        if source is not None and source.status == "approved"
        else None
    )
    claim = session.scalar(select(GuidanceClaim).where(GuidanceClaim.claim_text == CLAIM_TEXT))
    claim_action = "reuse"
    if claim is None:
        claim_action = "create_and_approve"
        if apply:
            if passage is None:
                raise SystemExit("The approved Scalar evidence passage is unavailable.")
            claim = guidance_claims.create_claim(
                session,
                claim_text=CLAIM_TEXT,
                claim_type="behavior",
                passage_ids=[passage.id],
                canonical_ids=[CANONICAL_ID],
                actor="release-0.27-scalar-evidence",
            )
    if claim is not None:
        if passage is None:
            raise SystemExit("The pinned Scalar claim exists without its approved passage.")
        _verify_claim(session, claim, passage.id)
        if claim.status == guidance_claims.STATUS_REJECTED:
            raise SystemExit(f"Pinned Scalar claim {claim.id} is rejected.")
        if claim.status == guidance_claims.STATUS_DRAFT:
            claim_action = "approve" if claim_action == "reuse" else claim_action
            if apply:
                guidance_claims.review_claim(
                    session,
                    claim,
                    decision=guidance_claims.STATUS_APPROVED,
                    note=CLAIM_REVIEW_NOTE,
                )

    report = am5_question_acceptance.build_report(session)
    return ApplyResult(
        mode="applied" if apply else "dry-run",
        manual_id=manual.id,
        source_id=source.id if source is not None else None,
        source_action=source_action,
        passage_id=passage.id if passage is not None else None,
        claim_id=claim.id if claim is not None else None,
        claim_action=claim_action,
        question_status=report.summary.acceptance_status,
        passing_questions=report.summary.passing_question_count,
        total_questions=report.summary.total_question_count,
        unexpected_gaps=report.summary.unexpected_gap_count,
        recommendations_created=0,
    )


def audit_counts(session, source_id: int, claim_id: int) -> dict[str, int]:
    return {
        "source_events": session.scalar(
            select(func.count(EvidenceSourceEvent.id)).where(
                EvidenceSourceEvent.source_id == source_id
            )
        )
        or 0,
        "claim_events": session.scalar(
            select(func.count(GuidanceClaimEvent.id)).where(GuidanceClaimEvent.claim_id == claim_id)
        )
        or 0,
    }


def _pinned_manual(session) -> Manual:
    manuals = list(session.scalars(select(Manual).where(Manual.sha256 == MANUAL_SHA256)))
    if len(manuals) != 1:
        raise SystemExit(f"Expected one pinned ASUS manual; found {len(manuals)}.")
    manual = manuals[0]
    expected = {
        "vendor_id": PUBLISHER_ID,
        "document_title": TITLE,
        "source_url": SOURCE_URL,
        "published_on": PUBLISHED_ON,
        "language": LANGUAGE,
    }
    actual = {field: getattr(manual, field) for field in expected}
    if actual != expected:
        raise SystemExit(
            "Pinned manual metadata mismatch: "
            + json.dumps({"expected": expected, "actual": actual}, sort_keys=True)
        )
    return manual


def _verify_manual_file(manual: Manual) -> None:
    path = config.UPLOADS_DIR / manual.stored_filename
    if not path.is_file():
        raise SystemExit(f"Pinned manual artifact is missing: {path}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != MANUAL_SHA256:
        raise SystemExit(f"Pinned manual artifact SHA-256 mismatch: {digest}")


def _create_and_approve_source(session, manual: Manual) -> EvidenceSource:
    path = config.UPLOADS_DIR / manual.stored_filename
    with path.open("rb") as stream:
        source = evidence_sources.create_source(
            session,
            stream,
            manual.original_filename,
            publisher_id=PUBLISHER_ID,
            title=TITLE,
            source_url=SOURCE_URL,
            document_id=DOCUMENT_ID,
            document_version=DOCUMENT_VERSION,
            published_on=PUBLISHED_ON,
            language=LANGUAGE,
        )
    evidence_sources.review_source(
        session,
        source,
        decision=evidence_sources.STATUS_APPROVED,
        note=SOURCE_REVIEW_NOTE,
    )
    return source


def _verify_source(source: EvidenceSource) -> None:
    expected = {
        "publisher_id": PUBLISHER_ID,
        "title": TITLE,
        "source_url": SOURCE_URL,
        "document_id": DOCUMENT_ID,
        "document_version": DOCUMENT_VERSION,
        "published_on": PUBLISHED_ON,
        "language": LANGUAGE,
        "source_type": "official_document",
        "trust_tier": "primary_official",
    }
    actual = {field: getattr(source, field) for field in expected}
    if actual != expected:
        raise SystemExit(
            "Pinned evidence source metadata mismatch: "
            + json.dumps({"expected": expected, "actual": actual}, sort_keys=True)
        )


def _resolve_passage(session, source_id: int) -> EvidencePassage:
    page_passages = list(
        session.scalars(
            select(EvidencePassage).where(
                EvidencePassage.source_id == source_id,
                EvidencePassage.page_number == PAGE_NUMBER,
            )
        )
    )
    expected_phrase = " ".join(EVIDENCE_PHRASE.split()).casefold()
    matches = [
        passage
        for passage in page_passages
        if expected_phrase in " ".join(passage.passage_text.split()).casefold()
    ]
    if len(matches) != 1:
        raise SystemExit(
            f"Expected one Scalar passage on page {PAGE_NUMBER}; found {len(matches)}."
        )
    return matches[0]


def _verify_claim(session, claim: GuidanceClaim, passage_id: int) -> None:
    detail = guidance_claims.get_detail(session, claim.id)
    if detail is None:
        raise SystemExit(f"Scalar claim {claim.id} could not be reloaded.")
    actual = {
        "claim_type": claim.claim_type,
        "applicability_scope": claim.applicability_scope,
        "cpu_model_id": claim.cpu_model_id,
        "passage_ids": [citation.passage.id for citation in detail.evidence],
        "canonical_ids": [setting.canonical_id for setting in detail.settings],
    }
    expected = {
        "claim_type": "behavior",
        "applicability_scope": guidance_claims.APPLICABILITY_SOURCE_SCOPE,
        "cpu_model_id": None,
        "passage_ids": [passage_id],
        "canonical_ids": [CANONICAL_ID],
    }
    if actual != expected:
        raise SystemExit(
            "Pinned Scalar claim mismatch: "
            + json.dumps({"expected": expected, "actual": actual}, sort_keys=True)
        )


if __name__ == "__main__":
    main()
