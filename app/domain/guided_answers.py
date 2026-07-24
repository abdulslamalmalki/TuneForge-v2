"""Deterministic guided answers over the fixed AM5 product-question contract."""

from dataclasses import dataclass, replace

from sqlalchemy.orm import Session

from app.db.models import CanonicalSetting
from app.domain import am5_question_acceptance, canonical_settings, guidance_read, refusal_gaps
from app.domain.errors import GuidedAnswerError
from app.domain.interface_presentation import (
    PresentationEnvelope,
    build_capability_refusal_envelope,
    build_resolved_envelope,
)
from app.domain.system_context import SystemContext


@dataclass(frozen=True)
class GuidedQuestion:
    question_id: str
    question: str
    category: str
    capability: str
    canonical_id: str | None
    canonical_name: str | None
    required_claim_types: tuple[str, ...]
    outcome: str
    reason: str


@dataclass(frozen=True)
class GuidedAnswer:
    question: GuidedQuestion
    presentation: PresentationEnvelope


def list_questions(session: Session) -> list[GuidedQuestion]:
    report = am5_question_acceptance.build_report(session)
    return [
        GuidedQuestion(
            question_id=row.question_id,
            question=row.question,
            category=row.category,
            capability=row.capability,
            canonical_id=row.canonical_id,
            canonical_name=row.canonical_name,
            required_claim_types=row.required_claim_types,
            outcome=row.actual_outcome,
            reason=row.reason,
        )
        for row in report.questions
    ]


def build_answer(
    session: Session,
    question_id: str,
    *,
    system_context: SystemContext | None = None,
) -> GuidedAnswer:
    question = next(
        (row for row in list_questions(session) if row.question_id == question_id.strip()),
        None,
    )
    if question is None:
        raise GuidedAnswerError("Guided question not found.")
    explanation = (
        guidance_read.explain_canonical_id(
            session,
            question.canonical_id,
            system_context=system_context,
        )
        if question.canonical_id
        else None
    )
    if question.outcome == am5_question_acceptance.OUTCOME_SUPPORTED:
        if explanation is None:
            raise RuntimeError("Supported guided question has no canonical explanation.")
        filtered_facts = tuple(
            fact for fact in explanation.facts if fact.claim_type in question.required_claim_types
        )
        if not filtered_facts:
            raise RuntimeError("Supported guided question has no required accepted claims.")
        focused = replace(
            explanation,
            resolved_by="guided_question",
            matched_input=question.question,
            matched_alias=None,
            facts=filtered_facts,
        )
        return GuidedAnswer(question=question, presentation=build_resolved_envelope(focused))

    identity = (
        session.get(CanonicalSetting, question.canonical_id) if question.canonical_id else None
    )
    pointers = refusal_gaps.referrals_for_matches(
        session,
        canonical_id=question.canonical_id,
    )
    equivalences = (
        canonical_settings.list_vendor_equivalences(session, (identity.canonical_id,))[
            identity.canonical_id
        ]
        if identity
        else ()
    )
    presentation = build_capability_refusal_envelope(
        query=question.question,
        refusal_message=question.reason,
        canonical_id=identity.canonical_id if identity else None,
        canonical_name=identity.canonical_name if identity else None,
        canonical_description=identity.description if identity else None,
        classification=(
            canonical_settings.get_classification(session, identity.canonical_id)
            if identity
            else None
        ),
        system_context=system_context,
        context_notice=explanation.context_notice if explanation else None,
        equivalent_vendor_names=equivalences,
        referral_pointers=pointers,
    )
    return GuidedAnswer(question=question, presentation=presentation)
