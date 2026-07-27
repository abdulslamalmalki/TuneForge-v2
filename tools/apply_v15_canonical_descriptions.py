"""Publish reviewed v1.5 canonical descriptions without altering source evidence.

Dry-run is the default. This post-batch operation is pinned to the 15 v1.5
identities and records every changed public description in the append-only
canonical identity event stream.
"""

from __future__ import annotations

import argparse
import json

from sqlalchemy import func, select, text

from app import config
from app.db.models import (
    CanonicalIdentityEvent,
    CanonicalIdentityProposal,
    CanonicalIdentityProposalEvent,
    CanonicalSetting,
    SettingCanonicalMapping,
)
from app.db.session import SessionLocal
from app.domain import canonical_settings
from app.domain.extraction_runs import utc_now_iso
from tools.apply_v15_canonical_identity_batch import (
    ACTOR,
    DECISIONS,
    REVIEWED_DESCRIPTIONS,
    _build_report,
    _verify_backup,
    _write_reports,
)

REVISION_REASON = (
    "v1.5 reviewer-authored canonical summary derived from the cited official-manual "
    "sources; source descriptions remain unchanged."
)
CORE_RATIO_CANONICAL_ID = "core-ratio-extension-mode"
CORE_RATIO_CONFIDENCE = 9950


def _verify_post_batch(session) -> tuple[CanonicalSetting, ...]:
    revision = session.scalar(text("SELECT version_num FROM alembic_version"))
    if revision != config.EXPECTED_DATABASE_REVISION:
        raise RuntimeError(f"Unexpected database revision: {revision}")
    expected_counts = {
        CanonicalSetting: 56,
        SettingCanonicalMapping: 243,
        CanonicalIdentityProposal: 20,
    }
    for model, expected in expected_counts.items():
        found = session.scalar(select(func.count()).select_from(model))
        if found != expected:
            raise RuntimeError(f"{model.__tablename__}: expected {expected}, found {found}.")
    identities = tuple(session.get(CanonicalSetting, seed.canonical_id) for seed in DECISIONS)
    if any(identity is None or identity.status != "active" for identity in identities):
        raise RuntimeError("A pinned v1.5 identity is missing or inactive.")
    for seed in DECISIONS:
        mapping_ids = tuple(
            session.scalars(
                select(SettingCanonicalMapping.setting_id)
                .where(SettingCanonicalMapping.canonical_id == seed.canonical_id)
                .order_by(SettingCanonicalMapping.setting_id)
            )
        )
        if mapping_ids != seed.setting_ids:
            raise RuntimeError(f"Pinned mappings changed for {seed.canonical_id}.")
    return identities


def _core_ratio_projection_requires_repair(session) -> bool:
    proposal = session.scalar(
        select(CanonicalIdentityProposal).where(
            CanonicalIdentityProposal.canonical_id == CORE_RATIO_CANONICAL_ID
        )
    )
    if proposal is None or proposal.status != "approved":
        raise RuntimeError("The approved Core Ratio Extension Mode proposal is missing.")
    if proposal.confidence_basis_points == CORE_RATIO_CONFIDENCE:
        return False
    if proposal.confidence_basis_points != 9000:
        raise RuntimeError("Core Ratio Extension Mode has an unexpected proposal confidence.")
    approval = session.scalar(
        select(CanonicalIdentityProposalEvent)
        .where(
            CanonicalIdentityProposalEvent.proposal_id == proposal.id,
            CanonicalIdentityProposalEvent.action == "approved",
        )
        .order_by(CanonicalIdentityProposalEvent.id.desc())
    )
    if approval is None:
        raise RuntimeError("Core Ratio Extension Mode approval evidence is missing.")
    approval_evidence = json.loads(approval.detail)
    if approval_evidence.get(
        "confidence_basis_points"
    ) != CORE_RATIO_CONFIDENCE or approval_evidence.get("setting_ids") != [5543, 7406, 8057]:
        raise RuntimeError("Core Ratio Extension Mode approval evidence changed.")
    return True


