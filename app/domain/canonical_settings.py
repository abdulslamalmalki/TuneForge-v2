"""Reviewer-controlled canonical BIOS-setting identities and mappings."""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app import config
from app.auth.context import current_actor
from app.db.models import (
    CanonicalClassificationEvent,
    CanonicalMappingEvent,
    CanonicalSetting,
    CanonicalSettingAlias,
    CanonicalSettingCategory,
    CanonicalSettingClassification,
    Manual,
    Setting,
    SettingCanonicalMapping,
    Vendor,
)
from app.domain.errors import CanonicalSettingError
from app.domain.extraction_runs import utc_now_iso
from app.domain.review import effective_name
from app.normalization.canonical_settings import (
    RISK_LEVELS,
    RISK_UNCLASSIFIED,
    canonical_slug,
    normalize_alias,
)

MAPPING_REVIEWER = "reviewer"
MAPPING_SEED = "seed"
MAPPING_AUTOMATIC = "automatic"
ACTION_MAPPED = "mapped"
ACTION_REMAPPED = "remapped"
ACTION_UNMAPPED = "unmapped"
ACTION_CLASSIFIED = "classified"
ACTION_RECLASSIFIED = "reclassified"


@dataclass(frozen=True)
class CanonicalMappingView:
    canonical_id: str
    canonical_name: str
    description: str | None
    mapping_method: str
    note: str | None
    actor: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class CanonicalSuggestion:
    canonical_id: str
    canonical_name: str
    matched_alias: str


@dataclass(frozen=True)
class CanonicalClassificationView:
    category_id: str
    category_name: str
    risk_level: str
    risk_rationale: str
    actor: str
    updated_at: str


@dataclass(frozen=True)
class CanonicalCatalogRow:
    canonical_id: str
    canonical_name: str
    description: str | None
    mapping_count: int
    vendor_count: int
    category_id: str | None
    category_name: str | None
    risk_level: str | None


@dataclass(frozen=True)
class CanonicalMappedSetting:
    setting_id: int
    effective_name: str
    manual_id: int
    manual_label: str
    vendor_id: str | None
    page_number: int


@dataclass(frozen=True)
class EquivalentNameCitation:
    """One source-setting citation supporting a vendor-equivalent name."""

    setting_id: int
    manual_id: int
    manual_title: str
    manual_label: str
    source_url: str | None
    page_number: int


@dataclass(frozen=True)
class VendorEquivalentName:
    """A reviewed vendor label, grouped without losing source scope."""

    vendor_id: str | None
    vendor_name: str
    setting_name: str
    citations: tuple[EquivalentNameCitation, ...]


@dataclass(frozen=True)
class CanonicalMappedNameMatch:
    """A unique canonical candidate found from an approved mapped source name."""

    canonical_id: str
    canonical_name: str
    matched_name: str


@dataclass(frozen=True)
class CanonicalDetail:
    identity: CanonicalSetting
    aliases: tuple[CanonicalSettingAlias, ...]
    mappings: tuple[CanonicalMappedSetting, ...]
    classification: CanonicalClassificationView | None
    classification_events: tuple[CanonicalClassificationEvent, ...]


def list_catalog(session: Session) -> list[CanonicalCatalogRow]:
    rows = session.execute(
        select(
            CanonicalSetting,
            func.count(SettingCanonicalMapping.setting_id),
            func.count(distinct(Manual.vendor_id)),
            CanonicalSettingClassification,
            CanonicalSettingCategory,
        )
        .outerjoin(
            SettingCanonicalMapping,
            SettingCanonicalMapping.canonical_id == CanonicalSetting.canonical_id,
        )
        .outerjoin(Setting, Setting.id == SettingCanonicalMapping.setting_id)
        .outerjoin(Manual, Manual.id == Setting.manual_id)
        .outerjoin(
            CanonicalSettingClassification,
            CanonicalSettingClassification.canonical_id == CanonicalSetting.canonical_id,
        )
        .outerjoin(
            CanonicalSettingCategory,
            CanonicalSettingCategory.category_id == CanonicalSettingClassification.category_id,
        )
        .group_by(
            CanonicalSetting.canonical_id,
            CanonicalSettingClassification.canonical_id,
            CanonicalSettingCategory.category_id,
        )
        .order_by(CanonicalSetting.canonical_name)
    )
    return [
        CanonicalCatalogRow(
            canonical_id=identity.canonical_id,
            canonical_name=identity.canonical_name,
            description=identity.description,
            mapping_count=mapping_count,
            vendor_count=vendor_count,
            category_id=(classification_view.category_id if classification_view else None),
            category_name=(classification_view.category_name if classification_view else None),
            risk_level=(classification_view.risk_level if classification_view else None),
        )
        for identity, mapping_count, vendor_count, classification, category in rows
        for classification_view in [_classification_view(classification, category)]
    ]


