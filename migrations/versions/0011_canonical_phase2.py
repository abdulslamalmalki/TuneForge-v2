"""Add canonical classifications and reviewer suggestion queue.

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-16
"""

import sqlalchemy as sa
from alembic import op

from app.normalization.canonical_settings import CATEGORIES, RISK_LEVELS

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if RISK_LEVELS != ("unclassified", "low", "moderate", "high", "critical"):
        raise RuntimeError("Migration 0011 requires canonical risk contract 1.0.")
    op.create_table(
        "canonical_setting_categories",
        sa.Column("category_id", sa.Text(), primary_key=True),
        sa.Column("canonical_name", sa.Text(), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False, unique=True),
    )
    connection = op.get_bind()
    for category in CATEGORIES:
        connection.execute(
            sa.text(
                "INSERT INTO canonical_setting_categories "
                "(category_id, canonical_name, description, ordinal) "
                "VALUES (:category_id, :canonical_name, :description, :ordinal)"
            ),
            {
                "category_id": category.category_id,
                "canonical_name": category.canonical_name,
                "description": category.description,
                "ordinal": category.ordinal,
            },
        )
    op.create_table(
        "canonical_setting_classifications",
        sa.Column(
            "canonical_id",
            sa.Text(),
            sa.ForeignKey("canonical_settings.canonical_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "category_id",
            sa.Text(),
            sa.ForeignKey(
                "canonical_setting_categories.category_id",
                ondelete="RESTRICT",
            ),
            nullable=False,
        ),
        sa.Column("risk_level", sa.Text(), nullable=False),
        sa.Column("risk_rationale", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "risk_level IN ('unclassified', 'low', 'moderate', 'high', 'critical')",
            name="ck_canonical_setting_classification_risk",
        ),
    )
    op.create_index(
        "ix_canonical_setting_classifications_category_id",
        "canonical_setting_classifications",
        ["category_id"],
    )
    op.create_index(
        "ix_canonical_setting_classifications_risk_level",
        "canonical_setting_classifications",
        ["risk_level"],
    )
    op.create_table(
        "canonical_classification_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "canonical_id",
            sa.Text(),
            sa.ForeignKey("canonical_settings.canonical_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_canonical_classification_events_canonical_id",
        "canonical_classification_events",
        ["canonical_id"],
    )
    op.create_table(
        "canonical_mapping_suggestions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "setting_id",
            sa.Integer(),
            sa.ForeignKey("settings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "canonical_id",
            sa.Text(),
            sa.ForeignKey("canonical_settings.canonical_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("match_type", sa.Text(), nullable=False),
        sa.Column("matched_alias", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("actor", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("reviewed_at", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "match_type IN ('exact_alias')",
            name="ck_canonical_mapping_suggestion_match_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'dismissed')",
            name="ck_canonical_mapping_suggestion_status",
        ),
    )
    op.create_index(
        "ux_canonical_mapping_suggestions_setting_canonical",
        "canonical_mapping_suggestions",
        ["setting_id", "canonical_id"],
        unique=True,
    )
    op.create_index(
        "ix_canonical_mapping_suggestions_status",
        "canonical_mapping_suggestions",
        ["status"],
    )
    op.create_index(
        "ix_canonical_mapping_suggestions_canonical_id",
        "canonical_mapping_suggestions",
        ["canonical_id"],
    )
    op.create_table(
        "canonical_suggestion_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "suggestion_id",
            sa.Integer(),
            sa.ForeignKey("canonical_mapping_suggestions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_canonical_suggestion_events_suggestion_id",
        "canonical_suggestion_events",
        ["suggestion_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_canonical_suggestion_events_suggestion_id",
        table_name="canonical_suggestion_events",
    )
    op.drop_table("canonical_suggestion_events")
    op.drop_index(
        "ix_canonical_mapping_suggestions_canonical_id",
        table_name="canonical_mapping_suggestions",
    )
    op.drop_index(
        "ix_canonical_mapping_suggestions_status",
        table_name="canonical_mapping_suggestions",
    )
    op.drop_index(
        "ux_canonical_mapping_suggestions_setting_canonical",
        table_name="canonical_mapping_suggestions",
    )
    op.drop_table("canonical_mapping_suggestions")
    op.drop_index(
        "ix_canonical_classification_events_canonical_id",
        table_name="canonical_classification_events",
    )
    op.drop_table("canonical_classification_events")
    op.drop_index(
        "ix_canonical_setting_classifications_risk_level",
        table_name="canonical_setting_classifications",
    )
    op.drop_index(
        "ix_canonical_setting_classifications_category_id",
        table_name="canonical_setting_classifications",
    )
    op.drop_table("canonical_setting_classifications")
    op.drop_table("canonical_setting_categories")
