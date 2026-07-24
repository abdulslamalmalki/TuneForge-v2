"""Shared citation-safe lookup used by the public page and JSON interface."""

from sqlalchemy.orm import Session

from app.domain import guidance_read, interface_presentation, refusal_gaps
from app.domain.errors import GuidanceReadError
from app.domain.interface_presentation import PresentationEnvelope
from app.domain.system_context import SystemContext


def build_presentation(
    session: Session,
    setting: str,
    *,
    system_context: SystemContext | None = None,
    motherboard_model_id: str = "",
    cpu_model_id: str = "",
    gap_query: str | None = None,
) -> PresentationEnvelope:
    """Resolve one exact reviewed identity or return a structured refusal."""

    try:
        explanation = guidance_read.explain_setting(
            session,
            setting,
            system_context=system_context,
        )
    except GuidanceReadError as error:
        if not error.message.startswith("No exact"):
            raise
        gap = refusal_gaps.try_record_lookup_gap(
            session,
            surface="guidance",
            reason="no_exact_guidance_match",
            query=gap_query or setting,
            context={
                "model_id": motherboard_model_id,
                "cpu_model_id": cpu_model_id,
            },
        )
        matches = (
            gap.nearest_matches if gap else refusal_gaps.nearest_canonical_matches(session, setting)
        )
        pointers = refusal_gaps.referrals_for_matches(session, matches)
        return interface_presentation.build_unresolved_envelope(
            query=gap_query or setting,
            refusal_message=error.message,
            referral_pointers=pointers,
        )

    pointers = []
    if not explanation.facts:
        gap = refusal_gaps.try_record_lookup_gap(
            session,
            surface="guidance",
            reason="no_accepted_guidance_claims",
            query=gap_query or setting,
            context={
                "canonical_id": explanation.canonical_id,
                "model_id": motherboard_model_id,
                "cpu_model_id": cpu_model_id,
            },
        )
        pointers = refusal_gaps.referrals_for_matches(
            session,
            gap.nearest_matches if gap else (),
            canonical_id=explanation.canonical_id,
        )
    return interface_presentation.build_resolved_envelope(
        explanation,
        referral_pointers=pointers,
    )
