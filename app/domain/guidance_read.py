"""Read-only, citation-first projection over approved guidance claims."""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import config
from app.db.models import (
    CanonicalSetting,
    EvidencePassage,
    EvidenceSource,
    GuidanceClaim,
    GuidanceClaimEvidence,
    GuidanceClaimSetting,
    GuidanceClaimSourceSetting,
    Manual,
    Setting,
)
from app.domain import canonical_settings, guidance_claims, guidance_conflicts
from app.domain.canonical_settings import CanonicalClassificationView, VendorEquivalentName
from app.domain.errors import GuidanceReadError
from app.domain.guidance_claims import GuidanceApplicabilityTarget, GuidanceBiosScope
from app.domain.review import effective_description
from app.domain.system_context import SystemContext
from app.normalization.canonical_settings import normalize_lookup_variants

ANSWER_MODE = "accepted_claims_only"
RECOMMENDATION_STATUS = "not_available"
RECOMMENDATION_NOTICE = (
    "TuneForge does not have accepted recommendation claims for this setting and will not "
    "invent values or a tuning plan."
)
CLAIM_TYPE_ORDER = {"definition": 0, "behavior": 1, "constraint": 2, "safety": 3}
BIOS_UNSCOPED = "unscoped"
BIOS_VERSION_NOT_PROVIDED = "version_not_provided"
BIOS_MATCHED = "matched"
BIOS_NOT_APPLICABLE = "not_applicable"
BIOS_UNKNOWN = "unknown"
HARDWARE_UNSCOPED = "unscoped"
HARDWARE_CONTEXT_NOT_PROVIDED = "context_not_provided"
HARDWARE_MATCHED = "matched"
HARDWARE_NOT_APPLICABLE = "not_applicable"

