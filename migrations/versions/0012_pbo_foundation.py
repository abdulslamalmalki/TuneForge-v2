"""Add CPU identity tables for the PBO guidance pilot.

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-17
"""

import sqlalchemy as sa
from alembic import op

from app.normalization.cpu import CPU_ARCHITECTURES, CPU_FAMILIES, CPU_MODELS
from app.normalization.vendors import normalized_key

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cpu_architectures",
        sa.Column("architecture_id", sa.Text(), primary_key=True),
        sa.Column("vendor_name", sa.Text(), nullable=False),
        sa.Column("canonical_name", sa.Text(), nullable=False, unique=True),
    )
    op.create_table(
        "cpu_families",
        sa.Column("family_id", sa.Text(), primary_key=True),
        sa.Column(
            "architecture_id",
            sa.Text(),
            sa.ForeignKey("cpu_architectures.architecture_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "platform_id",
            sa.Text(),
            sa.ForeignKey("hardware_platforms.platform_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("canonical_name", sa.Text(), nullable=False, unique=True),
    )
    op.create_index("ix_cpu_families_architecture_id", "cpu_families", ["architecture_id"])
    op.create_index("ix_cpu_families_platform_id", "cpu_families", ["platform_id"])
    op.create_table(
        "cpu_models",
        sa.Column("cpu_model_id", sa.Text(), primary_key=True),
        sa.Column(
            "family_id",
            sa.Text(),
            sa.ForeignKey("cpu_families.family_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "architecture_id",
            sa.Text(),
            sa.ForeignKey("cpu_architectures.architecture_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "platform_id",
            sa.Text(),
            sa.ForeignKey("hardware_platforms.platform_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("canonical_name", sa.Text(), nullable=False, unique=True),
        sa.Column("is_x3d", sa.Integer(), nullable=False),
        sa.CheckConstraint("is_x3d IN (0, 1)", name="ck_cpu_model_is_x3d"),
    )
    op.create_index("ix_cpu_models_family_id", "cpu_models", ["family_id"])
    op.create_index("ix_cpu_models_architecture_id", "cpu_models", ["architecture_id"])
    op.create_index("ix_cpu_models_platform_id", "cpu_models", ["platform_id"])
    op.create_table(
        "cpu_model_aliases",
        sa.Column("normalized_alias", sa.Text(), primary_key=True),
        sa.Column(
            "cpu_model_id",
            sa.Text(),
            sa.ForeignKey("cpu_models.cpu_model_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("display_alias", sa.Text(), nullable=False),
    )
    op.create_index("ix_cpu_model_aliases_cpu_model_id", "cpu_model_aliases", ["cpu_model_id"])

    connection = op.get_bind()
    for item in CPU_ARCHITECTURES:
        connection.execute(
            sa.text(
                "INSERT INTO cpu_architectures "
                "(architecture_id, vendor_name, canonical_name) "
                "VALUES (:architecture_id, :vendor_name, :canonical_name)"
            ),
            vars(item),
        )
    for item in CPU_FAMILIES:
        connection.execute(
            sa.text(
                "INSERT INTO cpu_families "
                "(family_id, architecture_id, platform_id, canonical_name) "
                "VALUES (:family_id, :architecture_id, :platform_id, :canonical_name)"
            ),
            vars(item),
        )
    for item in CPU_MODELS:
        connection.execute(
            sa.text(
                "INSERT INTO cpu_models "
                "(cpu_model_id, family_id, architecture_id, platform_id, "
                "canonical_name, is_x3d) VALUES (:cpu_model_id, :family_id, "
                ":architecture_id, :platform_id, :canonical_name, :is_x3d)"
            ),
            {
                "cpu_model_id": item.cpu_model_id,
                "family_id": item.family_id,
                "architecture_id": item.architecture_id,
                "platform_id": item.platform_id,
                "canonical_name": item.canonical_name,
                "is_x3d": int(item.is_x3d),
            },
        )
        aliases = dict.fromkeys((item.canonical_name, *item.aliases))
        for alias in aliases:
            connection.execute(
                sa.text(
                    "INSERT INTO cpu_model_aliases "
                    "(normalized_alias, cpu_model_id, display_alias) "
                    "VALUES (:normalized_alias, :cpu_model_id, :display_alias)"
                ),
                {
                    "normalized_alias": normalized_key(alias),
                    "cpu_model_id": item.cpu_model_id,
                    "display_alias": alias,
                },
            )


def downgrade() -> None:
    op.drop_index("ix_cpu_model_aliases_cpu_model_id", table_name="cpu_model_aliases")
    op.drop_table("cpu_model_aliases")
    op.drop_index("ix_cpu_models_platform_id", table_name="cpu_models")
    op.drop_index("ix_cpu_models_architecture_id", table_name="cpu_models")
    op.drop_index("ix_cpu_models_family_id", table_name="cpu_models")
    op.drop_table("cpu_models")
    op.drop_index("ix_cpu_families_platform_id", table_name="cpu_families")
    op.drop_index("ix_cpu_families_architecture_id", table_name="cpu_families")
    op.drop_table("cpu_families")
    op.drop_table("cpu_architectures")