def get_detail(session: Session, canonical_id: str) -> CanonicalDetail | None:
    identity = session.get(CanonicalSetting, canonical_id)
    if identity is None:
        return None
    aliases = tuple(
        session.scalars(
            select(CanonicalSettingAlias)
            .where(CanonicalSettingAlias.canonical_id == canonical_id)
            .order_by(CanonicalSettingAlias.display_alias)
        )
    )
    rows = session.execute(
        select(Setting, Manual)
        .join(SettingCanonicalMapping, SettingCanonicalMapping.setting_id == Setting.id)
        .join(Manual, Manual.id == Setting.manual_id)
        .where(SettingCanonicalMapping.canonical_id == canonical_id)
        .order_by(Manual.vendor, Manual.id, Setting.page_number, Setting.id)
    )
    mappings = tuple(
        CanonicalMappedSetting(
            setting_id=setting.id,
            effective_name=effective_name(setting),
            manual_id=manual.id,
            manual_label=(f"{manual.vendor or ''} {manual.model_name or ''}").strip()
            or manual.original_filename,
            vendor_id=manual.vendor_id,
            page_number=setting.edited_page_number or setting.page_number,
        )
        for setting, manual in rows
    )
    return CanonicalDetail(
        identity=identity,
        aliases=aliases,
        mappings=mappings,
        classification=get_classification(session, canonical_id),
        classification_events=tuple(list_classification_events(session, canonical_id)),
    )


def list_vendor_equivalences(
    session: Session,
    canonical_ids: tuple[str, ...] | list[str] | set[str],
) -> dict[str, tuple[VendorEquivalentName, ...]]:
    """Project approved mappings as cited, vendor-scoped equivalent names.

    This is a read-only view over the existing canonical mappings. Equivalent
    names are never inferred from canonical aliases or model knowledge.
    """
    requested = tuple(sorted({canonical_id for canonical_id in canonical_ids if canonical_id}))
    if not requested:
        return {}
    rows = session.execute(
        select(SettingCanonicalMapping.canonical_id, Setting, Manual, Vendor)
        .join(Setting, Setting.id == SettingCanonicalMapping.setting_id)
        .join(Manual, Manual.id == Setting.manual_id)
        .outerjoin(Vendor, Vendor.vendor_id == Manual.vendor_id)
        .where(
            SettingCanonicalMapping.canonical_id.in_(requested),
            Setting.status == "approved",
        )
        .order_by(
            SettingCanonicalMapping.canonical_id,
            Vendor.canonical_name,
            Manual.vendor,
            Setting.id,
        )
    )
    grouped: dict[
        str,
        dict[tuple[str | None, str, str], tuple[str, str, list[EquivalentNameCitation]]],
    ] = {canonical_id: {} for canonical_id in requested}
    for canonical_id, setting, manual, vendor in rows:
        setting_name = effective_name(setting)
        vendor_name = (
            vendor.canonical_name if vendor is not None else (manual.vendor or "Unknown vendor")
        )
        key = (manual.vendor_id, vendor_name.casefold(), normalize_alias(setting_name))
        by_name = grouped[canonical_id]
        if key not in by_name:
            by_name[key] = (vendor_name, setting_name, [])
        by_name[key][2].append(
            EquivalentNameCitation(
                setting_id=setting.id,
                manual_id=manual.id,
                manual_title=manual.document_title,
                manual_label=(f"{manual.vendor or ''} {manual.model_name or ''}").strip()
                or manual.original_filename,
                source_url=manual.source_url,
                page_number=setting.edited_page_number or setting.page_number,
            )
        )
    return {
        canonical_id: tuple(
            VendorEquivalentName(
                vendor_id=vendor_id,
                vendor_name=vendor_name,
                setting_name=setting_name,
                citations=tuple(citations),
            )
            for (vendor_id, _, _), (vendor_name, setting_name, citations) in sorted(
                by_name.items(),
                key=lambda item: (
                    item[1][0].casefold(),
                    item[1][1].casefold(),
                    item[0][0] or "",
                ),
            )
        )
        for canonical_id, by_name in grouped.items()
    }