def _repair_core_ratio_projection(session) -> bool:
    if not _core_ratio_projection_requires_repair(session):
        return False
    proposal = session.scalar(
        select(CanonicalIdentityProposal).where(
            CanonicalIdentityProposal.canonical_id == CORE_RATIO_CANONICAL_ID
        )
    )
    before_evidence = json.loads(proposal.evidence_json)
    expected_paths = [
        "Ai Tweaker > Tweaker's Paradise > Core Ratio Extension Mode",
        "Tweaker > Advanced CPU Settings > Core Ratio Extension Mode",
    ]
    if (
        before_evidence.get("setting_ids") != [5543, 7406, 8057]
        or before_evidence.get("reviewed_menu_paths") != expected_paths
    ):
        raise RuntimeError("Core Ratio Extension Mode proposal projection changed.")
    after_evidence = {
        **before_evidence,
        "automatic_eligible": True,
        "confidence_basis_points": CORE_RATIO_CONFIDENCE,
        "reasons": [
            "Exact normalized name agrees across multiple official manuals.",
            "Every reviewed description independently expresses the same function "
            "(minimum deterministic similarity 0.914).",
            "Evidence spans 3 manuals and 2 vendors.",
            "Reviewed BIOS menu context is recorded for every source.",
            "Vendor menu hierarchies differ and require the proposal reviewer's "
            "explicit equivalence decision.",
            "Every source passes official provenance and citation review.",
        ],
    }
    now = utc_now_iso()
    proposal.confidence_basis_points = CORE_RATIO_CONFIDENCE
    proposal.evidence_json = json.dumps(after_evidence, sort_keys=True)
    proposal.updated_at = now
    session.add(
        CanonicalIdentityProposalEvent(
            proposal_id=proposal.id,
            action="projection_repaired",
            actor=ACTOR,
            detail=json.dumps(
                {
                    "before_confidence_basis_points": 9000,
                    "after_confidence_basis_points": CORE_RATIO_CONFIDENCE,
                    "reason": (
                        "The final commit=False menu context was not flushed before the "
                        "initial proposal snapshot. The approval event already records the "
                        "complete 99.50% evidence group; this repairs only the projection."
                    ),
                },
                sort_keys=True,
            ),
            created_at=now,
        )
    )
    session.flush()
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Commit reviewed descriptions")
    args = parser.parse_args()

    _verify_backup()
    with SessionLocal() as session:
        identities = _verify_post_batch(session)
        changed = tuple(
            identity
            for identity in identities
            if identity.description != REVIEWED_DESCRIPTIONS[identity.canonical_id]
        )
        projection_repair = _core_ratio_projection_requires_repair(session)
        print(f"Reviewed descriptions requiring publication: {len(changed)}")
        print(f"Proposal projections requiring repair: {int(projection_repair)}")
        if not args.apply:
            print("Dry run only; pass --apply to commit the reviewed descriptions.")
            return 0
        before_events = int(
            session.scalar(select(func.count()).select_from(CanonicalIdentityEvent)) or 0
        )
        try:
            for identity in changed:
                canonical_settings.revise_identity_description(
                    session,
                    identity,
                    description=REVIEWED_DESCRIPTIONS[identity.canonical_id],
                    reason=REVISION_REASON,
                    actor=ACTOR,
                    commit=False,
                )
            repaired = _repair_core_ratio_projection(session)
            after_events = int(
                session.scalar(select(func.count()).select_from(CanonicalIdentityEvent)) or 0
            )
            if after_events - before_events != len(changed):
                raise RuntimeError("Canonical description audit event count is incorrect.")
            integrity = session.scalar(text("PRAGMA integrity_check"))
            if integrity != "ok":
                raise RuntimeError(f"SQLite integrity check failed: {integrity}")
            session.commit()
        except Exception:
            session.rollback()
            raise

        payload = _build_report(session, generated_at=utc_now_iso())
        _write_reports(payload)
        print(f"Published reviewed descriptions: {len(changed)}")
        print(f"Repaired proposal projections: {int(repaired)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
