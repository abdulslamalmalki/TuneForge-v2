"""Persisted, audited hardware applicability for manuals."""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import (
    Chipset,
    ChipsetFamily,
    HardwarePlatform,
    Manual,
    ManualApplicability,
    ManualApplicabilityTarget,
    ManualEvent,
    MotherboardModel,
)
from app.domain.extraction_runs import utc_now_iso
from app.normalization.hardware import (
    NORMALIZATION_VERSION,
    SCOPE_CHIPSET,
    SCOPE_CHIPSET_FAMILY,
    SCOPE_CHIPSET_SET,
    SCOPE_MODEL,
    SCOPE_PLATFORM,
    ApplicabilityNormalization,
    document_type_for_applicability,
    normalize_applicability,
)

ACTION_NORMALIZED = "hardware_applicability_normalized"
MIGRATION_ACTOR = "system-migration"

TARGET_PLATFORM = "platform"
TARGET_CHIPSET_FAMILY = "chipset_family"
TARGET_CHIPSET = "chipset"
TARGET_MOTHERBOARD_MODEL = "motherboard_model"


@dataclass(frozen=True)
class ApplicabilityTarget:
    target_type: str
    target_id: str
    canonical_name: str


@dataclass(frozen=True)
class ApplicabilityView:
    manual_id: int
    scope: str
    normalization_version: str
    model_status: str
    detected_model_id: str | None
    targets: tuple[ApplicabilityTarget, ...]
    warnings: tuple[str, ...]

    @property
    def target_ids(self) -> tuple[str, ...]:
        return tuple(target.target_id for target in self.targets)


def normalize_manual(manual: Manual) -> ApplicabilityNormalization:
    identity_text = " ".join(
        value
        for value in (
            manual.original_filename,
            manual.document_title,
            manual.model_name,
            manual.chipset,
        )
        if value
    )
    return normalize_applicability(
        vendor=manual.vendor,
        model_value=manual.model_name,
        chipset_value=manual.chipset,
        identity_text=identity_text,
        document_type=document_type_for_applicability(
            manual.document_type,
            manual.document_title,
        ),
    )


def sync_manual_applicability(
    session: Session,
    manual: Manual,
    *,
    actor: str,
    created_at: str | None = None,
) -> bool:
    """Recompute one manual's projection and append an event when it changes.

    The caller owns the transaction. Raw provenance fields are never changed.
    """
    result = normalize_manual(manual)
    before_view = get_manual_applicability(session, manual.id)
    before = snapshot(before_view) if before_view else None
    after = _result_snapshot(result)
    if before is not None and _comparable_snapshot(before) == _comparable_snapshot(after):
        return False

    now = created_at or utc_now_iso()
    row = session.get(ManualApplicability, manual.id)
    if row is None:
        row = ManualApplicability(
            manual_id=manual.id,
            scope=result.scope,
            normalization_version=NORMALIZATION_VERSION,
            model_status=result.model.status,
            detected_model_id=result.model.model_id,
            warnings_json=json.dumps(list(result.warnings), sort_keys=True),
            normalized_at=now,
        )
        session.add(row)
        session.flush()
    else:
        row.scope = result.scope
        row.normalization_version = NORMALIZATION_VERSION
        row.model_status = result.model.status
        row.detected_model_id = result.model.model_id
        row.warnings_json = json.dumps(list(result.warnings), sort_keys=True)
        row.normalized_at = now
        session.execute(
            delete(ManualApplicabilityTarget).where(
                ManualApplicabilityTarget.manual_id == manual.id
            )
        )

    for ordinal, target_id in enumerate(result.target_ids):
        session.add(_target_row(manual.id, ordinal, result.scope, target_id))
    session.add(
        ManualEvent(
            manual_id=manual.id,
            action=ACTION_NORMALIZED,
            actor=actor,
            detail=json.dumps(
                {
                    "before": before,
                    "after": after,
                    "raw_model_name": manual.model_name,
                    "raw_chipset": manual.chipset,
                    "document_type": document_type_for_applicability(
                        manual.document_type,
                        manual.document_title,
                    ),
                },
                sort_keys=True,
            ),
            created_at=now,
        )
    )
    return True


def get_manual_applicability(session: Session, manual_id: int) -> ApplicabilityView | None:
    row = session.get(ManualApplicability, manual_id)
    if row is None:
        return None
    target_rows = list(
        session.scalars(
            select(ManualApplicabilityTarget)
            .where(ManualApplicabilityTarget.manual_id == manual_id)
            .order_by(ManualApplicabilityTarget.ordinal)
        )
    )
    return ApplicabilityView(
        manual_id=manual_id,
        scope=row.scope,
        normalization_version=row.normalization_version,
        model_status=row.model_status,
        detected_model_id=row.detected_model_id,
        targets=tuple(_target_view(session, target) for target in target_rows),
        warnings=tuple(_warnings(row.warnings_json)),
    )


