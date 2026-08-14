"""Read-only, citation-first projection over approved guidance claims."""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import config
from app.db.models import (
    CanonicalSetting,
    CanonicalSettingAlias,
    EvidencePassage,
    EvidenceSource,
    GuidanceClaim,
    GuidanceClaimEvidence,
    GuidanceClaimSetting,
    GuidanceClaimSourceSetting,
    Manual,
    Setting,
)
from app.domain import canonical_settings, guidance_claims
from app.domain.canonical_settings import CanonicalClassificationView, VendorEquivalentName
from app.domain.errors import GuidanceReadError
from app.domain.guidance_claims import GuidanceApplicabilityTarget
from app.domain.review import effective_description
from app.domain.system_context import SystemContext
from app.normalization.canonical_settings import normalize_alias

ANSWER_MODE = "accepted_claims_only"
RECOMMENDATION_STATUS = "not_available"
RECOMMENDATION_NOTICE = (
    "TuneForge does not have accepted recommendation claims for this setting and will not "
    "invent values or a tuning plan."
)
CLAIM_TYPE_ORDER = {"definition": 0, "behavior": 1, "constraint": 2, "safety": 3}


@dataclass(frozen=True)
class GuidanceReadCitation:
    passage_id: int | None
    source_id: int
    publisher_name: str
    source_title: str
    source_url: str
    document_id: str | None
    document_version: str
    page_number: int
    section_heading: str | None
    passage_text: str
    text_sha256: str
    source_kind: str = "evidence_passage"
    setting_id: int | None = None
    manual_id: int | None = None


@dataclass(frozen=True)
class GuidanceReadFact:
    claim_id: int
    claim_text: str
    claim_type: str
    applicability_scope: str
    citations: tuple[GuidanceReadCitation, ...]
    applicability_targets: tuple[GuidanceApplicabilityTarget, ...] = ()


@dataclass(frozen=True)
class GuidanceExplanation:
    canonical_id: str
    canonical_name: str
    canonical_description: str | None
    resolved_by: str
    matched_input: str
    matched_alias: str | None
    classification: CanonicalClassificationView | None
    system_context: SystemContext | None
    context_notice: str | None
    equivalent_vendor_names: tuple[VendorEquivalentName, ...]
    facts: tuple[GuidanceReadFact, ...]
    answer_mode: str
    scope_notice: str
    recommendation_status: str
    recommendation_notice: str


@dataclass(frozen=True)
class GuidanceCoverageRow:
    canonical_id: str
    canonical_name: str
    approved_claim_count: int


def explain_setting(
    session: Session,
    query: str,
    *,
    system_context: SystemContext | None = None,
) -> GuidanceExplanation:
    identity, resolved_by, matched_alias = _resolve_identity(session, query)
    return _explanation_for_identity(
        session,
        identity,
        resolved_by=resolved_by,
        matched_input=" ".join(query.split()),
        matched_alias=matched_alias,
        system_context=system_context,
    )


def explain_canonical_id(
    session: Session,
    canonical_id: str,
    *,
    system_context: SystemContext | None = None,
) -> GuidanceExplanation | None:
    canonical_id = canonical_id.strip()
    identity = session.get(CanonicalSetting, canonical_id)
    if identity is None or identity.status != canonical_settings.IDENTITY_ACTIVE:
        return None
    return _explanation_for_identity(
        session,
        identity,
        resolved_by="canonical_id",
        matched_input=canonical_id,
        matched_alias=None,
        system_context=system_context,
    )


def list_coverage(session: Session, *, approved_only: bool = False) -> list[GuidanceCoverageRow]:
    counts = dict(
        session.execute(
            select(GuidanceClaimSetting.canonical_id, func.count(GuidanceClaim.id))
            .join(GuidanceClaim, GuidanceClaim.id == GuidanceClaimSetting.claim_id)
            .where(GuidanceClaim.status == "approved")
            .group_by(GuidanceClaimSetting.canonical_id)
        ).all()
    )
    identities = session.scalars(
        select(CanonicalSetting)
        .where(CanonicalSetting.status == canonical_settings.IDENTITY_ACTIVE)
        .order_by(CanonicalSetting.canonical_name)
    )
    rows = [
        GuidanceCoverageRow(
            canonical_id=identity.canonical_id,
            canonical_name=identity.canonical_name,
            approved_claim_count=counts.get(identity.canonical_id, 0),
        )
        for identity in identities
    ]
    return [row for row in rows if row.approved_claim_count] if approved_only else rows