def find_mapped_name_matches(session: Session, value: str) -> list[CanonicalMappedNameMatch]:
    """Resolve an exact normalized source name without guessing through ambiguity."""
    normalized = normalize_alias(value)
    if not normalized:
        return []
    rows = session.execute(
        select(CanonicalSetting, Setting)
        .join(
            SettingCanonicalMapping,
            SettingCanonicalMapping.canonical_id == CanonicalSetting.canonical_id,
        )
        .join(Setting, Setting.id == SettingCanonicalMapping.setting_id)
        .where(Setting.status == "approved")
        .order_by(CanonicalSetting.canonical_id, Setting.id)
    )
    matches: dict[str, CanonicalMappedNameMatch] = {}
    for identity, setting in rows:
        setting_name = effective_name(setting)
        if normalize_alias(setting_name) != normalized:
            continue
        matches.setdefault(
            identity.canonical_id,
            CanonicalMappedNameMatch(
                canonical_id=identity.canonical_id,
                canonical_name=identity.canonical_name,
                matched_name=setting_name,
            ),
        )
    return list(matches.values())


def create_identity(
    session: Session,
    *,
    name: str,
    description: str = "",
    aliases: tuple[str, ...] = (),
    canonical_id: str = "",
    actor: str | None = None,
    commit: bool = True,
) -> CanonicalSetting:
    actor = actor or current_actor()
    name = " ".join(name.split())
    description = description.strip()
    canonical_id = canonical_slug(canonical_id or name)
    _check_identity_values(name, description, canonical_id)
    if session.get(CanonicalSetting, canonical_id) is not None:
        raise CanonicalSettingError("That canonical setting ID already exists.")
    if session.scalar(
        select(CanonicalSetting.canonical_id).where(
            func.lower(CanonicalSetting.canonical_name) == name.casefold()
        )
    ):
        raise CanonicalSettingError("That canonical setting name already exists.")

    now = utc_now_iso()
    identity = CanonicalSetting(
        canonical_id=canonical_id,
        canonical_name=name,
        description=description or None,
        created_at=now,
        updated_at=now,
    )
    session.add(identity)
    session.flush()
    seen_alias_keys: set[str] = set()
    for alias in (name, *aliases):
        key = normalize_alias(alias)
        if key in seen_alias_keys:
            continue
        seen_alias_keys.add(key)
        add_alias(session, identity, alias=alias, actor=actor, commit=False)
    classify_identity(
        session,
        identity,
        category_id="uncategorized",
        risk_level=RISK_UNCLASSIFIED,
        risk_rationale="Awaiting reviewer classification.",
        actor=actor,
        commit=False,
    )
    if commit:
        session.commit()
    return identity


def list_categories(session: Session) -> list[CanonicalSettingCategory]:
    return list(
        session.scalars(select(CanonicalSettingCategory).order_by(CanonicalSettingCategory.ordinal))
    )


def get_classification(
    session: Session,
    canonical_id: str,
) -> CanonicalClassificationView | None:
    row = session.execute(
        select(CanonicalSettingClassification, CanonicalSettingCategory)
        .join(
            CanonicalSettingCategory,
            CanonicalSettingCategory.category_id == CanonicalSettingClassification.category_id,
        )
        .where(CanonicalSettingClassification.canonical_id == canonical_id)
    ).first()
    if row is None:
        return None
    return _classification_view(*row)


def classify_identity(
    session: Session,
    identity: CanonicalSetting,
    *,
    category_id: str,
    risk_level: str,
    risk_rationale: str,
    actor: str | None = None,
    commit: bool = True,
) -> bool:
    actor = actor or current_actor()
    category_id = category_id.strip().casefold()
    risk_level = risk_level.strip().casefold()
    risk_rationale = risk_rationale.strip()
    if session.get(CanonicalSettingCategory, category_id) is None:
        raise CanonicalSettingError("Canonical setting category is not recognized.")
    if risk_level not in RISK_LEVELS:
        raise CanonicalSettingError("Canonical risk level is not recognized.")
    if not risk_rationale:
        raise CanonicalSettingError("A risk rationale is required.")
    if len(risk_rationale) > config.MAX_CANONICAL_RISK_RATIONALE_LENGTH:
        raise CanonicalSettingError("Risk rationale is too long.")

    now = utc_now_iso()
    row = session.get(CanonicalSettingClassification, identity.canonical_id)
    before = _classification_snapshot(row)
    after = {
        "category_id": category_id,
        "risk_level": risk_level,
        "risk_rationale": risk_rationale,
    }
    if before == after:
        return False
    action = ACTION_CLASSIFIED if row is None else ACTION_RECLASSIFIED
    if row is None:
        row = CanonicalSettingClassification(
            canonical_id=identity.canonical_id,
            category_id=category_id,
            risk_level=risk_level,
            risk_rationale=risk_rationale,
            actor=actor,
            updated_at=now,
        )
        session.add(row)
    else:
        row.category_id = category_id
        row.risk_level = risk_level
        row.risk_rationale = risk_rationale
        row.actor = actor
        row.updated_at = now
    session.add(
        CanonicalClassificationEvent(
            canonical_id=identity.canonical_id,
            action=action,
            actor=actor,
            detail=json.dumps(
                {"before": before, "after": after},
                sort_keys=True,
            ),
            created_at=now,
        )
    )
    if commit:
        session.commit()
    return True


