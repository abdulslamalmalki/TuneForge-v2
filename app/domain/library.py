"""Public, read-only BIOS Settings Library projections.

The Library composes existing approved setting, canonical mapping, hardware,
and citation-first guidance read models. It never exposes review events,
reviewer notes, rejected candidates, drafts, or inferred relationships.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from math import ceil

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    CanonicalSetting,
    CanonicalSettingAlias,
    CanonicalSettingCategory,
    CanonicalSettingClassification,
    Manual,
    Setting,
    SettingCanonicalMapping,
    SettingMenuContext,
)
from app.domain import canonical_settings, guidance_read, hardware
from app.domain.canonical_settings import (
    CanonicalCatalogRow,
    CanonicalClassificationView,
    VendorEquivalentName,
)
from app.domain.guidance_read import GuidanceReadFact
from app.domain.review import effective_description, effective_name
from app.normalization.canonical_settings import normalize_alias
from app.normalization.hardware import CHIPSETS, MOTHERBOARD_MODELS
from app.search import query as approved_search

VIEW_CANONICAL = "canonical"
VIEW_SOURCE = "source"
LIBRARY_VIEWS = (VIEW_CANONICAL, VIEW_SOURCE)
DEFAULT_PAGE_SIZE = 24
MAX_PAGE_SIZE = 100

_CHIPSETS_BY_ID = {item.chipset_id: item for item in CHIPSETS}
_MODELS_BY_ID = {item.model_id: item for item in MOTHERBOARD_MODELS}


@dataclass(frozen=True)
class LibraryAlias:
    display_alias: str
    vendor_id: str | None


@dataclass(frozen=True)
class LibraryApplicabilityTarget:
    target_type: str
    target_id: str
    canonical_name: str


@dataclass(frozen=True)
class LibraryManualCitation:
    manual_id: int
    source_title: str
    manual_label: str
    source_url: str | None
    document_version: str | None
    page_number: int


@dataclass(frozen=True)
class LibrarySourceSetting:
    setting_id: int
    effective_name: str
    effective_description: str
    description_excerpt: str
    vendor_id: str | None
    vendor_name: str
    manual_label: str
    citation: LibraryManualCitation
    menu_path: str | None
    menu_path_page_number: int | None
    eligibility_label: str
    acceptance_label: str
    applicability_scope: str | None
    applicability_targets: tuple[LibraryApplicabilityTarget, ...]
    canonical_id: str | None
    canonical_name: str | None
    category_id: str | None
    category_name: str | None
    risk_level: str | None
    guidance_available: bool


@dataclass(frozen=True)
class LibraryCanonicalSetting:
    canonical_id: str
    canonical_name: str
    description: str | None
    aliases: tuple[LibraryAlias, ...]
    equivalent_vendor_names: tuple[VendorEquivalentName, ...]
    source_setting_count: int
    vendor_count: int
    category_id: str | None
    category_name: str | None
    risk_level: str | None
    guidance_available: bool
    applicability_targets: tuple[LibraryApplicabilityTarget, ...]


@dataclass(frozen=True)
class LibraryCanonicalDetail:
    setting: LibraryCanonicalSetting
    classification: CanonicalClassificationView | None
    source_settings: tuple[LibrarySourceSetting, ...]
    facts: tuple[GuidanceReadFact, ...]
    scope_notice: str
    recommendation_notice: str


@dataclass(frozen=True)
class LibrarySourceDetail:
    setting: LibrarySourceSetting
    canonical_description: str | None
    canonical_aliases: tuple[LibraryAlias, ...]
    equivalent_vendor_names: tuple[VendorEquivalentName, ...]
    classification: CanonicalClassificationView | None
    facts: tuple[GuidanceReadFact, ...]
    scope_notice: str | None
    recommendation_notice: str | None


@dataclass(frozen=True)
class LibraryPage:
    view: str
    items: tuple[LibraryCanonicalSetting | LibrarySourceSetting, ...]
    total: int
    page: int
    page_size: int
    total_pages: int

    @property
    def has_previous(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.total_pages


def browse(
    session: Session,
    *,
    view: str = VIEW_CANONICAL,
    query: str = "",
    vendor_id: str = "",
    platform_id: str = "",
    chipset_id: str = "",
    category_id: str = "",
    risk_level: str = "",
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> LibraryPage:
    """Browse approved public knowledge with stable filtering and pagination."""
    if view not in LIBRARY_VIEWS:
        raise ValueError("Unknown Library view.")
    if page < 1:
        raise ValueError("Library page must be at least one.")
    if not 1 <= page_size <= MAX_PAGE_SIZE:
        raise ValueError(f"Library page size must be between 1 and {MAX_PAGE_SIZE}.")

    filters = {
        "query": " ".join(query.split()),
        "vendor_id": vendor_id.strip().casefold(),
        "platform_id": platform_id.strip().casefold(),
        "chipset_id": chipset_id.strip().casefold(),
        "category_id": category_id.strip().casefold(),
        "risk_level": risk_level.strip().casefold(),
    }
    if not any(filters.values()):
        if view == VIEW_SOURCE:
            return _unfiltered_source_page(session, page=page, page_size=page_size)
        return _unfiltered_canonical_page(session, page=page, page_size=page_size)
    if view == VIEW_SOURCE:
        values: list[LibraryCanonicalSetting | LibrarySourceSetting] = list(
            _filtered_source_settings(session, **filters)
        )
    else:
        values = list(_filtered_canonical_settings(session, **filters))
    total = len(values)
    start = (page - 1) * page_size
    total_pages = ceil(total / page_size) if total else 0
    return LibraryPage(
        view=view,
        items=tuple(values[start : start + page_size]),
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


def _unfiltered_source_page(
    session: Session,
    *,
    page: int,
    page_size: int,
) -> LibraryPage:
    total = approved_source_count(session)
    total_pages = ceil(total / page_size) if total else 0
    effective_name = func.lower(func.coalesce(Setting.edited_name, Setting.extracted_name))
    vendor_name = func.lower(func.coalesce(Manual.vendor, "Unknown vendor"))
    setting_ids = set(
        session.scalars(
            select(Setting.id)
            .join(Manual, Manual.id == Setting.manual_id)
            .where(Setting.status == "approved")
            .order_by(effective_name, vendor_name, Setting.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    values = tuple(
        sorted(
            _load_source_settings(session, setting_ids=setting_ids),
            key=lambda item: (
                item.effective_name.casefold(),
                item.vendor_name.casefold(),
                item.setting_id,
            ),
        )
    )
    return LibraryPage(
        view=VIEW_SOURCE,
        items=values,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


def _unfiltered_canonical_page(
    session: Session,
    *,
    page: int,
    page_size: int,
) -> LibraryPage:
    catalog = canonical_settings.list_catalog(session, include_deprecated=False)
    catalog.sort(key=lambda row: row.canonical_name.casefold())
    total = len(catalog)
    total_pages = ceil(total / page_size) if total else 0
    selected = catalog[(page - 1) * page_size : page * page_size]
    canonical_ids = {row.canonical_id for row in selected}
    source_ids = set(
        session.scalars(
            select(SettingCanonicalMapping.setting_id)
            .join(Setting, Setting.id == SettingCanonicalMapping.setting_id)
            .where(
                SettingCanonicalMapping.canonical_id.in_(canonical_ids),
                Setting.status == "approved",
            )
        )
    )
    sources = _load_source_settings(session, setting_ids=source_ids)
    by_canonical: dict[str, list[LibrarySourceSetting]] = defaultdict(list)
    for source in sources:
        if source.canonical_id:
            by_canonical[source.canonical_id].append(source)
    aliases = _alias_map(session, canonical_ids)
    equivalences = canonical_settings.list_vendor_equivalences(session, canonical_ids)
    covered = _guidance_ids(session)
    values = tuple(
        _canonical_projection(
            row,
            tuple(by_canonical.get(row.canonical_id, ())),
            aliases=aliases.get(row.canonical_id, ()),
            equivalences=equivalences.get(row.canonical_id, ()),
            guidance_available=row.canonical_id in covered,
        )
        for row in selected
    )
    return LibraryPage(
        view=VIEW_CANONICAL,
        items=values,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


def canonical_detail(session: Session, canonical_id: str) -> LibraryCanonicalDetail | None:
    identity = session.get(CanonicalSetting, canonical_id)
    if identity is None or identity.status != canonical_settings.IDENTITY_ACTIVE:
        return None
    setting_ids = set(
        session.scalars(
            select(SettingCanonicalMapping.setting_id).where(
                SettingCanonicalMapping.canonical_id == identity.canonical_id
            )
        )
    )
    sources = _load_source_settings(session, setting_ids=setting_ids)
    catalog = next(
        (
            row
            for row in canonical_settings.list_catalog(session, include_deprecated=False)
            if row.canonical_id == canonical_id
        ),
        None,
    )
    if catalog is None:
        raise RuntimeError("Canonical Library identity disappeared during projection.")
    aliases = _alias_map(session, {canonical_id}).get(canonical_id, ())
    equivalences = canonical_settings.list_vendor_equivalences(session, {canonical_id}).get(
        canonical_id, ()
    )
    covered = _guidance_ids(session)
    setting = _canonical_projection(
        catalog,
        sources,
        aliases=aliases,
        equivalences=equivalences,
        guidance_available=canonical_id in covered,
    )
    explanation = guidance_read.explain_canonical_id(session, canonical_id)
    if explanation is None:  # guarded by the identity lookup above
        raise RuntimeError("Canonical Library identity could not be explained.")
    return LibraryCanonicalDetail(
        setting=setting,
        classification=explanation.classification,
        source_settings=sources,
        facts=explanation.facts,
        scope_notice=explanation.scope_notice,
        recommendation_notice=explanation.recommendation_notice,
    )


def source_detail(session: Session, setting_id: int) -> LibrarySourceDetail | None:
    values = _load_source_settings(session, setting_ids={setting_id})
    if not values:
        return None
    setting = values[0]
    if setting.canonical_id is None:
        return LibrarySourceDetail(
            setting=setting,
            canonical_description=None,
            canonical_aliases=(),
            equivalent_vendor_names=(),
            classification=None,
            facts=(),
            scope_notice=None,
            recommendation_notice=None,
        )
    canonical = canonical_settings.get_detail(session, setting.canonical_id)
    explanation = guidance_read.explain_canonical_id(session, setting.canonical_id)
    if canonical is None or explanation is None:
        raise RuntimeError("Mapped Library identity could not be loaded.")
    return LibrarySourceDetail(
        setting=setting,
        canonical_description=canonical.identity.description,
        canonical_aliases=tuple(
            LibraryAlias(alias.display_alias, alias.vendor_id) for alias in canonical.aliases
        ),
        equivalent_vendor_names=explanation.equivalent_vendor_names,
        classification=explanation.classification,
        facts=explanation.facts,
        scope_notice=explanation.scope_notice,
        recommendation_notice=explanation.recommendation_notice,
    )


def approved_source_count(session: Session) -> int:
    return (
        session.scalar(
            select(func.count()).select_from(Setting).where(Setting.status == "approved")
        )
        or 0
    )


def mapped_source_count(session: Session) -> int:
    """Count approved source settings attached to an active canonical identity."""
    return (
        session.scalar(
            select(func.count())
            .select_from(Setting)
            .join(SettingCanonicalMapping, SettingCanonicalMapping.setting_id == Setting.id)
            .join(
                CanonicalSetting,
                CanonicalSetting.canonical_id == SettingCanonicalMapping.canonical_id,
            )
            .where(
                Setting.status == "approved",
                CanonicalSetting.status == canonical_settings.IDENTITY_ACTIVE,
            )
        )
        or 0
    )


def canonical_count(session: Session) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(CanonicalSetting)
            .where(CanonicalSetting.status == canonical_settings.IDENTITY_ACTIVE)
        )
        or 0
    )


def guidance_count(session: Session) -> int:
    return len(guidance_read.list_coverage(session, approved_only=True))


def _filtered_source_settings(
    session: Session,
    *,
    query: str,
    vendor_id: str,
    platform_id: str,
    chipset_id: str,
    category_id: str,
    risk_level: str,
) -> tuple[LibrarySourceSetting, ...]:
    rank: dict[int, int] | None = None
    if query:
        results = approved_search.search_approved(
            session,
            query,
            vendor_id=vendor_id,
            platform_id=platform_id,
            chipset_id=chipset_id,
            category_id=category_id,
            risk_level=risk_level,
        )
        rank = {result.setting_id: index for index, result in enumerate(results)}
        values = _load_source_settings(session, setting_ids=set(rank))
    else:
        values = _load_source_settings(session)
    filtered = tuple(
        item
        for item in values
        if (rank is None or item.setting_id in rank)
        and (not vendor_id or item.vendor_id == vendor_id)
        and (not category_id or item.category_id == category_id)
        and (not risk_level or item.risk_level == risk_level)
        and _matches_hardware(item, platform_id=platform_id, chipset_id=chipset_id)
    )
    if rank is not None:
        return tuple(sorted(filtered, key=lambda item: rank[item.setting_id]))
    return tuple(
        sorted(
            filtered,
            key=lambda item: (
                item.effective_name.casefold(),
                item.vendor_name.casefold(),
                item.setting_id,
            ),
        )
    )


def _filtered_canonical_settings(
    session: Session,
    *,
    query: str,
    vendor_id: str,
    platform_id: str,
    chipset_id: str,
    category_id: str,
    risk_level: str,
) -> tuple[LibraryCanonicalSetting, ...]:
    all_sources = _load_source_settings(session)
    by_canonical: dict[str, list[LibrarySourceSetting]] = {}
    for source in all_sources:
        if source.canonical_id:
            by_canonical.setdefault(source.canonical_id, []).append(source)

    catalog = canonical_settings.list_catalog(session, include_deprecated=False)
    canonical_ids = {row.canonical_id for row in catalog}
    aliases = _alias_map(session, canonical_ids)
    equivalences = canonical_settings.list_vendor_equivalences(session, canonical_ids)
    covered = _guidance_ids(session)
    values = []
    normalized_query = normalize_alias(query)
    for row in catalog:
        sources = tuple(by_canonical.get(row.canonical_id, ()))
        projection = _canonical_projection(
            row,
            sources,
            aliases=aliases.get(row.canonical_id, ()),
            equivalences=equivalences.get(row.canonical_id, ()),
            guidance_available=row.canonical_id in covered,
        )
        if category_id and projection.category_id != category_id:
            continue
        if risk_level and projection.risk_level != risk_level:
            continue
        if any((vendor_id, platform_id, chipset_id)) and not any(
            (not vendor_id or source.vendor_id == vendor_id)
            and _matches_hardware(source, platform_id=platform_id, chipset_id=chipset_id)
            for source in sources
        ):
            continue
        if normalized_query and not _canonical_matches_query(projection, sources, query):
            continue
        values.append(projection)
    return tuple(
        sorted(
            values,
            key=lambda item: (
                _canonical_query_rank(item, by_canonical.get(item.canonical_id, ()), query),
                item.canonical_name.casefold(),
            ),
        )
    )


def _canonical_projection(
    catalog: CanonicalCatalogRow,
    sources: tuple[LibrarySourceSetting, ...],
    *,
    aliases: tuple[LibraryAlias, ...],
    equivalences: tuple[VendorEquivalentName, ...],
    guidance_available: bool,
) -> LibraryCanonicalSetting:
    target_map = {
        (target.target_type, target.target_id): target
        for source in sources
        for target in source.applicability_targets
    }
    return LibraryCanonicalSetting(
        canonical_id=catalog.canonical_id,
        canonical_name=catalog.canonical_name,
        description=catalog.description,
        aliases=aliases,
        equivalent_vendor_names=equivalences,
        source_setting_count=len(sources),
        vendor_count=len({source.vendor_id for source in sources if source.vendor_id}),
        category_id=catalog.category_id,
        category_name=catalog.category_name,
        risk_level=catalog.risk_level,
        guidance_available=guidance_available,
        applicability_targets=tuple(
            sorted(target_map.values(), key=lambda item: (item.target_type, item.canonical_name))
        ),
    )


def _load_source_settings(
    session: Session,
    *,
    setting_ids: set[int] | None = None,
) -> tuple[LibrarySourceSetting, ...]:
    statement = (
        select(
            Setting,
            Manual,
            CanonicalSetting,
            CanonicalSettingClassification,
            CanonicalSettingCategory,
            SettingMenuContext,
        )
        .join(Manual, Manual.id == Setting.manual_id)
        .outerjoin(SettingCanonicalMapping, SettingCanonicalMapping.setting_id == Setting.id)
        .outerjoin(
            CanonicalSetting,
            CanonicalSetting.canonical_id == SettingCanonicalMapping.canonical_id,
        )
        .outerjoin(
            CanonicalSettingClassification,
            CanonicalSettingClassification.canonical_id == CanonicalSetting.canonical_id,
        )
        .outerjoin(
            CanonicalSettingCategory,
            CanonicalSettingCategory.category_id == CanonicalSettingClassification.category_id,
        )
        .outerjoin(
            SettingMenuContext,
            (SettingMenuContext.setting_id == Setting.id)
            & (SettingMenuContext.status == "active"),
        )
        .where(Setting.status == "approved")
    )
    if setting_ids is not None:
        if not setting_ids:
            return ()
        statement = statement.where(Setting.id.in_(setting_ids))
    rows = session.execute(statement).all()
    manual_ids = {manual.id for _, manual, _, _, _, _ in rows}
    applicability = hardware.get_manual_applicability_many(session, manual_ids)
    covered = _guidance_ids(session)
    values = []
    for setting, manual, canonical, classification, category, menu_context in rows:
        if canonical is not None and canonical.status != canonical_settings.IDENTITY_ACTIVE:
            canonical = None
            classification = None
            category = None
        view = applicability[manual.id]
        targets = tuple(
            LibraryApplicabilityTarget(
                target.target_type,
                target.target_id,
                target.canonical_name,
            )
            for target in (view.targets if view else ())
        )
        name = effective_name(setting)
        description = effective_description(setting)
        manual_label = (f"{manual.vendor or ''} {manual.model_name or ''}").strip() or (
            manual.document_title or manual.original_filename
        )
        page_number = setting.edited_page_number or setting.page_number
        values.append(
            LibrarySourceSetting(
                setting_id=setting.id,
                effective_name=name,
                effective_description=description,
                description_excerpt=_excerpt(description),
                vendor_id=manual.vendor_id,
                vendor_name=manual.vendor or "Unknown vendor",
                manual_label=manual_label,
                citation=LibraryManualCitation(
                    manual_id=manual.id,
                    source_title=manual.document_title or manual.original_filename,
                    manual_label=manual_label,
                    source_url=manual.source_url,
                    document_version=manual.document_version,
                    page_number=page_number,
                ),
                menu_path=menu_context.menu_path if menu_context is not None else None,
                menu_path_page_number=(
                    menu_context.source_page_number if menu_context is not None else None
                ),
                eligibility_label=_eligibility_label(manual.eligibility_status),
                acceptance_label=(
                    "Acceptance certified"
                    if manual.acceptance_reviewed_at
                    else "Reviewer-approved setting"
                ),
                applicability_scope=(view.scope if view else None),
                applicability_targets=targets,
                canonical_id=(canonical.canonical_id if canonical else None),
                canonical_name=(canonical.canonical_name if canonical else None),
                category_id=(classification.category_id if classification else None),
                category_name=(category.canonical_name if category else None),
                risk_level=(classification.risk_level if classification else None),
                guidance_available=(canonical is not None and canonical.canonical_id in covered),
            )
        )
    return tuple(values)


def _canonical_matches_query(
    setting: LibraryCanonicalSetting,
    sources: tuple[LibrarySourceSetting, ...],
    query: str,
) -> bool:
    if normalize_alias(query).replace(" ", "") == _initialism(setting.canonical_name):
        return True
    terms = tuple(term.casefold() for term in query.split() if term)
    haystack = " ".join(
        value
        for value in (
            setting.canonical_name,
            setting.description or "",
            *(alias.display_alias for alias in setting.aliases),
            *(source.effective_name for source in sources),
            *(source.effective_description for source in sources),
        )
        if value
    ).casefold()
    return all(term in haystack for term in terms)


def _canonical_query_rank(
    setting: LibraryCanonicalSetting,
    sources: tuple[LibrarySourceSetting, ...] | list[LibrarySourceSetting],
    query: str,
) -> int:
    if not query:
        return 0
    normalized = normalize_alias(query)
    if normalized == normalize_alias(setting.canonical_name):
        return 0
    if normalized.replace(" ", "") == _initialism(setting.canonical_name):
        return 0
    if any(normalized == normalize_alias(alias.display_alias) for alias in setting.aliases):
        return 0
    if any(normalized == normalize_alias(source.effective_name) for source in sources):
        return 1
    if normalized and normalized in normalize_alias(setting.canonical_name):
        return 2
    return 3


def _initialism(value: str) -> str:
    return "".join(word[0] for word in value.casefold().split() if word and word[0].isalnum())


def _matches_hardware(
    setting: LibrarySourceSetting,
    *,
    platform_id: str,
    chipset_id: str,
) -> bool:
    if not platform_id and not chipset_id:
        return True
    if not setting.applicability_targets:
        return False
    if platform_id and not any(
        _target_platform_id(target) == platform_id for target in setting.applicability_targets
    ):
        return False
    if chipset_id and not any(
        _target_matches_chipset(target, chipset_id) for target in setting.applicability_targets
    ):
        return False
    return True


def _target_platform_id(target: LibraryApplicabilityTarget) -> str | None:
    if target.target_type == hardware.TARGET_PLATFORM:
        return target.target_id
    if target.target_type == hardware.TARGET_CHIPSET:
        definition = _CHIPSETS_BY_ID.get(target.target_id)
        return definition.platform_id if definition else None
    if target.target_type == hardware.TARGET_MOTHERBOARD_MODEL:
        definition = _MODELS_BY_ID.get(target.target_id)
        return definition.platform_id if definition else None
    if target.target_type == hardware.TARGET_CHIPSET_FAMILY:
        definition = next(
            (item for item in CHIPSETS if item.family_id == target.target_id),
            None,
        )
        return definition.platform_id if definition else None
    return None


def _target_matches_chipset(target: LibraryApplicabilityTarget, chipset_id: str) -> bool:
    selected = _CHIPSETS_BY_ID.get(chipset_id)
    if selected is None:
        return False
    if target.target_type == hardware.TARGET_PLATFORM:
        return target.target_id == selected.platform_id
    if target.target_type == hardware.TARGET_CHIPSET_FAMILY:
        return target.target_id == selected.family_id
    if target.target_type == hardware.TARGET_CHIPSET:
        return target.target_id == chipset_id
    if target.target_type == hardware.TARGET_MOTHERBOARD_MODEL:
        model = _MODELS_BY_ID.get(target.target_id)
        return model is not None and model.chipset_id == chipset_id
    return False


def _guidance_ids(session: Session) -> set[str]:
    return {
        row.canonical_id for row in guidance_read.list_coverage(session, approved_only=True)
    }


def _alias_map(
    session: Session,
    canonical_ids: set[str],
) -> dict[str, tuple[LibraryAlias, ...]]:
    if not canonical_ids:
        return {}
    grouped: dict[str, list[LibraryAlias]] = {canonical_id: [] for canonical_id in canonical_ids}
    rows = session.scalars(
        select(CanonicalSettingAlias)
        .where(CanonicalSettingAlias.canonical_id.in_(canonical_ids))
        .order_by(CanonicalSettingAlias.canonical_id, CanonicalSettingAlias.display_alias)
    )
    for row in rows:
        grouped[row.canonical_id].append(LibraryAlias(row.display_alias, row.vendor_id))
    return {canonical_id: tuple(values) for canonical_id, values in grouped.items()}


def _eligibility_label(status: str) -> str:
    if status == "eligible":
        return "Eligible BIOS source"
    if status == "legacy":
        return "Legacy reviewed BIOS source"
    return "Reviewed source"


def _excerpt(value: str, limit: int = 240) -> str:
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"