def _resolve_identity(
    session: Session,
    query: str,
) -> tuple[CanonicalSetting, str, str | None]:
    query = " ".join(query.split())
    if not query:
        raise GuidanceReadError("Enter a canonical setting name, ID, or reviewed alias.")
    if len(query) > config.MAX_CANONICAL_SETTING_NAME_LENGTH:
        raise GuidanceReadError("Setting lookup is too long.")
    identity = session.get(CanonicalSetting, query)
    if identity is not None and identity.status == canonical_settings.IDENTITY_ACTIVE:
        return identity, "canonical_id", None
    normalized = normalize_alias(query)
    if not normalized:
        raise GuidanceReadError("Enter a canonical setting name, ID, or reviewed alias.")
    row = session.execute(
        select(CanonicalSettingAlias, CanonicalSetting)
        .join(
            CanonicalSetting,
            CanonicalSetting.canonical_id == CanonicalSettingAlias.canonical_id,
        )
        .where(
            CanonicalSettingAlias.normalized_alias == normalized,
            CanonicalSetting.status == canonical_settings.IDENTITY_ACTIVE,
        )
    ).first()
    mapped_matches = canonical_settings.find_mapped_name_matches(session, query)
    candidate_ids = {match.canonical_id for match in mapped_matches}
    if row is not None:
        candidate_ids.add(row[1].canonical_id)
    if len(candidate_ids) > 1:
        raise GuidanceReadError(
            "Ambiguous vendor setting name maps to multiple canonical settings; "
            "use an exact canonical ID."
        )
    if row is None and not mapped_matches:
        raise GuidanceReadError("No exact canonical setting or reviewed alias matched this lookup.")
    if row is None:
        match = mapped_matches[0]
        identity = session.get(CanonicalSetting, match.canonical_id)
        if identity is None:  # guarded by the mapping foreign key
            raise RuntimeError("Mapped canonical identity could not be loaded.")
        return identity, "mapped_vendor_name", match.matched_name
    alias, identity = row
    resolved_by = (
        "canonical_name"
        if normalize_alias(identity.canonical_name) == normalized
        else "reviewed_alias"
    )
    return identity, resolved_by, alias.display_alias


def _explanation_for_identity(
    session: Session,
    identity: CanonicalSetting,
    *,
    resolved_by: str,
    matched_input: str,
    matched_alias: str | None,
    system_context: SystemContext | None,
) -> GuidanceExplanation:
    claims = tuple(
        session.scalars(
            select(GuidanceClaim)
            .join(GuidanceClaimSetting, GuidanceClaimSetting.claim_id == GuidanceClaim.id)
            .where(
                GuidanceClaimSetting.canonical_id == identity.canonical_id,
                GuidanceClaim.status == "approved",
            )
        )
    )
    claim_ids = [claim.id for claim in claims]
    citations_by_claim: dict[int, list[GuidanceReadCitation]] = {
        claim_id: [] for claim_id in claim_ids
    }
    if claim_ids:
        rows = session.execute(
            select(GuidanceClaimEvidence.claim_id, EvidencePassage, EvidenceSource)
            .join(
                EvidencePassage,
                EvidencePassage.id == GuidanceClaimEvidence.passage_id,
            )
            .join(EvidenceSource, EvidenceSource.id == EvidencePassage.source_id)
            .where(
                GuidanceClaimEvidence.claim_id.in_(claim_ids),
                EvidenceSource.status == "approved",
            )
            .order_by(
                GuidanceClaimEvidence.claim_id,
                EvidenceSource.id,
                EvidencePassage.page_number,
                EvidencePassage.ordinal,
            )
        )
        for claim_id, passage, source in rows:
            citations_by_claim[claim_id].append(
                GuidanceReadCitation(
                    passage_id=passage.id,
                    source_id=source.id,
                    publisher_name=source.publisher_name,
                    source_title=source.title,
                    source_url=source.source_url,
                    document_id=source.document_id,
                    document_version=source.document_version,
                    page_number=passage.page_number,
                    section_heading=passage.section_heading,
                    passage_text=passage.passage_text,
                    text_sha256=passage.text_sha256,
                )
            )
        source_rows = session.execute(
            select(GuidanceClaimSourceSetting.claim_id, Setting, Manual)
            .join(Setting, Setting.id == GuidanceClaimSourceSetting.setting_id)
            .join(Manual, Manual.id == Setting.manual_id)
            .where(GuidanceClaimSourceSetting.claim_id.in_(claim_ids))
            .order_by(
                GuidanceClaimSourceSetting.claim_id,
                Manual.id,
                Setting.page_number,
                Setting.id,
            )
        )
        for claim_id, setting, manual in source_rows:
            passage_text = effective_description(setting)
            citations_by_claim[claim_id].append(
                GuidanceReadCitation(
                    passage_id=None,
                    source_id=manual.id,
                    publisher_name=manual.vendor or "Official motherboard vendor",
                    source_title=manual.document_title or manual.original_filename,
                    source_url=manual.source_url or "",
                    document_id=manual.sha256,
                    document_version=manual.document_version or "",
                    page_number=setting.edited_page_number or setting.page_number,
                    section_heading=None,
                    passage_text=passage_text,
                    text_sha256=hashlib.sha256(passage_text.encode("utf-8")).hexdigest(),
                    source_kind="approved_source_setting",
                    setting_id=setting.id,
                    manual_id=manual.id,
                )
            )
    facts = []
    for claim in claims:
        citations = tuple(
            sorted(
                citations_by_claim[claim.id],
                key=lambda item: (
                    item.source_kind,
                    item.source_id,
                    item.page_number,
                    item.passage_id or 0,
                    item.setting_id or 0,
                ),
            )
        )
        if not citations:
            raise RuntimeError(f"Approved guidance claim {claim.id} has no approved citation.")
        facts.append(
            GuidanceReadFact(
                claim_id=claim.id,
                claim_text=claim.claim_text,
                claim_type=claim.claim_type,
                applicability_scope=claim.applicability_scope,
                applicability_targets=guidance_claims.list_applicability_targets(session, claim.id),
                citations=citations,
            )
        )
    facts.sort(key=lambda item: (CLAIM_TYPE_ORDER[item.claim_type], item.claim_id))
    return GuidanceExplanation(
        canonical_id=identity.canonical_id,
        canonical_name=identity.canonical_name,
        canonical_description=identity.description,
        resolved_by=resolved_by,
        matched_input=matched_input,
        matched_alias=matched_alias,
        classification=canonical_settings.get_classification(session, identity.canonical_id),
        system_context=system_context,
        context_notice=_context_notice(system_context, facts),
        equivalent_vendor_names=canonical_settings.list_vendor_equivalences(
            session, (identity.canonical_id,)
        )[identity.canonical_id],
        facts=tuple(facts),
        answer_mode=ANSWER_MODE,
        scope_notice=_scope_notice(facts),
        recommendation_status=RECOMMENDATION_STATUS,
        recommendation_notice=RECOMMENDATION_NOTICE,
    )


