"""Add audited guidance candidates and approved source-setting citations.

Revision ID: 0021
Revises: 0020
Create Date: 2026-08-14
"""

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "guidance_claim_source_settings",
        sa.Column(
            "claim_id",
            sa.Integer(),
            sa.ForeignKey("guidance_claims.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "setting_id",
            sa.Integer(),
            sa.ForeignKey("settings.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("relation", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "relation IN ('supports')", name="ck_guidance_source_setting_relation"
        ),
    )
    op.create_index(
        "ix_guidance_claim_source_settings_setting_id",
        "guidance_claim_source_settings",
        ["setting_id"],
    )

    op.create_table(
        "guidance_candidates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "setting_id",
            sa.Integer(),
            sa.ForeignKey("settings.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "canonical_id",
            sa.Text(),
            sa.ForeignKey("canonical_settings.canonical_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("claim_text", sa.Text(), nullable=True),
        sa.Column("claim_type", sa.Text(), nullable=True),
        sa.Column("source_excerpt", sa.Text(), nullable=False),
        sa.Column("source_page_number", sa.Integer(), nullable=False),
        sa.Column("hardware_scope_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("bios_scope_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("source_trust", sa.Text(), nullable=False),
        sa.Column("confidence_basis_points", sa.Integer(), nullable=False),
        sa.Column("priority_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("possible_conflict", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("conflict_detail", sa.Text(), nullable=True),
        sa.Column(
            "duplicate_of_candidate_id",
            sa.Integer(),
            sa.ForeignKey("guidance_candidates.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "promoted_claim_id",
            sa.Integer(),
            sa.ForeignKey("guidance_claims.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("generator_version", sa.Text(), nullable=False),
        sa.Column("evidence_sha256", sa.Text(), nullable=False),
        sa.Column("reviewer", sa.Text(), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "claim_type IS NULL OR claim_type IN "
            "('definition', 'behavior', 'constraint', 'safety')",
            name="ck_guidance_candidate_claim_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'needs_research', 'uncertain', 'rejected', "
            "'skipped', 'duplicate', 'promoted')",
            name="ck_guidance_candidate_status",
        ),
        sa.CheckConstraint(
            "source_trust IN ('trusted_official_manual', 'incomplete')",
            name="ck_guidance_candidate_source_trust",
        ),
        sa.CheckConstraint(
            "confidence_basis_points BETWEEN 0 AND 10000",
            name="ck_guidance_candidate_confidence",
        ),
        sa.CheckConstraint(
            "possible_conflict IN (0, 1)", name="ck_guidance_candidate_conflict_flag"
        ),
        sa.CheckConstraint(
            "source_page_number > 0", name="ck_guidance_candidate_page_number"
        ),
        sa.UniqueConstraint(
            "setting_id",
            "claim_type",
            "evidence_sha256",
            name="ux_guidance_candidate_source_fact",
        ),
    )
    op.create_index("ix_guidance_candidates_status", "guidance_candidates", ["status"])
    op.create_index(
        "ix_guidance_candidates_setting_id", "guidance_candidates", ["setting_id"]
    )
    op.create_index(
        "ix_guidance_candidates_canonical_id", "guidance_candidates", ["canonical_id"]
    )
    op.create_index(
        "ix_guidance_candidates_priority",
        "guidance_candidates",
        ["status", "priority_score"],
    )
    op.create_index(
        "ix_guidance_candidates_promoted_claim_id",
        "guidance_candidates",
        ["promoted_claim_id"],
    )

    op.create_table(
        "guidance_candidate_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "candidate_id",
            sa.Integer(),
            sa.ForeignKey("guidance_candidates.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_guidance_candidate_events_candidate_id",
        "guidance_candidate_events",
        ["candidate_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_guidance_candidate_events_candidate_id",
        table_name="guidance_candidate_events",
    )
    op.drop_table("guidance_candidate_events")
    op.drop_index(
        "ix_guidance_candidates_promoted_claim_id", table_name="guidance_candidates"
    )
    op.drop_index("ix_guidance_candidates_priority", table_name="guidance_candidates")
    op.drop_index("ix_guidance_candidates_canonical_id", table_name="guidance_candidates")
    op.drop_index("ix_guidance_candidates_setting_id", table_name="guidance_candidates")
    op.drop_index("ix_guidance_candidates_status", table_name="guidance_candidates")
    op.drop_table("guidance_candidates")
    op.drop_index(
        "ix_guidance_claim_source_settings_setting_id",
        table_name="guidance_claim_source_settings",
    )
    op.drop_table("guidance_claim_source_settings")
