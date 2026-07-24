"""Deterministic presentation packet and validator for optional model output."""

from dataclasses import dataclass

from app.domain.guidance_read import GuidanceExplanation
from app.normalization.interface_contract import INTERFACE_CONTRACT_VERSION

OUTCOME_SUPPORTED = "supported"
OUTCOME_REFUSED = "refused"


@dataclass(frozen=True)
class PresentationStatement:
    claim_id: int
    claim_type: str
    applicability_scope: str
    text: str
    citation_passage_ids: tuple[int, ...]


@dataclass(frozen=True)
class PresentationDraft:
    outcome: str
    statements: tuple[PresentationStatement, ...]
    refusal_message: str | None
    scope_notice: str
    recommendation_notice: str


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    statement_index: int | None = None


@dataclass(frozen=True)
class PresentationValidation:
    contract_version: str
    valid: bool
    issues: tuple[ValidationIssue, ...]


def build_deterministic_draft(explanation: GuidanceExplanation) -> PresentationDraft:
    statements = tuple(
        PresentationStatement(
            claim_id=fact.claim_id,
            claim_type=fact.claim_type,
            applicability_scope=fact.applicability_scope,
            text=fact.claim_text,
            citation_passage_ids=tuple(citation.passage_id for citation in fact.citations),
        )
        for fact in explanation.facts
    )
    return PresentationDraft(
        outcome=OUTCOME_SUPPORTED if statements else OUTCOME_REFUSED,
        statements=statements,
        refusal_message=None if statements else explanation.scope_notice,
        scope_notice=explanation.scope_notice,
        recommendation_notice=explanation.recommendation_notice,
    )


def validate_draft(
    explanation: GuidanceExplanation,
    draft: PresentationDraft,
) -> PresentationValidation:
    """Reject every transformation that is not an exact retrieved presentation."""

    expected = build_deterministic_draft(explanation)
    return validate_expected_draft(expected, draft)


def validate_expected_draft(
    expected: PresentationDraft,
    draft: PresentationDraft,
) -> PresentationValidation:
    """Validate an untrusted draft against a core-produced deterministic draft."""

    issues: list[ValidationIssue] = []
    if draft.outcome != expected.outcome:
        issues.append(
            ValidationIssue(
                "outcome_mismatch",
                f"Outcome must be {expected.outcome} for this retrieval envelope.",
            )
        )
    if draft.scope_notice != expected.scope_notice:
        issues.append(
            ValidationIssue("scope_notice_changed", "Scope notice must remain unchanged.")
        )
    if draft.recommendation_notice != expected.recommendation_notice:
        issues.append(
            ValidationIssue(
                "recommendation_notice_changed",
                "Recommendation notice must remain unchanged.",
            )
        )
    if draft.refusal_message != expected.refusal_message:
        issues.append(
            ValidationIssue(
                "refusal_message_mismatch",
                "Refusal message must exactly match the deterministic packet.",
            )
        )
    if len(draft.statements) != len(expected.statements):
        issues.append(
            ValidationIssue(
                "statement_count_mismatch",
                "Every retrieved accepted claim must appear exactly once.",
            )
        )
    for index, expected_statement in enumerate(expected.statements):
        if index >= len(draft.statements):
            break
        statement = draft.statements[index]
        _compare_statement(issues, index, statement, expected_statement)
    for index in range(len(expected.statements), len(draft.statements)):
        issues.append(
            ValidationIssue(
                "invented_statement",
                "The draft contains a statement not present in retrieval.",
                index,
            )
        )
    return PresentationValidation(
        contract_version=INTERFACE_CONTRACT_VERSION,
        valid=not issues,
        issues=tuple(issues),
    )


def _compare_statement(
    issues: list[ValidationIssue],
    index: int,
    actual: PresentationStatement,
    expected: PresentationStatement,
) -> None:
    comparisons = (
        (
            actual.claim_id == expected.claim_id,
            "claim_order_or_identity_changed",
            "Claim identity and deterministic order must remain unchanged.",
        ),
        (
            actual.claim_type == expected.claim_type,
            "claim_type_changed",
            "Claim type must remain unchanged.",
        ),
        (
            actual.applicability_scope == expected.applicability_scope,
            "applicability_scope_changed",
            "Applicability scope must remain unchanged.",
        ),
        (
            actual.text == expected.text,
            "claim_text_changed",
            "Statement text must exactly equal the accepted claim text.",
        ),
        (
            actual.citation_passage_ids == expected.citation_passage_ids,
            "citation_set_changed",
            "The complete ordered citation passage ID set must remain unchanged.",
        ),
    )
    for valid, code, message in comparisons:
        if not valid:
            issues.append(ValidationIssue(code, message, index))
