"""Explicit public-safe schemas for the BIOS Settings Library."""

from typing import Literal

from pydantic import BaseModel

from app.domain import library
from app.domain.canonical_settings import VendorEquivalentName
from app.domain.guidance_read import GuidanceReadFact


class LibraryAliasOut(BaseModel):
    display_alias: str
    vendor_id: str | None


class LibraryApplicabilityTargetOut(BaseModel):
    target_type: str
    target_id: str
    canonical_name: str


class LibraryManualCitationOut(BaseModel):
    manual_id: int
    source_title: str
    manual_label: str
    source_url: str | None
    document_version: str | None
    page_number: int


class LibraryEquivalentCitationOut(BaseModel):
    setting_id: int
    manual_id: int
    manual_title: str
    manual_label: str
    source_url: str | None
    page_number: int
    menu_path: str | None
    menu_path_page_number: int | None


class LibraryVendorEquivalentOut(BaseModel):
    vendor_id: str | None
    vendor_name: str
    setting_name: str
    citations: list[LibraryEquivalentCitationOut]

    @classmethod
    def from_domain(cls, value: VendorEquivalentName) -> "LibraryVendorEquivalentOut":
        return cls(
            vendor_id=value.vendor_id,
            vendor_name=value.vendor_name,
            setting_name=value.setting_name,
            citations=[
                LibraryEquivalentCitationOut.model_validate(citation, from_attributes=True)
                for citation in value.citations
            ],
        )


class LibraryEvidenceCitationOut(BaseModel):
    passage_id: int
    source_id: int
    publisher_name: str
    source_title: str
    source_url: str
    document_id: str | None
    document_version: str
    page_number: int
    section_heading: str | None


class LibraryFactOut(BaseModel):
    claim_id: int
    claim_text: str
    claim_type: str
    applicability_scope: str
    bios_match_status: str
    citations: list[LibraryEvidenceCitationOut]

    @classmethod
    def from_domain(cls, value: GuidanceReadFact) -> "LibraryFactOut":
        return cls(
            claim_id=value.claim_id,
            claim_text=value.claim_text,
            claim_type=value.claim_type,
            applicability_scope=value.applicability_scope,
            bios_match_status=value.bios_match_status,
            citations=[
                LibraryEvidenceCitationOut.model_validate(citation, from_attributes=True)
                for citation in value.citations
            ],
        )


class LibraryClassificationOut(BaseModel):
    category_id: str
    category_name: str
    risk_level: str
    risk_rationale: str


class LibrarySourceSummaryOut(BaseModel):
    kind: Literal["source"] = "source"
    setting_id: int
    effective_name: str
    description_excerpt: str
    vendor_id: str | None
    vendor_name: str
    manual_label: str
    citation: LibraryManualCitationOut
    menu_path: str | None
    menu_path_page_number: int | None
    eligibility_label: str
    acceptance_label: str
    applicability_scope: str | None
    applicability_targets: list[LibraryApplicabilityTargetOut]
    canonical_id: str | None
    canonical_name: str | None
    category_id: str | None
    category_name: str | None
    risk_level: str | None
    guidance_available: bool

    @classmethod
    def from_domain(cls, value: library.LibrarySourceSetting) -> "LibrarySourceSummaryOut":
        return cls(
            setting_id=value.setting_id,
            effective_name=value.effective_name,
            description_excerpt=value.description_excerpt,
            vendor_id=value.vendor_id,
            vendor_name=value.vendor_name,
            manual_label=value.manual_label,
            citation=LibraryManualCitationOut.model_validate(value.citation, from_attributes=True),
            menu_path=value.menu_path,
            menu_path_page_number=value.menu_path_page_number,
            eligibility_label=value.eligibility_label,
            acceptance_label=value.acceptance_label,
            applicability_scope=value.applicability_scope,
            applicability_targets=[
                LibraryApplicabilityTargetOut.model_validate(target, from_attributes=True)
                for target in value.applicability_targets
            ],
            canonical_id=value.canonical_id,
            canonical_name=value.canonical_name,
            category_id=value.category_id,
            category_name=value.category_name,
            risk_level=value.risk_level,
            guidance_available=value.guidance_available,
        )


