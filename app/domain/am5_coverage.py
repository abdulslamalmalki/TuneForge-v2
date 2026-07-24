"""Read-only coverage audit for the AMD AM5 product pilot."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import case, distinct, func, or_, select
from sqlalchemy.orm import Session

from app.db.models import (
    CanonicalSetting,
    Chipset,
    ChipsetFamily,
    GuidanceClaim,
    GuidanceClaimSetting,
    Manual,
    ManualApplicability,
    ManualApplicabilityTarget,
    MotherboardModel,
    Setting,
    SettingCanonicalMapping,
    Vendor,
)
from app.normalization.vendors import VENDORS

REPORT_VERSION = "1.0"
PLATFORM_ID = "amd-am5"

MODEL_EVIDENCE_EXACT = "exact_model"
MODEL_EVIDENCE_BROAD = "broad"
MODEL_EVIDENCE_NONE = "none"

EXPANSION_READY = "ready"
EXPANSION_HOLD = "hold"


@dataclass(frozen=True)
class Am5ManualCoverage:
    manual_id: int
    vendor_id: str
    vendor_name: str
    document_title: str
    model_name: str
    applicability_scope: str
    applicability_target_ids: tuple[str, ...]
    acceptance_certified: bool
    candidate_count: int
    approved_setting_count: int
    rejected_setting_count: int
    draft_setting_count: int
    mapped_setting_count: int
    canonical_setting_count: int


@dataclass(frozen=True)
class Am5VendorCoverage:
    vendor_id: str
    vendor_name: str
    selectable_model_count: int
    manual_count: int
    certified_manual_count: int
    approved_setting_count: int
    mapped_setting_count: int
    canonical_setting_count: int


@dataclass(frozen=True)
class Am5ModelCoverage:
    model_id: str
    vendor_id: str
    vendor_name: str
    model_name: str
    chipset_id: str
    chipset_name: str
    compatible_manual_count: int
    exact_manual_count: int
    compatible_approved_setting_count: int
    exact_approved_setting_count: int
    evidence_level: str
    evidence_note: str


@dataclass(frozen=True)
class Am5CanonicalCoverage:
    canonical_id: str
    canonical_name: str
    mapped_vendor_ids: tuple[str, ...]
    mapped_vendor_names: tuple[str, ...]
    mapped_setting_count: int
    approved_claim_count: int
    guidance_status: str


@dataclass(frozen=True)
class Am5ExpansionGate:
    gate_id: str
    label: str
    passed: bool
    required_for_expansion: bool
    detail: str


@dataclass(frozen=True)
class Am5CoverageSummary:
    major_vendor_count: int
    vendors_with_manuals: int
    vendors_with_selectable_models: int
    selectable_model_count: int
    manual_count: int
    certified_manual_count: int
    approved_setting_count: int
    draft_setting_count: int
    mapped_setting_count: int
    observed_canonical_setting_count: int
    total_canonical_catalog_count: int
    observed_canonical_with_guidance_count: int
    models_with_compatible_evidence: int
    models_with_exact_evidence: int
    expansion_status: str


@dataclass(frozen=True)
class Am5CoverageReport:
    report_version: str
    platform_id: str
    platform_name: str
    scope_notice: str
    summary: Am5CoverageSummary
    gates: tuple[Am5ExpansionGate, ...]
    vendors: tuple[Am5VendorCoverage, ...]
    models: tuple[Am5ModelCoverage, ...]
    manuals: tuple[Am5ManualCoverage, ...]
    canonical_settings: tuple[Am5CanonicalCoverage, ...]
    next_actions: tuple[str, ...]


def build_report(session: Session) -> Am5CoverageReport:
    """Project reviewed AM5 coverage without creating or changing knowledge."""
    manual_ids = _am5_manual_ids(session)
    manual_rows = _manual_rows(session, manual_ids)
    model_rows = _model_rows(session, manual_rows)
    canonical_rows = _canonical_rows(session, manual_ids)
    vendor_rows = _vendor_rows(manual_rows, model_rows, canonical_rows)
    gates = _gates(manual_rows, model_rows, vendor_rows)
    required_gates = tuple(gate for gate in gates if gate.required_for_expansion)
    expansion_status = (
        EXPANSION_READY
        if required_gates and all(gate.passed for gate in required_gates)
        else EXPANSION_HOLD
    )
    summary = Am5CoverageSummary(
        major_vendor_count=len(VENDORS),
        vendors_with_manuals=sum(row.manual_count > 0 for row in vendor_rows),
        vendors_with_selectable_models=sum(row.selectable_model_count > 0 for row in vendor_rows),
        selectable_model_count=len(model_rows),
        manual_count=len(manual_rows),
        certified_manual_count=sum(row.acceptance_certified for row in manual_rows),
        approved_setting_count=sum(row.approved_setting_count for row in manual_rows),
        draft_setting_count=sum(row.draft_setting_count for row in manual_rows),
        mapped_setting_count=sum(row.mapped_setting_count for row in manual_rows),
        observed_canonical_setting_count=len(canonical_rows),
        total_canonical_catalog_count=session.scalar(
            select(func.count()).select_from(CanonicalSetting)
        )
        or 0,
        observed_canonical_with_guidance_count=sum(
            row.approved_claim_count > 0 for row in canonical_rows
        ),
        models_with_compatible_evidence=sum(
            row.compatible_approved_setting_count > 0 for row in model_rows
        ),
        models_with_exact_evidence=sum(row.exact_approved_setting_count > 0 for row in model_rows),
        expansion_status=expansion_status,
    )
    return Am5CoverageReport(
        report_version=REPORT_VERSION,
        platform_id=PLATFORM_ID,
        platform_name="AMD AM5",
        scope_notice=(
            "Observed canonical coverage is derived only from approved settings in reviewed "
            "AM5-scoped manuals. An unobserved canonical setting is not automatically an AM5 gap."
        ),
        summary=summary,
        gates=gates,
        vendors=vendor_rows,
        models=model_rows,
        manuals=manual_rows,
        canonical_settings=canonical_rows,
        next_actions=_next_actions(vendor_rows, model_rows, canonical_rows),
    )


def _am5_manual_ids(session: Session) -> tuple[int, ...]:
    target = ManualApplicabilityTarget
    family = ChipsetFamily
    chipset = Chipset
    model = MotherboardModel
    return tuple(
        session.scalars(
            select(distinct(target.manual_id))
            .outerjoin(family, family.family_id == target.chipset_family_id)
            .outerjoin(chipset, chipset.chipset_id == target.chipset_id)
            .outerjoin(model, model.model_id == target.motherboard_model_id)
            .where(
                or_(
                    target.platform_id == PLATFORM_ID,
                    family.platform_id == PLATFORM_ID,
                    chipset.platform_id == PLATFORM_ID,
                    model.platform_id == PLATFORM_ID,
                )
            )
            .order_by(target.manual_id)
        )
    )


def _manual_rows(session: Session, manual_ids: tuple[int, ...]) -> tuple[Am5ManualCoverage, ...]:
    if not manual_ids:
        return ()
    target_ids: dict[int, list[str]] = defaultdict(list)
    for target in session.scalars(
        select(ManualApplicabilityTarget)
        .where(ManualApplicabilityTarget.manual_id.in_(manual_ids))
        .order_by(ManualApplicabilityTarget.manual_id, ManualApplicabilityTarget.ordinal)
    ):
        target_ids[target.manual_id].append(_target_id(target))

    counts = {
        row.manual_id: row
        for row in session.execute(
            select(
                Setting.manual_id.label("manual_id"),
                func.count(Setting.id).label("candidate_count"),
                func.sum(case((Setting.status == "approved", 1), else_=0)).label("approved"),
                func.sum(case((Setting.status == "rejected", 1), else_=0)).label("rejected"),
                func.sum(case((Setting.status == "draft", 1), else_=0)).label("draft"),
                func.count(
                    case(
                        (Setting.status == "approved", SettingCanonicalMapping.setting_id),
                    )
                ).label("mapped"),
                func.count(
                    distinct(
                        case(
                            (Setting.status == "approved", SettingCanonicalMapping.canonical_id),
                        )
                    )
                ).label("canonical"),
            )
            .outerjoin(
                SettingCanonicalMapping,
                SettingCanonicalMapping.setting_id == Setting.id,
            )
            .where(Setting.manual_id.in_(manual_ids))
            .group_by(Setting.manual_id)
        )
    }
    rows = session.execute(
        select(Manual, ManualApplicability, Vendor)
        .join(ManualApplicability, ManualApplicability.manual_id == Manual.id)
        .outerjoin(Vendor, Vendor.vendor_id == Manual.vendor_id)
        .where(Manual.id.in_(manual_ids))
        .order_by(Manual.id)
    )
    result = []
    for manual, applicability, vendor in rows:
        count = counts.get(manual.id)
        vendor_id = vendor.vendor_id if vendor else "unrecognized"
        vendor_name = vendor.canonical_name if vendor else (manual.vendor or "Unrecognized")
        result.append(
            Am5ManualCoverage(
                manual_id=manual.id,
                vendor_id=vendor_id,
                vendor_name=vendor_name,
                document_title=manual.document_title,
                model_name=manual.model_name or "",
                applicability_scope=applicability.scope,
                applicability_target_ids=tuple(target_ids[manual.id]),
                acceptance_certified=manual.acceptance_reviewed_at is not None,
                candidate_count=count.candidate_count if count else 0,
                approved_setting_count=count.approved if count else 0,
                rejected_setting_count=count.rejected if count else 0,
                draft_setting_count=count.draft if count else 0,
                mapped_setting_count=count.mapped if count else 0,
                canonical_setting_count=count.canonical if count else 0,
            )
        )
    return tuple(result)


def _model_rows(
    session: Session,
    manuals: tuple[Am5ManualCoverage, ...],
) -> tuple[Am5ModelCoverage, ...]:
    rows = session.execute(
        select(MotherboardModel, Vendor, Chipset, ChipsetFamily)
        .join(Vendor, Vendor.vendor_id == MotherboardModel.vendor_id)
        .join(Chipset, Chipset.chipset_id == MotherboardModel.chipset_id)
        .join(ChipsetFamily, ChipsetFamily.family_id == Chipset.family_id)
        .where(MotherboardModel.platform_id == PLATFORM_ID)
        .order_by(Vendor.canonical_name, MotherboardModel.canonical_name)
    )
    result = []
    for model, vendor, chipset, family in rows:
        compatible = tuple(
            manual
            for manual in manuals
            if _manual_matches_model(
                manual,
                vendor_id=vendor.vendor_id,
                model_id=model.model_id,
                chipset_id=chipset.chipset_id,
                family_id=family.family_id,
            )
        )
        exact = tuple(
            manual for manual in compatible if model.model_id in manual.applicability_target_ids
        )
        compatible_approved = sum(row.approved_setting_count for row in compatible)
        exact_approved = sum(row.approved_setting_count for row in exact)
        if exact_approved:
            evidence_level = MODEL_EVIDENCE_EXACT
            evidence_note = "Approved settings exist in a manual normalized to this exact model."
        elif compatible_approved:
            evidence_level = MODEL_EVIDENCE_BROAD
            evidence_note = (
                "Approved settings exist only in broader chipset-family, chipset, "
                "or platform manuals."
            )
        else:
            evidence_level = MODEL_EVIDENCE_NONE
            evidence_note = "No approved setting evidence currently covers this model."
        result.append(
            Am5ModelCoverage(
                model_id=model.model_id,
                vendor_id=vendor.vendor_id,
                vendor_name=vendor.canonical_name,
                model_name=model.canonical_name,
                chipset_id=chipset.chipset_id,
                chipset_name=chipset.canonical_name,
                compatible_manual_count=len(compatible),
                exact_manual_count=len(exact),
                compatible_approved_setting_count=compatible_approved,
                exact_approved_setting_count=exact_approved,
                evidence_level=evidence_level,
                evidence_note=evidence_note,
            )
        )
    return tuple(result)


def _canonical_rows(
    session: Session,
    manual_ids: tuple[int, ...],
) -> tuple[Am5CanonicalCoverage, ...]:
    if not manual_ids:
        return ()
    observed: dict[str, dict[str, object]] = {}
    rows = session.execute(
        select(
            CanonicalSetting.canonical_id,
            CanonicalSetting.canonical_name,
            Manual.vendor_id,
            Setting.id,
        )
        .join(
            SettingCanonicalMapping,
            SettingCanonicalMapping.canonical_id == CanonicalSetting.canonical_id,
        )
        .join(Setting, Setting.id == SettingCanonicalMapping.setting_id)
        .join(Manual, Manual.id == Setting.manual_id)
        .where(Setting.manual_id.in_(manual_ids), Setting.status == "approved")
    )
    for canonical_id, canonical_name, vendor_id, setting_id in rows:
        item = observed.setdefault(
            canonical_id,
            {"name": canonical_name, "vendor_ids": set(), "setting_ids": set()},
        )
        item["vendor_ids"].add(vendor_id or "unrecognized")
        item["setting_ids"].add(setting_id)

    claim_counts = dict(
        session.execute(
            select(GuidanceClaimSetting.canonical_id, func.count(distinct(GuidanceClaim.id)))
            .join(GuidanceClaim, GuidanceClaim.id == GuidanceClaimSetting.claim_id)
            .where(GuidanceClaim.status == "approved")
            .group_by(GuidanceClaimSetting.canonical_id)
        ).all()
    )
    vendor_names = {definition.vendor_id: definition.canonical_name for definition in VENDORS}
    vendor_names["unrecognized"] = "Unrecognized"
    result = []
    for canonical_id, item in observed.items():
        mapped_vendor_ids = tuple(sorted(item["vendor_ids"]))
        approved_claim_count = claim_counts.get(canonical_id, 0)
        result.append(
            Am5CanonicalCoverage(
                canonical_id=canonical_id,
                canonical_name=item["name"],
                mapped_vendor_ids=mapped_vendor_ids,
                mapped_vendor_names=tuple(vendor_names[item] for item in mapped_vendor_ids),
                mapped_setting_count=len(item["setting_ids"]),
                approved_claim_count=approved_claim_count,
                guidance_status="accepted_claims" if approved_claim_count else "no_accepted_claims",
            )
        )
    return tuple(
        sorted(
            result,
            key=lambda row: (
                row.approved_claim_count > 0,
                len(row.mapped_vendor_ids),
                row.canonical_name,
            ),
        )
    )


def _vendor_rows(
    manuals: tuple[Am5ManualCoverage, ...],
    models: tuple[Am5ModelCoverage, ...],
    canonical_settings: tuple[Am5CanonicalCoverage, ...],
) -> tuple[Am5VendorCoverage, ...]:
    rows = []
    for definition in VENDORS:
        vendor_manuals = tuple(row for row in manuals if row.vendor_id == definition.vendor_id)
        rows.append(
            Am5VendorCoverage(
                vendor_id=definition.vendor_id,
                vendor_name=definition.canonical_name,
                selectable_model_count=sum(row.vendor_id == definition.vendor_id for row in models),
                manual_count=len(vendor_manuals),
                certified_manual_count=sum(row.acceptance_certified for row in vendor_manuals),
                approved_setting_count=sum(row.approved_setting_count for row in vendor_manuals),
                mapped_setting_count=sum(row.mapped_setting_count for row in vendor_manuals),
                canonical_setting_count=sum(
                    definition.vendor_id in row.mapped_vendor_ids for row in canonical_settings
                ),
            )
        )
    return tuple(rows)


def _gates(
    manuals: tuple[Am5ManualCoverage, ...],
    models: tuple[Am5ModelCoverage, ...],
    vendors: tuple[Am5VendorCoverage, ...],
) -> tuple[Am5ExpansionGate, ...]:
    certified_and_reviewed = bool(manuals) and all(
        row.acceptance_certified and row.draft_setting_count == 0 for row in manuals
    )
    four_vendor_manuals = all(row.manual_count > 0 for row in vendors)
    four_vendor_models = all(row.selectable_model_count > 0 for row in vendors)
    compatible_models = bool(models) and all(
        row.compatible_approved_setting_count > 0 for row in models
    )
    exact_model_pilot = any(row.exact_approved_setting_count > 0 for row in models)
    return (
        Am5ExpansionGate(
            "review_complete",
            "Accepted AM5 manuals are fully reviewed",
            certified_and_reviewed,
            True,
            "Every AM5-scoped manual must be acceptance-certified with no draft settings.",
        ),
        Am5ExpansionGate(
            "four_vendor_manuals",
            "All four major vendors have AM5 manual coverage",
            four_vendor_manuals,
            True,
            "ASUS, MSI, GIGABYTE, and ASRock must each have reviewed AM5 source material.",
        ),
        Am5ExpansionGate(
            "four_vendor_models",
            "All four major vendors have a selectable AM5 model",
            four_vendor_models,
            True,
            "My System needs at least one reviewed motherboard identity per vendor.",
        ),
        Am5ExpansionGate(
            "compatible_model_evidence",
            "Every selectable AM5 model has approved compatible evidence",
            compatible_models,
            True,
            "Broad evidence is acceptable only when its broader scope remains visible.",
        ),
        Am5ExpansionGate(
            "exact_model_validation",
            "At least one AM5 model has approved exact-model evidence",
            exact_model_pilot,
            True,
            "One positive exact-model case is required to validate the narrowest "
            "applicability path.",
        ),
    )


def _next_actions(
    vendors: tuple[Am5VendorCoverage, ...],
    models: tuple[Am5ModelCoverage, ...],
    canonical_settings: tuple[Am5CanonicalCoverage, ...],
) -> tuple[str, ...]:
    actions = []
    missing_model_vendors = [row.vendor_name for row in vendors if not row.selectable_model_count]
    if missing_model_vendors:
        actions.append(
            "Add one reviewed AM5 motherboard identity for: "
            + ", ".join(missing_model_vendors)
            + "."
        )
    if not any(row.exact_approved_setting_count for row in models):
        actions.append(
            "Validate at least one positive exact-model AM5 manual; the current exact "
            "MSI user manual is a negative extraction case."
        )
    no_guidance = sum(row.approved_claim_count == 0 for row in canonical_settings)
    if no_guidance:
        actions.append(
            f"Review official evidence for the {no_guidance} AM5-observed canonical "
            "settings with no accepted guidance claims."
        )
    actions.append(
        "Do not classify unobserved catalog identities as AM5 omissions until "
        "applicability is reviewed."
    )
    return tuple(actions)


def _manual_matches_model(
    manual: Am5ManualCoverage,
    *,
    vendor_id: str,
    model_id: str,
    chipset_id: str,
    family_id: str,
) -> bool:
    if manual.vendor_id != vendor_id:
        return False
    targets = set(manual.applicability_target_ids)
    return bool({model_id, chipset_id, family_id, PLATFORM_ID} & targets)


def _target_id(target: ManualApplicabilityTarget) -> str:
    for value in (
        target.platform_id,
        target.chipset_family_id,
        target.chipset_id,
        target.motherboard_model_id,
    ):
        if value:
            return value
    raise RuntimeError("Manual applicability target has no identity.")
