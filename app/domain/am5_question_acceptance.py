"""Read-only acceptance projection for a fixed AM5 product question set."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.db.models import CanonicalSetting, GuidanceClaim, GuidanceClaimSetting
from app.domain import am5_coverage, guidance_read

REPORT_VERSION = "1.0"
PLATFORM_ID = "amd-am5"

CAPABILITY_ACCEPTED_CLAIMS = "accepted_claims"
CAPABILITY_RECOMMENDATION = "recommendation"
CAPABILITY_EXACT_HARDWARE = "exact_hardware"
CAPABILITY_BIOS_VERSION = "bios_version"

OUTCOME_SUPPORTED = "supported"
OUTCOME_REFUSED = "refused"

ACCEPTANCE_READY = "ready"
ACCEPTANCE_HOLD = "hold"


@dataclass(frozen=True)
class QuestionSpec:
    question_id: str
    question: str
    category: str
    capability: str
    expected_outcome: str
    canonical_id: str | None = None
    required_claim_types: tuple[str, ...] = ()


QUESTION_SET = (
    QuestionSpec(
        "pbo-behavior",
        "What does Precision Boost Overdrive do?",
        "explanation",
        CAPABILITY_ACCEPTED_CLAIMS,
        OUTCOME_SUPPORTED,
        "precision-boost-overdrive",
        ("behavior",),
    ),
    QuestionSpec(
        "pbo-scalar-behavior",
        "What does PBO Scalar do?",
        "explanation",
        CAPABILITY_ACCEPTED_CLAIMS,
        OUTCOME_SUPPORTED,
        "pbo-scalar",
        ("behavior",),
    ),
    QuestionSpec(
        "ppt-definition",
        "What does PPT represent?",
        "power-limits",
        CAPABILITY_ACCEPTED_CLAIMS,
        OUTCOME_SUPPORTED,
        "package-power-tracking-limit",
        ("definition",),
    ),
    QuestionSpec(
        "tdc-definition",
        "What does TDC represent?",
        "power-limits",
        CAPABILITY_ACCEPTED_CLAIMS,
        OUTCOME_SUPPORTED,
        "thermal-design-current-limit",
        ("definition",),
    ),
    QuestionSpec(
        "edc-definition",
        "What does EDC represent?",
        "power-limits",
        CAPABILITY_ACCEPTED_CLAIMS,
        OUTCOME_SUPPORTED,
        "electrical-design-current-limit",
        ("definition",),
    ),
    QuestionSpec(
        "curve-optimizer-modes",
        "Which Curve Optimizer modes are documented?",
        "curve-optimizer",
        CAPABILITY_ACCEPTED_CLAIMS,
        OUTCOME_SUPPORTED,
        "curve-optimizer",
        ("definition",),
    ),
    QuestionSpec(
        "curve-optimizer-unavailable",
        "What documented conditions can make Curve Optimizer unavailable?",
        "curve-optimizer",
        CAPABILITY_ACCEPTED_CLAIMS,
        OUTCOME_SUPPORTED,
        "curve-optimizer",
        ("constraint",),
    ),
    QuestionSpec(
        "curve-optimizer-safety",
        "What stability warnings are documented for Curve Optimizer?",
        "safety",
        CAPABILITY_ACCEPTED_CLAIMS,
        OUTCOME_SUPPORTED,
        "curve-optimizer",
        ("safety",),
    ),
    QuestionSpec(
        "negative-curve-optimizer",
        "What does a negative Curve Optimizer value do?",
        "curve-optimizer",
        CAPABILITY_ACCEPTED_CLAIMS,
        OUTCOME_SUPPORTED,
        "curve-optimizer",
        ("behavior",),
    ),
    QuestionSpec(
        "pbo-scalar-recommendation",
        "What PBO Scalar value should I use?",
        "recommendation",
        CAPABILITY_RECOMMENDATION,
        OUTCOME_REFUSED,
        "pbo-scalar",
    ),
    QuestionSpec(
        "exact-system-safety",
        "Is Curve Optimizer safe for my exact CPU, motherboard, and BIOS?",
        "exact-applicability",
        CAPABILITY_EXACT_HARDWARE,
        OUTCOME_REFUSED,
        "curve-optimizer",
    ),
    QuestionSpec(
        "bios-version-changes",
        "What changed in this setting in BIOS version X?",
        "firmware-version",
        CAPABILITY_BIOS_VERSION,
        OUTCOME_REFUSED,
    ),
)


@dataclass(frozen=True)
class Am5QuestionResult:
    question_id: str
    question: str
    category: str
    capability: str
    canonical_id: str | None
    canonical_name: str | None
    required_claim_types: tuple[str, ...]
    available_claim_types: tuple[str, ...]
    approved_claim_count: int
    expected_outcome: str
    actual_outcome: str
    passed: bool
    reason: str


@dataclass(frozen=True)
class Am5QuestionAcceptanceSummary:
    total_question_count: int
    expected_supported_count: int
    expected_refusal_count: int
    actual_supported_count: int
    actual_refusal_count: int
    passing_question_count: int
    unexpected_gap_count: int
    acceptance_status: str


@dataclass(frozen=True)
class Am5QuestionAcceptanceReport:
    report_version: str
    platform_id: str
    platform_name: str
    scope_notice: str
    summary: Am5QuestionAcceptanceSummary
    questions: tuple[Am5QuestionResult, ...]
    next_actions: tuple[str, ...]


def build_report(session: Session) -> Am5QuestionAcceptanceReport:
    """Evaluate the fixed question set without generating answers or changing knowledge."""
    coverage = am5_coverage.build_report(session)
    observed_ids = {row.canonical_id for row in coverage.canonical_settings}
    claim_types, claim_counts = _accepted_claim_coverage(session)
    canonical_names = _canonical_names(session)
    questions = tuple(
        _evaluate_question(
            spec,
            observed_ids=observed_ids,
            claim_types=claim_types,
            claim_counts=claim_counts,
            canonical_names=canonical_names,
        )
        for spec in QUESTION_SET
    )
    passing_count = sum(row.passed for row in questions)
    unexpected_gap_count = len(questions) - passing_count
    summary = Am5QuestionAcceptanceSummary(
        total_question_count=len(questions),
        expected_supported_count=sum(
            row.expected_outcome == OUTCOME_SUPPORTED for row in questions
        ),
        expected_refusal_count=sum(row.expected_outcome == OUTCOME_REFUSED for row in questions),
        actual_supported_count=sum(row.actual_outcome == OUTCOME_SUPPORTED for row in questions),
        actual_refusal_count=sum(row.actual_outcome == OUTCOME_REFUSED for row in questions),
        passing_question_count=passing_count,
        unexpected_gap_count=unexpected_gap_count,
        acceptance_status=ACCEPTANCE_READY if not unexpected_gap_count else ACCEPTANCE_HOLD,
    )
    return Am5QuestionAcceptanceReport(
        report_version=REPORT_VERSION,
        platform_id=PLATFORM_ID,
        platform_name="AMD AM5",
        scope_notice=(
            "This report measures whether a fixed question can be served by the required "
            "accepted claim types, or correctly refused by an explicit product boundary. "
            "It does not generate an answer or judge claim completeness from wording alone."
        ),
        summary=summary,
        questions=questions,
        next_actions=_next_actions(questions),
    )


def _accepted_claim_coverage(
    session: Session,
) -> tuple[dict[str, set[str]], dict[str, int]]:
    claim_types: dict[str, set[str]] = {}
    claim_counts: dict[str, int] = {}
    rows = session.execute(
        select(
            GuidanceClaimSetting.canonical_id,
            GuidanceClaim.claim_type,
            func.count(distinct(GuidanceClaim.id)),
        )
        .join(GuidanceClaim, GuidanceClaim.id == GuidanceClaimSetting.claim_id)
        .where(GuidanceClaim.status == "approved")
        .group_by(GuidanceClaimSetting.canonical_id, GuidanceClaim.claim_type)
    )
    for canonical_id, claim_type, count in rows:
        claim_types.setdefault(canonical_id, set()).add(claim_type)
        claim_counts[canonical_id] = claim_counts.get(canonical_id, 0) + count
    return claim_types, claim_counts


def _canonical_names(session: Session) -> dict[str, str]:
    requested_ids = {spec.canonical_id for spec in QUESTION_SET if spec.canonical_id}
    return dict(
        session.execute(
            select(CanonicalSetting.canonical_id, CanonicalSetting.canonical_name).where(
                CanonicalSetting.canonical_id.in_(requested_ids)
            )
        ).all()
    )


def _evaluate_question(
    spec: QuestionSpec,
    *,
    observed_ids: set[str],
    claim_types: dict[str, set[str]],
    claim_counts: dict[str, int],
    canonical_names: dict[str, str],
) -> Am5QuestionResult:
    available = tuple(sorted(claim_types.get(spec.canonical_id or "", set())))
    approved_claim_count = claim_counts.get(spec.canonical_id or "", 0)
    if spec.capability == CAPABILITY_ACCEPTED_CLAIMS:
        actual_outcome, reason = _accepted_claim_outcome(
            spec,
            observed_ids=observed_ids,
            available_claim_types=set(available),
        )
    elif spec.capability == CAPABILITY_RECOMMENDATION:
        actual_outcome = OUTCOME_REFUSED
        reason = guidance_read.RECOMMENDATION_NOTICE
    elif spec.capability == CAPABILITY_EXACT_HARDWARE:
        actual_outcome = OUTCOME_REFUSED
        reason = (
            "Accepted guidance remains source-scoped; TuneForge has no reviewed claim "
            "contract for an exact motherboard, CPU, and BIOS combination."
        )
    elif spec.capability == CAPABILITY_BIOS_VERSION:
        actual_outcome = OUTCOME_REFUSED
        reason = (
            "BIOS-version applicability requires an exact system version and a matching "
            "reviewed BIOS scope; this fixed acceptance query provides neither."
        )
    else:
        raise RuntimeError(f"Unknown question capability: {spec.capability}")
    return Am5QuestionResult(
        question_id=spec.question_id,
        question=spec.question,
        category=spec.category,
        capability=spec.capability,
        canonical_id=spec.canonical_id,
        canonical_name=canonical_names.get(spec.canonical_id or ""),
        required_claim_types=spec.required_claim_types,
        available_claim_types=available,
        approved_claim_count=approved_claim_count,
        expected_outcome=spec.expected_outcome,
        actual_outcome=actual_outcome,
        passed=actual_outcome == spec.expected_outcome,
        reason=reason,
    )


def _accepted_claim_outcome(
    spec: QuestionSpec,
    *,
    observed_ids: set[str],
    available_claim_types: set[str],
) -> tuple[str, str]:
    if spec.canonical_id not in observed_ids:
        return (
            OUTCOME_REFUSED,
            "No approved AM5 setting is mapped to this canonical identity.",
        )
    missing = tuple(
        claim_type
        for claim_type in spec.required_claim_types
        if claim_type not in available_claim_types
    )
    if missing:
        return (
            OUTCOME_REFUSED,
            "Missing accepted claim type"
            + ("s" if len(missing) > 1 else "")
            + ": "
            + ", ".join(missing)
            + ".",
        )
    return (
        OUTCOME_SUPPORTED,
        "The canonical setting is observed in approved AM5 evidence and every required "
        "claim type is accepted.",
    )


def _next_actions(questions: tuple[Am5QuestionResult, ...]) -> tuple[str, ...]:
    actions = []
    for row in questions:
        if row.passed or row.expected_outcome != OUTCOME_SUPPORTED:
            continue
        actions.append(
            f"Close {row.question_id}: {row.reason} Ingest and review official evidence only."
        )
    actions.append(
        "Keep recommendation, exact-system, and BIOS-version questions fail-closed until "
        "their reviewed evidence and applicability contracts exist."
    )
    return tuple(actions)