class LibraryCanonicalSummaryOut(BaseModel):
    kind: Literal["canonical"] = "canonical"
    canonical_id: str
    canonical_name: str
    description: str | None
    aliases: list[LibraryAliasOut]
    equivalent_vendor_names: list[LibraryVendorEquivalentOut]
    source_setting_count: int
    vendor_count: int
    category_id: str | None
    category_name: str | None
    risk_level: str | None
    guidance_available: bool
    applicability_targets: list[LibraryApplicabilityTargetOut]

    @classmethod
    def from_domain(cls, value: library.LibraryCanonicalSetting) -> "LibraryCanonicalSummaryOut":
        return cls(
            canonical_id=value.canonical_id,
            canonical_name=value.canonical_name,
            description=value.description,
            aliases=[
                LibraryAliasOut.model_validate(alias, from_attributes=True)
                for alias in value.aliases
            ],
            equivalent_vendor_names=[
                LibraryVendorEquivalentOut.from_domain(item)
                for item in value.equivalent_vendor_names
            ],
            source_setting_count=value.source_setting_count,
            vendor_count=value.vendor_count,
            category_id=value.category_id,
            category_name=value.category_name,
            risk_level=value.risk_level,
            guidance_available=value.guidance_available,
            applicability_targets=[
                LibraryApplicabilityTargetOut.model_validate(target, from_attributes=True)
                for target in value.applicability_targets
            ],
        )


class LibraryPageOut(BaseModel):
    view: Literal["canonical", "source"]
    total: int
    page: int
    page_size: int
    total_pages: int
    items: list[LibraryCanonicalSummaryOut | LibrarySourceSummaryOut]

    @classmethod
    def from_domain(cls, value: library.LibraryPage) -> "LibraryPageOut":
        items = []
        for item in value.items:
            if isinstance(item, library.LibraryCanonicalSetting):
                items.append(LibraryCanonicalSummaryOut.from_domain(item))
            else:
                items.append(LibrarySourceSummaryOut.from_domain(item))
        return cls(
            view=value.view,
            total=value.total,
            page=value.page,
            page_size=value.page_size,
            total_pages=value.total_pages,
            items=items,
        )


class LibraryCanonicalDetailOut(BaseModel):
    setting: LibraryCanonicalSummaryOut
    classification: LibraryClassificationOut | None
    source_settings: list[LibrarySourceSummaryOut]
    facts: list[LibraryFactOut]
    scope_notice: str
    recommendation_notice: str

    @classmethod
    def from_domain(cls, value: library.LibraryCanonicalDetail) -> "LibraryCanonicalDetailOut":
        return cls(
            setting=LibraryCanonicalSummaryOut.from_domain(value.setting),
            classification=(
                LibraryClassificationOut.model_validate(value.classification, from_attributes=True)
                if value.classification
                else None
            ),
            source_settings=[
                LibrarySourceSummaryOut.from_domain(item) for item in value.source_settings
            ],
            facts=[LibraryFactOut.from_domain(item) for item in value.facts],
            scope_notice=value.scope_notice,
            recommendation_notice=value.recommendation_notice,
        )


class LibrarySourceDetailOut(BaseModel):
    setting: LibrarySourceSummaryOut
    effective_description: str
    canonical_description: str | None
    canonical_aliases: list[LibraryAliasOut]
    equivalent_vendor_names: list[LibraryVendorEquivalentOut]
    classification: LibraryClassificationOut | None
    facts: list[LibraryFactOut]
    scope_notice: str | None
    recommendation_notice: str | None

    @classmethod
    def from_domain(cls, value: library.LibrarySourceDetail) -> "LibrarySourceDetailOut":
        return cls(
            setting=LibrarySourceSummaryOut.from_domain(value.setting),
            effective_description=value.setting.effective_description,
            canonical_description=value.canonical_description,
            canonical_aliases=[
                LibraryAliasOut.model_validate(alias, from_attributes=True)
                for alias in value.canonical_aliases
            ],
            equivalent_vendor_names=[
                LibraryVendorEquivalentOut.from_domain(item)
                for item in value.equivalent_vendor_names
            ],
            classification=(
                LibraryClassificationOut.model_validate(value.classification, from_attributes=True)
                if value.classification
                else None
            ),
            facts=[LibraryFactOut.from_domain(item) for item in value.facts],
            scope_notice=value.scope_notice,
            recommendation_notice=value.recommendation_notice,
        )
