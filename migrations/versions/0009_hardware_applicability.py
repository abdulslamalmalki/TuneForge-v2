"""Persist normalized hardware identities and manual applicability.

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-16
"""

import json

import sqlalchemy as sa
from alembic import op

from app.normalization.hardware import (
    CHIPSET_FAMILIES,
    CHIPSETS,
    MOTHERBOARD_MODELS,
    NORMALIZATION_VERSION,
    PLATFORMS,
    SCOPE_CHIPSET,
    SCOPE_CHIPSET_FAMILY,
    SCOPE_CHIPSET_SET,
    SCOPE_MODEL,
    SCOPE_PLATFORM,
    document_type_for_applicability,
    normalize_applicability,
)

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if NORMALIZATION_VERSION != "1.0":
        raise RuntimeError("Migration 0009 requires hardware normalization contract 1.0.")

    op.create_table(
        "hardware_platforms",
        sa.Column("platform_id", sa.Text(), primary_key=True),
        sa.Column("canonical_name", sa.Text(), nullable=False, unique=True),
        sa.Column("cpu_vendor", sa.Text(), nullable=False),
        sa.Column("socket_name", sa.Text(), nullable=False),
    )
    op.create_table(
        "chipset_families",
        sa.Column("family_id", sa.Text(), primary_key=True),
        sa.Column("canonical_name", sa.Text(), nullable=False, unique=True),
        sa.Column(
            "platform_id",
            sa.Text(),
            sa.ForeignKey("hardware_platforms.platform_id", ondelete="RESTRICT"),
            nullable=False,
        ),
    )
    op.create_index("ix_chipset_families_platform_id", "chipset_families", ["platform_id"])
    op.create_table(
        "chipsets",
        sa.Column("chipset_id", sa.Text(), primary_key=True),
        sa.Column("canonical_name", sa.Text(), nullable=False, unique=True),
        sa.Column(
            "family_id",
            sa.Text(),
            sa.ForeignKey("chipset_families.family_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "platform_id",
            sa.Text(),
            sa.ForeignKey("hardware_platforms.platform_id", ondelete="RESTRICT"),
            nullable=False,
        ),
    )
    op.create_index("ix_chipsets_family_id", "chipsets", ["family_id"])
    op.create_index("ix_chipsets_platform_id", "chipsets", ["platform_id"])
    op.create_table(
        "motherboard_models",
        sa.Column("model_id", sa.Text(), primary_key=True),
        sa.Column(
            "vendor_id",
            sa.Text(),
            sa.ForeignKey("vendors.vendor_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column(
            "chipset_id",
            sa.Text(),
            sa.ForeignKey("chipsets.chipset_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "platform_id",
            sa.Text(),
            sa.ForeignKey("hardware_platforms.platform_id", ondelete="RESTRICT"),
            nullable=False,
        ),
    )
    op.create_index("ix_motherboard_models_vendor_id", "motherboard_models", ["vendor_id"])
    op.create_index("ix_motherboard_models_chipset_id", "motherboard_models", ["chipset_id"])
    op.create_index("ix_motherboard_models_platform_id", "motherboard_models", ["platform_id"])
    op.create_table(
        "manual_applicability",
        sa.Column(
            "manual_id",
            sa.Integer(),
            sa.ForeignKey("manuals.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("normalization_version", sa.Text(), nullable=False),
        sa.Column("model_status", sa.Text(), nullable=False),
        sa.Column(
            "detected_model_id",
            sa.Text(),
            sa.ForeignKey("motherboard_models.model_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("warnings_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("normalized_at", sa.Text(), nullable=False),
    )
    op.create_index("ix_manual_applicability_scope", "manual_applicability", ["scope"])
    op.create_index(
        "ix_manual_applicability_detected_model_id",
        "manual_applicability",
        ["detected_model_id"],
    )
    op.create_table(
        "manual_applicability_targets",
        sa.Column(
            "manual_id",
            sa.Integer(),
            sa.ForeignKey("manual_applicability.manual_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("ordinal", sa.Integer(), primary_key=True),
        sa.Column(
            "platform_id",
            sa.Text(),
            sa.ForeignKey("hardware_platforms.platform_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "chipset_family_id",
            sa.Text(),
            sa.ForeignKey("chipset_families.family_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "chipset_id",
            sa.Text(),
            sa.ForeignKey("chipsets.chipset_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "motherboard_model_id",
            sa.Text(),
            sa.ForeignKey("motherboard_models.model_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.CheckConstraint(
            "(platform_id IS NOT NULL) + (chipset_family_id IS NOT NULL) + "
            "(chipset_id IS NOT NULL) + (motherboard_model_id IS NOT NULL) = 1",
            name="ck_manual_applicability_target_exactly_one",
        ),
    )
    op.create_index(
        "ix_manual_applicability_targets_platform",
        "manual_applicability_targets",
        ["platform_id"],
    )
    op.create_index(
        "ix_manual_applicability_targets_family",
        "manual_applicability_targets",
        ["chipset_family_id"],
    )
    op.create_index(
        "ix_manual_applicability_targets_chipset",
        "manual_applicability_targets",
        ["chipset_id"],
    )
    op.create_index(
        "ix_manual_applicability_targets_model",
        "manual_applicability_targets",
        ["motherboard_model_id"],
    )

    connection = op.get_bind()
    _seed_contract(connection)
    _backfill_manuals(connection)


def _seed_contract(connection) -> None:
    for item in PLATFORMS:
        connection.execute(
            sa.text(
                "INSERT INTO hardware_platforms "
                "(platform_id, canonical_name, cpu_vendor, socket_name) "
                "VALUES (:platform_id, :canonical_name, :cpu_vendor, :socket_name)"
            ),
            {
                "platform_id": item.platform_id,
                "canonical_name": item.canonical_name,
                "cpu_vendor": item.cpu_vendor,
                "socket_name": item.socket_name,
            },
        )
    for item in CHIPSET_FAMILIES:
        connection.execute(
            sa.text(
                "INSERT INTO chipset_families (family_id, canonical_name, platform_id) "
                "VALUES (:family_id, :canonical_name, :platform_id)"
            ),
            {
                "family_id": item.family_id,
                "canonical_name": item.canonical_name,
                "platform_id": item.platform_id,
            },
        )
    for item in CHIPSETS:
        connection.execute(
            sa.text(
                "INSERT INTO chipsets (chipset_id, canonical_name, family_id, platform_id) "
                "VALUES (:chipset_id, :canonical_name, :family_id, :platform_id)"
            ),
            {
                "chipset_id": item.chipset_id,
                "canonical_name": item.canonical_name,
                "family_id": item.family_id,
                "platform_id": item.platform_id,
            },
        )
    for item in MOTHERBOARD_MODELS:
        connection.execute(
            sa.text(
                "INSERT INTO motherboard_models "
                "(model_id, vendor_id, canonical_name, chipset_id, platform_id) "
                "VALUES (:model_id, :vendor_id, :canonical_name, :chipset_id, :platform_id)"
            ),
            {
                "model_id": item.model_id,
                "vendor_id": item.vendor_id,
                "canonical_name": item.canonical_name,
                "chipset_id": item.chipset_id,
                "platform_id": item.platform_id,
            },
        )


def _backfill_manuals(connection) -> None:
    rows = connection.execute(
        sa.text(
            "SELECT id, original_filename, vendor, model_name, document_title, "
            "chipset, document_type, created_at FROM manuals ORDER BY id"
        )
    ).mappings()
    for row in rows:
        identity = " ".join(
            str(value)
            for value in (
                row["original_filename"],
                row["document_title"],
                row["model_name"],
                row["chipset"],
            )
            if value
        )
        document_type = document_type_for_applicability(
            row["document_type"],
            row["document_title"] or "",
        )
        result = normalize_applicability(
            vendor=row["vendor"],
            model_value=row["model_name"],
            chipset_value=row["chipset"],
            identity_text=identity,
            document_type=document_type,
        )
        after = {
            "normalization_version": NORMALIZATION_VERSION,
            "scope": result.scope,
            "target_ids": list(result.target_ids),
            "model_status": result.model.status,
            "detected_model_id": result.model.model_id,
            "warnings": list(result.warnings),
        }
        connection.execute(
            sa.text(
                "INSERT INTO manual_applicability "
                "(manual_id, scope, normalization_version, model_status, "
                "detected_model_id, warnings_json, normalized_at) "
                "VALUES (:manual_id, :scope, :version, :model_status, "
                ":model_id, :warnings, :normalized_at)"
            ),
            {
                "manual_id": row["id"],
                "scope": result.scope,
                "version": NORMALIZATION_VERSION,
                "model_status": result.model.status,
                "model_id": result.model.model_id,
                "warnings": json.dumps(list(result.warnings), sort_keys=True),
                "normalized_at": row["created_at"],
            },
        )
        for ordinal, target_id in enumerate(result.target_ids):
            values = _target_values(result.scope, target_id)
            connection.execute(
                sa.text(
                    "INSERT INTO manual_applicability_targets "
                    "(manual_id, ordinal, platform_id, chipset_family_id, "
                    "chipset_id, motherboard_model_id) "
                    "VALUES (:manual_id, :ordinal, :platform_id, :family_id, "
                    ":chipset_id, :model_id)"
                ),
                {
                    "manual_id": row["id"],
                    "ordinal": ordinal,
                    **values,
                },
            )
        connection.execute(
            sa.text(
                "INSERT INTO manual_events "
                "(manual_id, action, actor, detail, created_at) "
                "VALUES (:manual_id, 'hardware_applicability_normalized', "
                "'system-migration', :detail, :created_at)"
            ),
            {
                "manual_id": row["id"],
                "detail": json.dumps(
                    {
                        "before": None,
                        "after": after,
                        "raw_model_name": row["model_name"],
                        "raw_chipset": row["chipset"],
                        "document_type": document_type,
                    },
                    sort_keys=True,
                ),
                "created_at": row["created_at"],
            },
        )


def _target_values(scope: str, target_id: str) -> dict[str, str | None]:
    values = {
        "platform_id": None,
        "family_id": None,
        "chipset_id": None,
        "model_id": None,
    }
    if scope == SCOPE_PLATFORM:
        values["platform_id"] = target_id
    elif scope == SCOPE_CHIPSET_FAMILY:
        values["family_id"] = target_id
    elif scope in {SCOPE_CHIPSET, SCOPE_CHIPSET_SET}:
        values["chipset_id"] = target_id
    elif scope == SCOPE_MODEL:
        values["model_id"] = target_id
    else:
        raise RuntimeError(f"Scope {scope!r} cannot have an applicability target.")
    return values


def downgrade() -> None:
    op.drop_table("manual_applicability_targets")
    op.drop_table("manual_applicability")
    op.drop_table("motherboard_models")
    op.drop_table("chipsets")
    op.drop_table("chipset_families")
    op.drop_table("hardware_platforms")
