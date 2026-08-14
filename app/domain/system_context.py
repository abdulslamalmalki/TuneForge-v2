"""Read-only exact-system context for the AMD AM5 product pilot."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    Chipset,
    ChipsetFamily,
    CpuArchitecture,
    CpuFamily,
    CpuModel,
    CpuVendor,
    HardwarePlatform,
    MotherboardFamily,
    MotherboardModel,
    Vendor,
)
from app.domain.errors import SystemContextError
from app.domain.recommendation_policy import USE_CASE_LABELS, USE_CASES

PILOT_PLATFORM_ID = "amd-am5"
BIOS_VERSION_NOT_PROVIDED = "not_provided"
BIOS_VERSION_PROVIDED = "provided_unverified"
USE_CASE_NOT_PROVIDED = "not_provided"
USE_CASE_PROVIDED = "provided"
COVERAGE_IDENTITY_RESOLVED = "identity_resolved"

MATCH_EXACT_MODEL = "exact_model"
MATCH_CHIPSET = "chipset"
MATCH_CHIPSET_FAMILY = "chipset_family"
MATCH_PLATFORM = "platform"
MATCH_UNVERIFIED = "unverified"
MATCH_INCOMPATIBLE = "incompatible"


@dataclass(frozen=True)
class SystemContext:
    platform_id: str
    platform_name: str
    vendor_id: str
    vendor_name: str
    cpu_vendor_id: str
    cpu_vendor_name: str
    motherboard_model_id: str
    motherboard_model_name: str
    motherboard_family_id: str | None
    motherboard_family_name: str | None
    chipset_id: str
    chipset_name: str
    chipset_family_id: str
    chipset_family_name: str
    cpu_model_id: str
    cpu_model_name: str
    cpu_family_id: str
    cpu_family_name: str
    cpu_architecture_id: str
    cpu_architecture_name: str
    bios_version: str | None
    bios_version_status: str
    use_case: str | None
    use_case_label: str | None
    use_case_status: str
    coverage_status: str
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class ContextApplicabilityMatch:
    status: str
    label: str
    reason: str


def resolve(
    session: Session,
    *,
    motherboard_model_id: str,
    cpu_model_id: str,
    bios_version: str = "",
    use_case: str = "",
) -> SystemContext:
    """Resolve reviewed hardware IDs and enforce the current AM5 pilot boundary."""
    motherboard_model_id = motherboard_model_id.strip().casefold()
    cpu_model_id = cpu_model_id.strip().casefold()
    bios_version = " ".join(bios_version.split())
    use_case = use_case.strip().casefold()
    if len(bios_version) > 64:
        raise SystemContextError("BIOS version must be 64 characters or fewer.")
    if use_case and use_case not in USE_CASES:
        raise SystemContextError(
            f"Use case must be one of: {', '.join(USE_CASES)}."
        )
    if not motherboard_model_id or not cpu_model_id:
        raise SystemContextError("Choose both a motherboard and CPU model.")
    motherboard = session.get(MotherboardModel, motherboard_model_id)
    if motherboard is None:
        raise SystemContextError("Choose a known motherboard model.")
    cpu = session.get(CpuModel, cpu_model_id)
    if cpu is None:
        raise SystemContextError("Choose a known CPU model.")
    if motherboard.platform_id != cpu.platform_id:
        raise SystemContextError("The selected motherboard and CPU use different platforms.")
    if motherboard.platform_id != PILOT_PLATFORM_ID:
        raise SystemContextError("The current product pilot supports AMD AM5 only.")

    vendor = session.get(Vendor, motherboard.vendor_id)
    chipset = session.get(Chipset, motherboard.chipset_id)
    platform = session.get(HardwarePlatform, motherboard.platform_id)
    cpu_family = session.get(CpuFamily, cpu.family_id)
    if vendor is None or chipset is None or platform is None or cpu_family is None:
        raise RuntimeError("System context references an incomplete hardware identity graph.")
    chipset_family = session.get(ChipsetFamily, chipset.family_id)
    cpu_architecture = session.get(CpuArchitecture, cpu.architecture_id)
    cpu_vendor = session.get(CpuVendor, platform.cpu_vendor.casefold())
    motherboard_family = (
        session.get(MotherboardFamily, motherboard.family_id) if motherboard.family_id else None
    )
    if chipset_family is None or cpu_architecture is None or cpu_vendor is None:
        raise RuntimeError("System context hardware graph could not be loaded.")

    return SystemContext(
        platform_id=platform.platform_id,
        platform_name=platform.canonical_name,
        vendor_id=vendor.vendor_id,
        vendor_name=vendor.canonical_name,
        cpu_vendor_id=cpu_vendor.vendor_id,
        cpu_vendor_name=cpu_vendor.canonical_name,
        motherboard_model_id=motherboard.model_id,
        motherboard_model_name=motherboard.canonical_name,
        motherboard_family_id=(motherboard_family.family_id if motherboard_family else None),
        motherboard_family_name=(motherboard_family.canonical_name if motherboard_family else None),
        chipset_id=chipset.chipset_id,
        chipset_name=chipset.canonical_name,
        chipset_family_id=chipset_family.family_id,
        chipset_family_name=chipset_family.canonical_name,
        cpu_model_id=cpu.cpu_model_id,
        cpu_model_name=cpu.canonical_name,
        cpu_family_id=cpu_family.family_id,
        cpu_family_name=cpu_family.canonical_name,
        cpu_architecture_id=cpu_architecture.architecture_id,
        cpu_architecture_name=cpu_architecture.canonical_name,
        bios_version=bios_version or None,
        bios_version_status=(
            BIOS_VERSION_PROVIDED if bios_version else BIOS_VERSION_NOT_PROVIDED
        ),
        use_case=use_case or None,
        use_case_label=USE_CASE_LABELS.get(use_case),
        use_case_status=USE_CASE_PROVIDED if use_case else USE_CASE_NOT_PROVIDED,
        coverage_status=COVERAGE_IDENTITY_RESOLVED,
        warnings=(
            (
                "BIOS version is user-provided and is matched only against explicit "
                "reviewed version scopes."
            )
            if bios_version
            else (
                "BIOS version was not provided; TuneForge will not infer it and "
                "version-scoped facts remain unverified."
            ),
            (
                "Use case is request context only. TuneForge does not yet expose "
                "recommendation retrieval."
                if use_case
                else "Use case was not provided; factual BIOS answers remain available."
            ),
        ),
    )


def resolve_optional(
    session: Session,
    *,
    motherboard_model_id: str = "",
    cpu_model_id: str = "",
    bios_version: str = "",
    use_case: str = "",
) -> SystemContext | None:
    if (
        not motherboard_model_id.strip()
        and not cpu_model_id.strip()
        and not bios_version.strip()
        and not use_case.strip()
    ):
        return None
    return resolve(
        session,
        motherboard_model_id=motherboard_model_id,
        cpu_model_id=cpu_model_id,
        bios_version=bios_version,
        use_case=use_case,
    )


def list_pilot_motherboards(session: Session) -> list[MotherboardModel]:
    return list(
        session.scalars(
            select(MotherboardModel)
            .where(MotherboardModel.platform_id == PILOT_PLATFORM_ID)
            .order_by(MotherboardModel.vendor_id, MotherboardModel.canonical_name)
        )
    )


def list_pilot_cpus(session: Session) -> list[CpuModel]:
    return list(
        session.scalars(
            select(CpuModel)
            .where(CpuModel.platform_id == PILOT_PLATFORM_ID)
            .order_by(CpuModel.canonical_name)
        )
    )


def validate_vendor_filter(context: SystemContext, vendor_id: str = "") -> None:
    requested_vendor_id = vendor_id.strip().casefold()
    if requested_vendor_id and requested_vendor_id != context.vendor_id:
        raise SystemContextError("The vendor filter does not match the selected motherboard.")


def classify_manual_targets(
    context: SystemContext,
    *,
    manual_vendor_id: str | None,
    applicability_scope: str | None,
    target_ids: tuple[str, ...],
) -> ContextApplicabilityMatch:
    """Explain how a manual's reviewed scope relates to one resolved system."""
    if manual_vendor_id != context.vendor_id:
        return ContextApplicabilityMatch(
            MATCH_INCOMPATIBLE,
            "Different vendor",
            "The cited manual belongs to a different motherboard vendor.",
        )
    targets = set(target_ids)
    if context.motherboard_model_id in targets:
        return ContextApplicabilityMatch(
            MATCH_EXACT_MODEL,
            "Exact motherboard",
            "The cited manual is normalized to the selected motherboard model.",
        )
    if context.chipset_id in targets:
        return ContextApplicabilityMatch(
            MATCH_CHIPSET,
            "Chipset-level",
            "The cited manual covers the selected motherboard chipset.",
        )
    if context.chipset_family_id in targets:
        return ContextApplicabilityMatch(
            MATCH_CHIPSET_FAMILY,
            "Chipset-family",
            "The cited manual covers the selected motherboard chipset family.",
        )
    if context.platform_id in targets:
        return ContextApplicabilityMatch(
            MATCH_PLATFORM,
            "Platform-level",
            "The cited manual covers AMD AM5 broadly, not this exact motherboard.",
        )
    if not target_ids or applicability_scope in {None, "unknown"}:
        return ContextApplicabilityMatch(
            MATCH_UNVERIFIED,
            "Not verified",
            "The manual has no reviewed applicability target for this system.",
        )
    return ContextApplicabilityMatch(
        MATCH_INCOMPATIBLE,
        "Different hardware",
        "The manual's reviewed applicability targets do not include this system.",
    )