def get_manual_applicability_many(
    session: Session,
    manual_ids: set[int] | tuple[int, ...] | list[int],
) -> dict[int, ApplicabilityView]:
    """Batch the public read projection without changing single-record semantics."""

    requested = tuple(sorted(set(manual_ids)))
    if not requested:
        return {}
    projections = list(
        session.scalars(
            select(ManualApplicability).where(ManualApplicability.manual_id.in_(requested))
        )
    )
    target_rows = list(
        session.scalars(
            select(ManualApplicabilityTarget)
            .where(ManualApplicabilityTarget.manual_id.in_(requested))
            .order_by(ManualApplicabilityTarget.manual_id, ManualApplicabilityTarget.ordinal)
        )
    )
    names: dict[tuple[str, str], str] = {}
    identity_queries = (
        (
            TARGET_PLATFORM,
            HardwarePlatform,
            HardwarePlatform.platform_id,
            {row.platform_id for row in target_rows if row.platform_id},
        ),
        (
            TARGET_CHIPSET_FAMILY,
            ChipsetFamily,
            ChipsetFamily.family_id,
            {row.chipset_family_id for row in target_rows if row.chipset_family_id},
        ),
        (
            TARGET_CHIPSET,
            Chipset,
            Chipset.chipset_id,
            {row.chipset_id for row in target_rows if row.chipset_id},
        ),
        (
            TARGET_MOTHERBOARD_MODEL,
            MotherboardModel,
            MotherboardModel.model_id,
            {row.motherboard_model_id for row in target_rows if row.motherboard_model_id},
        ),
    )
    for target_type, model, id_column, ids in identity_queries:
        if ids:
            for identity in session.scalars(select(model).where(id_column.in_(ids))):
                identity_id = getattr(identity, id_column.key)
                names[(target_type, identity_id)] = identity.canonical_name
    grouped: dict[int, list[ApplicabilityTarget]] = {manual_id: [] for manual_id in requested}
    for row in target_rows:
        target_type, target_id = _target_identity(row)
        try:
            name = names[(target_type, target_id)]
        except KeyError:
            raise RuntimeError(
                f"Applicability target {row.manual_id}/{row.ordinal} is orphaned."
            ) from None
        grouped[row.manual_id].append(ApplicabilityTarget(target_type, target_id, name))
    return {
        row.manual_id: ApplicabilityView(
            manual_id=row.manual_id,
            scope=row.scope,
            normalization_version=row.normalization_version,
            model_status=row.model_status,
            detected_model_id=row.detected_model_id,
            targets=tuple(grouped[row.manual_id]),
            warnings=tuple(_warnings(row.warnings_json)),
        )
        for row in projections
    }


def snapshot(view: ApplicabilityView | None) -> dict[str, object] | None:
    if view is None:
        return None
    return {
        "normalization_version": view.normalization_version,
        "scope": view.scope,
        "target_ids": list(view.target_ids),
        "model_status": view.model_status,
        "detected_model_id": view.detected_model_id,
        "warnings": list(view.warnings),
    }


def _result_snapshot(result: ApplicabilityNormalization) -> dict[str, object]:
    return {
        "normalization_version": NORMALIZATION_VERSION,
        "scope": result.scope,
        "target_ids": list(result.target_ids),
        "model_status": result.model.status,
        "detected_model_id": result.model.model_id,
        "warnings": list(result.warnings),
    }


def _comparable_snapshot(value: dict[str, object]) -> tuple[object, ...]:
    return (
        value["normalization_version"],
        value["scope"],
        tuple(value["target_ids"]),
        value["model_status"],
        value["detected_model_id"],
        tuple(value["warnings"]),
    )


def _target_row(
    manual_id: int,
    ordinal: int,
    scope: str,
    target_id: str,
) -> ManualApplicabilityTarget:
    values: dict[str, object] = {
        "manual_id": manual_id,
        "ordinal": ordinal,
        "platform_id": None,
        "chipset_family_id": None,
        "chipset_id": None,
        "motherboard_model_id": None,
    }
    if scope == SCOPE_PLATFORM:
        values["platform_id"] = target_id
    elif scope == SCOPE_CHIPSET_FAMILY:
        values["chipset_family_id"] = target_id
    elif scope in {SCOPE_CHIPSET, SCOPE_CHIPSET_SET}:
        values["chipset_id"] = target_id
    elif scope == SCOPE_MODEL:
        values["motherboard_model_id"] = target_id
    else:
        raise ValueError(f"Unknown applicability target scope: {scope}")
    return ManualApplicabilityTarget(**values)


def _target_view(session: Session, row: ManualApplicabilityTarget) -> ApplicabilityTarget:
    if row.platform_id:
        definition = session.get(HardwarePlatform, row.platform_id)
        return ApplicabilityTarget(TARGET_PLATFORM, row.platform_id, definition.canonical_name)
    if row.chipset_family_id:
        definition = session.get(ChipsetFamily, row.chipset_family_id)
        return ApplicabilityTarget(
            TARGET_CHIPSET_FAMILY,
            row.chipset_family_id,
            definition.canonical_name,
        )
    if row.chipset_id:
        definition = session.get(Chipset, row.chipset_id)
        return ApplicabilityTarget(TARGET_CHIPSET, row.chipset_id, definition.canonical_name)
    if row.motherboard_model_id:
        definition = session.get(MotherboardModel, row.motherboard_model_id)
        return ApplicabilityTarget(
            TARGET_MOTHERBOARD_MODEL,
            row.motherboard_model_id,
            definition.canonical_name,
        )
    raise RuntimeError(f"Applicability target {row.manual_id}/{row.ordinal} has no identity.")


def _target_identity(row: ManualApplicabilityTarget) -> tuple[str, str]:
    if row.platform_id:
        return TARGET_PLATFORM, row.platform_id
    if row.chipset_family_id:
        return TARGET_CHIPSET_FAMILY, row.chipset_family_id
    if row.chipset_id:
        return TARGET_CHIPSET, row.chipset_id
    if row.motherboard_model_id:
        return TARGET_MOTHERBOARD_MODEL, row.motherboard_model_id
    raise RuntimeError(f"Applicability target {row.manual_id}/{row.ordinal} has no identity.")


def _warnings(value: str) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return ["Stored applicability warnings are invalid."]
    return [str(item) for item in parsed]