ANSWER_VERIFIED = "Verified Answer"
ANSWER_PARTIAL = "Partially Verified Answer"
ANSWER_CONFLICTING = "Conflicting Evidence"
ANSWER_NONE = "No Verified Answer"


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
    bios_scopes: tuple[GuidanceBiosScope, ...] = ()
    bios_match_status: str = BIOS_UNSCOPED
    hardware_match_status: str = HARDWARE_UNSCOPED


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
    knowledge_conflicts: tuple[guidance_conflicts.ConflictOverview, ...] = ()
    answer_state: str = ANSWER_NONE
    excluded_fact_count: int = 0


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
    normalized_variants = normalize_lookup_variants(query)
    if not normalized_variants:
        raise GuidanceReadError("Enter a canonical setting name, ID, or reviewed alias.")
    rows = canonical_settings.find_alias_identity_matches(session, query)
    mapped_matches = canonical_settings.find_mapped_name_matches(session, query)
    candidate_ids = {match.canonical_id for match in mapped_matches}
    candidate_ids.update(row[1].canonical_id for row in rows)
    if len(candidate_ids) > 1:
        raise GuidanceReadError(
            "Ambiguous vendor setting name maps to multiple canonical settings; "
            "use an exact canonical ID."
        )
    if not rows and not mapped_matches:
        raise GuidanceReadError("No exact canonical setting or reviewed alias matched this lookup.")
    if not rows:
        match = mapped_matches[0]
        identity = session.get(CanonicalSetting, match.canonical_id)
        if identity is None:  # guarded by the mapping foreign key
            raise RuntimeError("Mapped canonical identity could not be loaded.")
        return identity, "mapped_vendor_name", match.matched_name
    alias, identity = rows[0]
    resolved_by = (
        "canonical_name"
        if set(normalize_lookup_variants(identity.canonical_name)).intersection(
            normalized_variants
        )
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
        bios_scopes = guidance_claims.list_bios_scopes(session, claim.id)
        facts.append(
            GuidanceReadFact(
                claim_id=claim.id,
                claim_text=claim.claim_text,
                claim_type=claim.claim_type,
                applicability_scope=claim.applicability_scope,
                applicability_targets=guidance_claims.list_applicability_targets(session, claim.id),
                bios_scopes=bios_scopes,
                bios_match_status=_bios_match_status(system_context, bios_scopes),
                citations=citations,
            )
        )
    facts = [
        GuidanceReadFact(
            claim_id=fact.claim_id,
            claim_text=fact.claim_text,
            claim_type=fact.claim_type,
            applicability_scope=fact.applicability_scope,
            citations=fact.citations,
            applicability_targets=fact.applicability_targets,
            bios_scopes=fact.bios_scopes,
            bios_match_status=fact.bios_match_status,
            hardware_match_status=_hardware_match_status(
                system_context, fact.applicability_targets
            ),
        )
        for fact in facts
    ]
    facts.sort(key=lambda item: (CLAIM_TYPE_ORDER[item.claim_type], item.claim_id))
    eligible_facts = tuple(
        fact
        for fact in facts
        if fact.hardware_match_status != HARDWARE_NOT_APPLICABLE
        and fact.bios_match_status != BIOS_NOT_APPLICABLE
    )
    excluded_fact_count = len(facts) - len(eligible_facts)
    visible_claim_ids = [fact.claim_id for fact in eligible_facts]
    knowledge_conflicts = guidance_conflicts.public_conflicts_for_claims(
        session, visible_claim_ids
    )
    scope_notice = _scope_notice(eligible_facts, excluded_fact_count=excluded_fact_count)
    if knowledge_conflicts:
        scope_notice += (
            f" {len(knowledge_conflicts)} reviewed knowledge difference(s) are attached; "
            "TuneForge preserves both claims and does not silently reconcile them."
        )
    return GuidanceExplanation(
        canonical_id=identity.canonical_id,
        canonical_name=identity.canonical_name,
        canonical_description=identity.description,
        resolved_by=resolved_by,
        matched_input=matched_input,
        matched_alias=matched_alias,
        classification=canonical_settings.get_classification(session, identity.canonical_id),
        system_context=system_context,
        context_notice=_context_notice(
            system_context, eligible_facts, excluded_fact_count=excluded_fact_count
        ),
        equivalent_vendor_names=canonical_settings.list_vendor_equivalences(
            session, (identity.canonical_id,)
        )[identity.canonical_id],
        facts=eligible_facts,
        knowledge_conflicts=knowledge_conflicts,
        answer_mode=ANSWER_MODE,
        scope_notice=scope_notice,
        recommendation_status=RECOMMENDATION_STATUS,
        recommendation_notice=RECOMMENDATION_NOTICE,
        answer_state=_answer_state(
            system_context,
            eligible_facts,
            knowledge_conflicts,
            excluded_fact_count=excluded_fact_count,
        ),
        excluded_fact_count=excluded_fact_count,
    )


def _scope_notice(
    facts: Sequence[GuidanceReadFact],
    *,
    excluded_fact_count: int = 0,
) -> str:
    if not facts:
        if excluded_fact_count:
            return (
                f"{excluded_fact_count} accepted fact(s) exist but do not match the selected "
                "reviewed hardware or BIOS-version context. TuneForge excludes them rather "
                "than generalizing their scope."
            )
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
    reviewed_bios_count = sum(bool(fact.bios_scopes) for fact in facts)
    if reviewed_target_count:
        return (
            f"{reviewed_target_count} accepted fact(s) carry explicit reviewed hardware "
            f"targets and {reviewed_bios_count} carry explicit reviewed BIOS-version "
            f"scope; other facts, if shown, retain source scope. Sources: {sources}. "
            "TuneForge does not generalize any target beyond its recorded identity."
        )
    if reviewed_bios_count:
        return (
            f"{reviewed_bios_count} accepted fact(s) carry explicit reviewed BIOS-version "
            f"scope. Sources: {sources}. TuneForge does not order opaque firmware labels "
            "or infer applicability outside recorded boundaries."
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
    *,
    excluded_fact_count: int = 0,
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
    if excluded_fact_count:
        summary += (
            f"{excluded_fact_count} accepted fact(s) were excluded because their reviewed "
            "hardware or BIOS scope does not match. "
        )
    bios_counts = {
        status: sum(fact.bios_match_status == status for fact in facts)
        for status in (BIOS_MATCHED, BIOS_NOT_APPLICABLE, BIOS_VERSION_NOT_PROVIDED, BIOS_UNKNOWN)
    }
    bios_scoped = sum(bios_counts.values())
    if not targeted:
        if bios_scoped:
            return summary + (
                "Hardware applicability remains source-scoped. "
                f"BIOS scope: {bios_counts[BIOS_MATCHED]} matched, "
                f"{bios_counts[BIOS_NOT_APPLICABLE]} not applicable, "
                f"{bios_counts[BIOS_VERSION_NOT_PROVIDED]} need a version, and "
                f"{bios_counts[BIOS_UNKNOWN]} remain informational or unorderable."
            )
        return summary + (
            "Current accepted guidance remains source-scoped and is not validated for "
            "this exact motherboard, CPU, or BIOS version."
        )
    return summary + (
        f"{matched} reviewed hardware-scoped fact(s) match this context, "
        f"{incompatible} target-scoped fact(s) do not, and {broad} fact(s) remain broad. "
        f"BIOS scope: {bios_counts[BIOS_MATCHED]} matched, "
        f"{bios_counts[BIOS_NOT_APPLICABLE]} not applicable, "
        f"{bios_counts[BIOS_VERSION_NOT_PROVIDED]} need a version, and "
        f"{bios_counts[BIOS_UNKNOWN]} remain informational or unorderable."
    )


def _bios_match_status(
    context: SystemContext | None,
    scopes: Sequence[GuidanceBiosScope],
) -> str:
    if not scopes:
        return BIOS_UNSCOPED
    if context is None:
        return BIOS_VERSION_NOT_PROVIDED
    relevant = tuple(
        scope
        for scope in scopes
        if (
            scope.context_type == "motherboard_model"
            and scope.context_id == context.motherboard_model_id
        )
        or (scope.context_type == "vendor" and scope.context_id == context.vendor_id)
    )
    if not relevant:
        return BIOS_NOT_APPLICABLE
    if not context.bios_version:
        return BIOS_VERSION_NOT_PROVIDED
    comparable = False
    for scope in relevant:
        if scope.bios_version_exact is not None:
            comparable = True
            if scope.bios_version_exact.casefold() == context.bios_version.casefold():
                return BIOS_MATCHED
            continue
        if scope.valid_from is None and scope.valid_until is None:
            continue
        comparable = True
        try:
            actual = _bios_version_key(context.bios_version, scope.comparison_mode)
        except ValueError:
            continue
        if scope.valid_from is not None and actual < _bios_version_key(
            scope.valid_from, scope.comparison_mode
        ):
            continue
        if scope.valid_until is not None and actual > _bios_version_key(
            scope.valid_until, scope.comparison_mode
        ):
            continue
        return BIOS_MATCHED
    return BIOS_NOT_APPLICABLE if comparable else BIOS_UNKNOWN


def _hardware_match_status(
    context: SystemContext | None,
    targets: Sequence[GuidanceApplicabilityTarget],
) -> str:
    if not targets:
        return HARDWARE_UNSCOPED
    if context is None:
        return HARDWARE_CONTEXT_NOT_PROVIDED
    return (
        HARDWARE_MATCHED
        if _targets_match_context(context, targets)
        else HARDWARE_NOT_APPLICABLE
    )


def _answer_state(
    context: SystemContext | None,
    facts: Sequence[GuidanceReadFact],
    conflicts: Sequence[guidance_conflicts.ConflictOverview],
    *,
    excluded_fact_count: int,
) -> str:
    if not facts:
        return ANSWER_NONE
    if any(item.conflict.status == guidance_conflicts.STATUS_CONFIRMED for item in conflicts):
        return ANSWER_CONFLICTING
    if excluded_fact_count:
        return ANSWER_PARTIAL
    if context is not None and any(
        fact.hardware_match_status in {HARDWARE_UNSCOPED, HARDWARE_CONTEXT_NOT_PROVIDED}
        or fact.bios_match_status in {BIOS_VERSION_NOT_PROVIDED, BIOS_UNKNOWN}
        for fact in facts
    ):
        return ANSWER_PARTIAL
    return ANSWER_VERIFIED


def _bios_version_key(value: str, mode: str) -> tuple[int, ...]:
    if mode == "integer_numeric" and value.isdecimal():
        return (int(value),)
    if mode == "dotted_numeric":
        parts = value.split(".")
        if len(parts) > 1 and all(part.isdecimal() for part in parts):
            return tuple(int(part) for part in parts)
    raise ValueError("BIOS version is not comparable under the reviewed comparison mode.")


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
