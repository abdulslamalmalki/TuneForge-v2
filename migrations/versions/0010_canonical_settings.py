"""Add reviewer-controlled canonical setting identities and mappings.

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "canonical_settings",
        sa.Column("canonical_id", sa.Text(), primary_key=True),
        sa.Column("canonical_name", sa.Text(), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
    )
    op.create_table(
        "canonical_setting_aliases",
        sa.Column("normalized_alias", sa.Text(), primary_key=True),
        sa.Column(
            "canonical_id",
            sa.Text(),
            sa.ForeignKey("canonical_settings.canonical_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("display_alias", sa.Text(), nullable=False),
        sa.Column(
            "vendor_id",
            sa.Text(),
            sa.ForeignKey("vendors.vendor_id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_canonical_setting_aliases_canonical_id",
        "canonical_setting_aliases",
        ["canonical_id"],
    )
    op.create_index(
        "ix_canonical_setting_aliases_vendor_id",
        "canonical_setting_aliases",
        ["vendor_id"],
    )
    op.create_table(
        "setting_canonical_mappings",
        sa.Column(
            "setting_id",
            sa.Integer(),
            sa.ForeignKey("settings.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "canonical_id",
            sa.Text(),
            sa.ForeignKey("canonical_settings.canonical_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("mapping_method", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "mapping_method IN ('reviewer', 'seed')",
            name="ck_setting_canonical_mapping_method",
        ),
    )
    op.create_index(
        "ix_setting_canonical_mappings_canonical_id",
        "setting_canonical_mappings",
        ["canonical_id"],
    )
    op.create_table(
        "canonical_mapping_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "setting_id",
            sa.Integer(),
            sa.ForeignKey("settings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_canonical_mapping_events_setting_id",
        "canonical_mapping_events",
        ["setting_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_canonical_mapping_events_setting_id",
        table_name="canonical_mapping_events",
    )
    op.drop_table("canonical_mapping_events")
    op.drop_index(
        "ix_setting_canonical_mappings_canonical_id",
        table_name="setting_canonical_mappings",
    )
    op.drop_table("setting_canonical_mappings")
    op.drop_index(
        "ix_canonical_setting_aliases_vendor_id",
        table_name="canonical_setting_aliases",
    )
    op.drop_index(
        "ix_canonical_setting_aliases_canonical_id",
        table_name="canonical_setting_aliases",
    )
    op.drop_table("canonical_setting_aliases")
    op.drop_table("canonical_settings")