def _scope_notice(facts: Sequence[GuidanceReadFact]) -> str:
    if not facts:
        return (
            "No accepted guidance claims are available for this setting, so TuneForge "
            "makes no hardware or CPU applicability claim."
        )

    source_labels: list[str] = []
    seen_sources: set[tuple[str, int]] = set()
    for fact in facts:
        for citation in fact.citations:
            source_key = (citation.source_kind, citation.source_id)
            if source_key in seen_sources:
                continue
            seen_sources.add(source_key)
            source_labels.append(f"{citation.publisher_name} — {citation.source_title}")
    sources = "; ".join(source_labels)
    reviewed_target_count = sum(bool(fact.applicability_targets) for fact in facts)
    if reviewed_target_count:
        return (
            f"{reviewed_target_count} accepted fact(s) carry explicit reviewed hardware "
            f"targets; other facts, if shown, retain source scope. Sources: {sources}. "
            "TuneForge does not generalize any target beyond its recorded identity."
        )
    if any(fact.applicability_scope == "source_scope" for fact in facts):
        return (
            f"Accepted facts retain source-scope applicability from: {sources}. "
            "Source-scope claims are not validated for a specific CPU or motherboard model."
        )
    return (
        f"Accepted facts retain the reviewed applicability stated by: {sources}. "
        "TuneForge does not infer applicability beyond each accepted claim."
    )


def _context_notice(
    context: SystemContext | None,
    facts: Sequence[GuidanceReadFact],
) -> str | None:
    if context is None:
        return None
    targeted = [fact for fact in facts if fact.applicability_targets]
    matched = sum(_targets_match_context(context, fact.applicability_targets) for fact in targeted)
    broad = len(facts) - len(targeted)
    incompatible = len(targeted) - matched
    summary = (
        f"System identity resolved as {context.motherboard_model_name} with "
        f"{context.cpu_model_name}. "
    )
    if not targeted:
        return summary + (
            "Current accepted guidance remains source-scoped and is not validated for "
            "this exact motherboard, CPU, or BIOS version."
        )
    return summary + (
        f"{matched} reviewed hardware-scoped fact(s) match this context, "
        f"{incompatible} target-scoped fact(s) do not, and {broad} fact(s) remain broad. "
        "BIOS-version applicability is still unverified."
    )


def _targets_match_context(
    context: SystemContext,
    targets: Sequence[GuidanceApplicabilityTarget],
) -> bool:
    context_ids = {
        guidance_claims.TARGET_CPU_MODEL: context.cpu_model_id,
        guidance_claims.TARGET_CPU_FAMILY: context.cpu_family_id,
        guidance_claims.TARGET_CPU_ARCHITECTURE: context.cpu_architecture_id,
        guidance_claims.TARGET_MOTHERBOARD_MODEL: context.motherboard_model_id,
        guidance_claims.TARGET_CHIPSET: context.chipset_id,
        guidance_claims.TARGET_CHIPSET_FAMILY: context.chipset_family_id,
        guidance_claims.TARGET_PLATFORM: context.platform_id,
        guidance_claims.TARGET_VENDOR: context.vendor_id,
        guidance_claims.TARGET_CPU_VENDOR: context.cpu_vendor_id,
        guidance_claims.TARGET_MOTHERBOARD_FAMILY: context.motherboard_family_id,
    }
    groups: dict[str, set[str]] = {}
    for target in targets:
        groups.setdefault(target.target_type, set()).add(target.target_id)
    return all(
        context_ids[target_type] is not None and context_ids[target_type] in target_ids
        for target_type, target_ids in groups.items()
    )