def list_classification_events(
    session: Session,
    canonical_id: str,
) -> list[CanonicalClassificationEvent]:
    return list(
        session.scalars(
            select(CanonicalClassificationEvent)
            .where(CanonicalClassificationEvent.canonical_id == canonical_id)
            .order_by(CanonicalClassificationEvent.id)
        )
    )


def add_alias(
    session: Session,
    identity: CanonicalSetting,
    *,
    alias: str,
    vendor_id: str | None = None,
    actor: str | None = None,
    commit: bool = True,
) -> CanonicalSettingAlias:
    del actor  # reserved for a future identity-level audit stream
    alias = " ".join(alias.split())
    key = normalize_alias(alias)
    if not key:
        raise CanonicalSettingError("Alias is required.")
    if len(alias) > config.MAX_CANONICAL_SETTING_NAME_LENGTH:
        raise CanonicalSettingError("Alias is too long.")
    if vendor_id and session.get(Vendor, vendor_id) is None:
        raise CanonicalSettingError("Vendor is not recognized.")
    existing = session.get(CanonicalSettingAlias, key)
    if existing is not None:
        if existing.canonical_id != identity.canonical_id:
            raise CanonicalSettingError("That alias belongs to another canonical setting.")
        return existing
    row = CanonicalSettingAlias(
        normalized_alias=key,
        canonical_id=identity.canonical_id,
        display_alias=alias,
        vendor_id=vendor_id,
    )
    session.add(row)
    if commit:
        session.commit()
    return row


def get_mapping(session: Session, setting_id: int) -> CanonicalMappingView | None:
    row = session.execute(
        select(SettingCanonicalMapping, CanonicalSetting)
        .join(
            CanonicalSetting,
            CanonicalSetting.canonical_id == SettingCanonicalMapping.canonical_id,
        )
        .where(SettingCanonicalMapping.setting_id == setting_id)
    ).first()
    if row is None:
        return None
    mapping, identity = row
    return CanonicalMappingView(
        canonical_id=identity.canonical_id,
        canonical_name=identity.canonical_name,
        description=identity.description,
        mapping_method=mapping.mapping_method,
        note=mapping.note,
        actor=mapping.actor,
        created_at=mapping.created_at,
        updated_at=mapping.updated_at,
    )


def suggest_for_setting(session: Session, setting: Setting) -> list[CanonicalSuggestion]:
    if setting.status != "approved" or get_mapping(session, setting.id) is not None:
        return []
    key = normalize_alias(effective_name(setting))
    rows = session.execute(
        select(CanonicalSettingAlias, CanonicalSetting)
        .join(
            CanonicalSetting,
            CanonicalSetting.canonical_id == CanonicalSettingAlias.canonical_id,
        )
        .where(CanonicalSettingAlias.normalized_alias == key)
        .order_by(CanonicalSetting.canonical_name)
    )
    return [
        CanonicalSuggestion(
            canonical_id=identity.canonical_id,
            canonical_name=identity.canonical_name,
            matched_alias=alias.display_alias,
        )
        for alias, identity in rows
    ]


