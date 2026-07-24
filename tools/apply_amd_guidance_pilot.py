"""Apply the reviewed 0.20 AMD PBO/Curve Optimizer guidance pilot.

Dry-run is the default. The manifest contains factual, source-scope claims only;
it creates no recommendations and no CPU-model-specific applicability.
"""

import argparse
import json
from dataclasses import dataclass

from sqlalchemy import func, select

from app.db.models import (
    CanonicalSetting,
    EvidencePassage,
    EvidenceSource,
    GuidanceClaim,
    GuidanceClaimEvent,
)
from app.db.session import SessionLocal
from app.domain import guidance_claims

SOURCE_SHA256 = "95d8fc44602cea07554fb548e11e8ba48dec0073094b82f4265b570b22281f5f"
REVIEW_NOTE = (
    "Release 0.20 claim meaning, source-scope applicability, exact evidence passage, "
    "and canonical setting links reviewed against AMD publication 68886 revision 3.1.0."
)


@dataclass(frozen=True)
class ClaimSeed:
    claim_text: str
    claim_type: str
    page_number: int
    evidence_phrase: str
    canonical_ids: tuple[str, ...]


CLAIMS = (
    ClaimSeed(
        "AMD Spec mode permits adjustment of PPT, TDC, and EDC up to, but not beyond, "
        "AMD specification limits.",
        "constraint",
        26,
        "up to but not exceeding the AMD spec limits",
        (
            "pbo-limits",
            "package-power-tracking-limit",
            "thermal-design-current-limit",
            "electrical-design-current-limit",
        ),
    ),
    ClaimSeed(
        "PBO mode can operate beyond default infrastructure limits up to the motherboard's "
        "limits, potentially increasing sustained frequencies.",
        "behavior",
        26,
        "beyond the default infrastructure",
        ("precision-boost-overdrive", "pbo-limits"),
    ),
    ClaimSeed(
        "PBO Advanced mode exposes a maximum boost frequency above stock and includes Boost "
        "Override CPU and PBO Scalar controls.",
        "definition",
        26,
        "Sets a maximum boost frequency higher than the",
        (
            "cpu-boost-clock-override",
            "maximum-cpu-boost-clock-override-positive",
            "pbo-scalar",
            "pbo-scalar-control",
        ),
    ),
    ClaimSeed(
        "PPT represents total socket power.",
        "definition",
        26,
        "PPT: Total socket power",
        ("package-power-tracking-limit",),
    ),
    ClaimSeed(
        "TDC represents the sustained current limit.",
        "definition",
        26,
        "TDC: Sustained current limit",
        ("thermal-design-current-limit",),
    ),
    ClaimSeed(
        "EDC represents the peak current limit.",
        "definition",
        26,
        "EDC: Peak current limit",
        ("electrical-design-current-limit",),
    ),
    ClaimSeed(
        "Curve Optimizer can shift the CPU voltage/frequency curve toward lower voltages "
        "using negative values.",
        "behavior",
        27,
        "shift the Voltage/Frequency curve to include lower voltages",
        (
            "curve-optimizer",
            "all-core-curve-optimizer-sign",
            "per-core-curve-optimizer-sign",
        ),
    ),
    ClaimSeed(
        "A larger Curve Optimizer offset produces a larger voltage-shift magnitude.",
        "behavior",
        27,
        "magnitude of the voltage shift",
        (
            "curve-optimizer",
            "all-core-curve-optimizer-magnitude",
            "per-core-curve-optimizer-magnitude",
        ),
    ),
    ClaimSeed(
        "Curve Optimizer can be Off or configured for All Cores, Per Die, or Per Core.",
        "definition",
        27,
        "leave this mode in the OFF position",
        ("curve-optimizer",),
    ),
    ClaimSeed(
        "Curve Optimizer may not be available on every CPU supported by Ryzen Master.",
        "constraint",
        27,
        "Curve Optimizer feature may not be available for all CPUs",
        ("curve-optimizer",),
    ),
    ClaimSeed(
        "Curve Optimizer is unavailable when Manual tuning mode or Legacy Compatibility Mode "
        "is selected, or when cores are parked.",
        "constraint",
        27,
        "Curve Optimizer is not enabled when",
        ("curve-optimizer",),
    ),
    ClaimSeed(
        "Automatic Curve Optimizer is a long, invasive process that may restart the system; "
        "AMD instructs users to save work and close applications first.",
        "safety",
        27,
        "system may restart during this process",
        ("curve-optimizer",),
    ),
    ClaimSeed(
        "Curve Optimizer offset values above the default may cause additional restarts and "
        "system stability problems during optimization.",
        "safety",
        27,
        "may cause system stability issues",
        (
            "curve-optimizer",
            "all-core-curve-optimizer-magnitude",
            "per-core-curve-optimizer-magnitude",
        ),
    ),
    ClaimSeed(
        "While Curve Optimizer is running, other tuning features are unavailable and the "
        "displayed tuning controls are read-only.",
        "constraint",
        27,
        "You will not be able to use any other tuning features",
        ("curve-optimizer",),
    ),
    ClaimSeed(
        "Automatic Curve Optimizer accepts a stress-test duration from 10 to 600 in steps of 1.",
        "constraint",
        27,
        "Stress Test Duration",
        ("curve-optimizer",),
    ),
    ClaimSeed(
        "Curve Optimizer's Per Die mode applies only to multi-die CPUs.",
        "constraint",
        15,
        "Per Die (Applicable only for Multi Die CPUs)",
        ("curve-optimizer",),
    ),
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    with SessionLocal() as session:
        source = session.scalar(
            select(EvidenceSource).where(EvidenceSource.sha256 == SOURCE_SHA256)
        )
        if source is None or source.status != "approved":
            raise SystemExit("The pinned AMD evidence source is not approved.")
        canonical_ids = {value for seed in CLAIMS for value in seed.canonical_ids}
        found_ids = set(
            session.scalars(
                select(CanonicalSetting.canonical_id).where(
                    CanonicalSetting.canonical_id.in_(canonical_ids)
                )
            )
        )
        missing_ids = sorted(canonical_ids - found_ids)
        if missing_ids:
            raise SystemExit(f"Missing canonical settings: {missing_ids}")
        resolved = [(seed, _resolve_passage(session, source.id, seed)) for seed in CLAIMS]
        if not args.apply:
            print(
                json.dumps(
                    {
                        "mode": "dry-run",
                        "source_id": source.id,
                        "claims": [
                            {
                                "claim_text": seed.claim_text,
                                "claim_type": seed.claim_type,
                                "page_number": seed.page_number,
                                "passage_id": passage.id,
                                "canonical_ids": seed.canonical_ids,
                            }
                            for seed, passage in resolved
                        ],
                    },
                    indent=2,
                )
            )
            return

        claim_ids = []
        for seed, passage in resolved:
            claim = session.scalar(
                select(GuidanceClaim).where(GuidanceClaim.claim_text == seed.claim_text)
            )
            if claim is None:
                claim = guidance_claims.create_claim(
                    session,
                    claim_text=seed.claim_text,
                    claim_type=seed.claim_type,
                    passage_ids=[passage.id],
                    canonical_ids=list(seed.canonical_ids),
                    actor="release-0.20-pilot",
                )
            detail = guidance_claims.get_detail(session, claim.id)
            if detail is None:
                raise SystemExit(f"Claim {claim.id} could not be reloaded.")
            _verify_detail(seed, passage.id, detail)
            if claim.status == guidance_claims.STATUS_REJECTED:
                raise SystemExit(f"Pinned claim {claim.id} is rejected; refusing to alter it.")
            if claim.status == guidance_claims.STATUS_DRAFT:
                guidance_claims.review_claim(
                    session,
                    claim,
                    decision=guidance_claims.STATUS_APPROVED,
                    note=REVIEW_NOTE,
                )
            claim_ids.append(claim.id)

        statuses = guidance_claims.status_counts(session)
        event_count = session.scalar(
            select(func.count(GuidanceClaimEvent.id)).where(
                GuidanceClaimEvent.claim_id.in_(claim_ids)
            )
        )
        print(
            json.dumps(
                {
                    "mode": "applied",
                    "source_id": source.id,
                    "claim_ids": claim_ids,
                    "pilot_claims": len(claim_ids),
                    "pilot_events": event_count,
                    "status_counts": statuses,
                    "applicability_scope": guidance_claims.APPLICABILITY_SOURCE_SCOPE,
                    "recommendations_created": 0,
                },
                indent=2,
                sort_keys=True,
            )
        )


def _resolve_passage(
    session,
    source_id: int,
    seed: ClaimSeed,
) -> EvidencePassage:
    matches = list(
        session.scalars(
            select(EvidencePassage).where(
                EvidencePassage.source_id == source_id,
                EvidencePassage.page_number == seed.page_number,
                EvidencePassage.passage_text.contains(seed.evidence_phrase),
            )
        )
    )
    if len(matches) != 1:
        raise SystemExit(
            f"Expected one passage for page {seed.page_number} phrase "
            f"{seed.evidence_phrase!r}; found {len(matches)}."
        )
    return matches[0]


def _verify_detail(seed: ClaimSeed, passage_id: int, detail) -> None:
    actual = {
        "claim_type": detail.claim.claim_type,
        "applicability_scope": detail.claim.applicability_scope,
        "cpu_model_id": detail.claim.cpu_model_id,
        "passage_ids": [citation.passage.id for citation in detail.evidence],
        "canonical_ids": sorted(setting.canonical_id for setting in detail.settings),
    }
    expected = {
        "claim_type": seed.claim_type,
        "applicability_scope": guidance_claims.APPLICABILITY_SOURCE_SCOPE,
        "cpu_model_id": None,
        "passage_ids": [passage_id],
        "canonical_ids": sorted(seed.canonical_ids),
    }
    if actual != expected:
        raise SystemExit(
            f"Stored claim {detail.claim.id} mismatch: "
            f"{json.dumps({'expected': expected, 'actual': actual}, sort_keys=True)}"
        )


if __name__ == "__main__":
    main()
