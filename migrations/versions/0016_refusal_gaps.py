"""Add append-only refusal gaps and reviewed official referral pointers.

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-19
"""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "unanswered_lookups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("surface", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("normalized_query", sa.Text(), nullable=False),
        sa.Column("nearest_matches_json", sa.Text(), nullable=False),
        sa.Column("context_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "surface IN ('search', 'guidance')",
            name="ck_unanswered_lookup_surface",
        ),
        sa.CheckConstraint(
            "reason IN ('no_approved_search_results', 'no_exact_guidance_match', "
            "'no_accepted_guidance_claims')",
            name="ck_unanswered_lookup_reason",
        ),
        sa.CheckConstraint(
            "length(query_text) BETWEEN 1 AND 300",
            name="ck_unanswered_lookup_query_length",
        ),
    )
    op.create_index("ix_unanswered_lookups_created_at", "unanswered_lookups", ["created_at"])
    op.create_index(
        "ix_unanswered_lookups_normalized_query", "unanswered_lookups", ["normalized_query"]
    )
    op.create_index("ix_unanswered_lookups_reason", "unanswered_lookups", ["reason"])

    op.create_table(
        "referral_pointers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("publisher_id", sa.Text(), nullable=False),
        sa.Column("publisher_name", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("document_id", sa.Text(), nullable=True),
        sa.Column("document_version", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("reviewer", sa.Text(), nullable=False),
        sa.Column("review_note", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("retired_at", sa.Text(), nullable=True),
        sa.CheckConstraint("status IN ('active', 'retired')", name="ck_referral_pointer_status"),
    )
    op.create_index("ix_referral_pointers_status", "referral_pointers", ["status"])
    op.create_index("ix_referral_pointers_publisher_id", "referral_pointers", ["publisher_id"])
    op.create_index(
        "ux_referral_pointer_identity",
        "referral_pointers",
        ["publisher_id", "title", "source_url", "document_version"],
        unique=True,
    )
    op.create_table(
        "referral_pointer_settings",
        sa.Column(
            "pointer_id",
            sa.Integer(),
            sa.ForeignKey("referral_pointers.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column(
            "canonical_id",
            sa.Text(),
            sa.ForeignKey("canonical_settings.canonical_id", ondelete="RESTRICT"),
            primary_key=True,
        ),
    )
    op.create_index(
        "ix_referral_pointer_settings_canonical_id",
        "referral_pointer_settings",
        ["canonical_id"],
    )
    op.create_table(
        "referral_pointer_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "pointer_id",
            sa.Integer(),
            sa.ForeignKey("referral_pointers.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_referral_pointer_events_pointer_id", "referral_pointer_events", ["pointer_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_referral_pointer_events_pointer_id", table_name="referral_pointer_events")
    op.drop_table("referral_pointer_events")
    op.drop_index(
        "ix_referral_pointer_settings_canonical_id", table_name="referral_pointer_settings"
    )
    op.drop_table("referral_pointer_settings")
    op.drop_index("ux_referral_pointer_identity", table_name="referral_pointers")
    op.drop_index("ix_referral_pointers_publisher_id", table_name="referral_pointers")
    op.drop_index("ix_referral_pointers_status", table_name="referral_pointers")
    op.drop_table("referral_pointers")
    op.drop_index("ix_unanswered_lookups_reason", table_name="unanswered_lookups")
    op.drop_index("ix_unanswered_lookups_normalized_query", table_name="unanswered_lookups")
    op.drop_index("ix_unanswered_lookups_created_at", table_name="unanswered_lookups")
    op.drop_table("unanswered_lookups")