def map_setting(
    session: Session,
    setting: Setting,
    *,
    canonical_id: str,
    note: str = "",
    actor: str | None = None,
    mapping_method: str = MAPPING_REVIEWER,
    commit: bool = True,
) -> bool:
    actor = actor or current_actor()
    if setting.status != "approved":
        raise CanonicalSettingError("Only approved settings can be canonically mapped.")
    identity = session.get(CanonicalSetting, canonical_id)
    if identity is None:
        raise CanonicalSettingError("Canonical setting not found.")
    if mapping_method not in {MAPPING_REVIEWER, MAPPING_SEED, MAPPING_AUTOMATIC}:
        raise CanonicalSettingError("Mapping method is invalid.")
    note = note.strip()
    if len(note) > config.MAX_CANONICAL_MAPPING_NOTE_LENGTH:
        raise CanonicalSettingError("Mapping note is too long.")

    now = utc_now_iso()
    row = session.get(SettingCanonicalMapping, setting.id)
    before = _mapping_snapshot(row)
    if row is not None and row.canonical_id == canonical_id and row.note == (note or None):
        return False
    action = ACTION_MAPPED if row is None else ACTION_REMAPPED
    if row is None:
        row = SettingCanonicalMapping(
            setting_id=setting.id,
            canonical_id=canonical_id,
            mapping_method=mapping_method,
            note=note or None,
            actor=actor,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
    else:
        row.canonical_id = canonical_id
        row.mapping_method = mapping_method
        row.note = note or None
        row.actor = actor
        row.updated_at = now
    session.add(
        CanonicalMappingEvent(
            setting_id=setting.id,
            action=action,
            actor=actor,
            detail=json.dumps(
                {
                    "before": before,
                    "after": {
                        "canonical_id": canonical_id,
                        "mapping_method": mapping_method,
                        "note": note or None,
                    },
                },
                sort_keys=True,
            ),
            created_at=now,
        )
    )
    if commit:
        session.commit()
    return True


def unmap_setting(
    session: Session,
    setting: Setting,
    *,
    reason: str,
    actor: str | None = None,
) -> None:
    actor = actor or current_actor()
    row = session.get(SettingCanonicalMapping, setting.id)
    if row is None:
        raise CanonicalSettingError("This setting is not canonically mapped.")
    reason = reason.strip()
    if not reason:
        raise CanonicalSettingError("An unmapping reason is required.")
    if len(reason) > config.MAX_CANONICAL_MAPPING_NOTE_LENGTH:
        raise CanonicalSettingError("Unmapping reason is too long.")
    now = utc_now_iso()
    session.add(
        CanonicalMappingEvent(
            setting_id=setting.id,
            action=ACTION_UNMAPPED,
            actor=actor,
            detail=json.dumps(
                {
                    "before": _mapping_snapshot(row),
                    "after": None,
                    "reason": reason,
                },
                sort_keys=True,
            ),
            created_at=now,
        )
    )
    session.delete(row)
    session.commit()


def list_mapping_events(session: Session, setting_id: int) -> list[CanonicalMappingEvent]:
    return list(
        session.scalars(
            select(CanonicalMappingEvent)
            .where(CanonicalMappingEvent.setting_id == setting_id)
            .order_by(CanonicalMappingEvent.id)
        )
    )


def _check_identity_values(name: str, description: str, canonical_id: str) -> None:
    if not name:
        raise CanonicalSettingError("Canonical setting name is required.")
    if len(name) > config.MAX_CANONICAL_SETTING_NAME_LENGTH:
        raise CanonicalSettingError("Canonical setting name is too long.")
    if len(description) > config.MAX_CANONICAL_SETTING_DESCRIPTION_LENGTH:
        raise CanonicalSettingError("Canonical setting description is too long.")
    if not canonical_id or len(canonical_id) > 100:
        raise CanonicalSettingError("Canonical setting ID must be 1 to 100 characters.")


def _mapping_snapshot(row: SettingCanonicalMapping | None) -> dict[str, object] | None:
    if row is None:
        return None
    return {
        "canonical_id": row.canonical_id,
        "mapping_method": row.mapping_method,
        "note": row.note,
    }


def _classification_view(
    row: CanonicalSettingClassification | None,
    category: CanonicalSettingCategory | None,
) -> CanonicalClassificationView | None:
    if row is None or category is None:
        return None
    return CanonicalClassificationView(
        category_id=row.category_id,
        category_name=category.canonical_name,
        risk_level=row.risk_level,
        risk_rationale=row.risk_rationale,
        actor=row.actor,
        updated_at=row.updated_at,
    )


def _classification_snapshot(
    row: CanonicalSettingClassification | None,
) -> dict[str, str] | None:
    if row is None:
        return None
    return {
        "category_id": row.category_id,
        "risk_level": row.risk_level,
        "risk_rationale": row.risk_rationale,
    }
