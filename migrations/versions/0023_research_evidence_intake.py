"""Add reviewed live-research evidence intake boundaries.

Revision ID: 0023
Revises: 0022
Create Date: 2026-08-14
"""

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_evidence_intakes",
        sa.Column(
            "result_id",
            sa.Integer(),
            sa.ForeignKey("trusted_web_research_results.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "setting_id",
            sa.Integer(),
            sa.ForeignKey("settings.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "canonical_id",
            sa.Text(),
            sa.ForeignKey("canonical_settings.canonical_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("hardware_scope_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("bios_scope_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("proposed_claim_text", sa.Text(), nullable=True),
        sa.Column("proposed_claim_type", sa.Text(), nullable=True),
        sa.Column(
            "evidence_source_id",
            sa.Integer(),
            sa.ForeignKey("evidence_sources.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "evidence_passage_id",
            sa.Integer(),
            sa.ForeignKey("evidence_passages.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "draft_claim_id",
            sa.Integer(),
            sa.ForeignKey("guidance_claims.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("reviewer", sa.Text(), nullable=False),
        sa.Column("review_note", sa.Text(), nullable=False),
        sa.Column("reviewed_at", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "status IN ('evidence_intake', 'needs_corroboration', 'low_quality', "
            "'rejected', 'archived', 'evidence_linked', 'claim_drafted')",
            name="ck_research_evidence_intake_status",
        ),
        sa.CheckConstraint(
            "proposed_claim_type IS NULL OR proposed_claim_type IN "
            "('definition', 'behavior', 'constraint', 'safety')",
            name="ck_research_evidence_intake_claim_type",
        ),
        sa.CheckConstraint(
            "(evidence_source_id IS NULL AND evidence_passage_id IS NULL) OR "
            "(evidence_source_id IS NOT NULL AND evidence_passage_id IS NOT NULL)",
            name="ck_research_evidence_intake_evidence_pair",
        ),
    )
    op.create_index(
        "ix_research_evidence_intakes_status",
        "research_evidence_intakes",
        ["status"],
    )
    op.create_index(
        "ix_research_evidence_intakes_canonical_id",
        "research_evidence_intakes",
        ["canonical_id"],
    )
    op.create_index(
        "ix_research_evidence_intakes_setting_id",
        "research_evidence_intakes",
        ["setting_id"],
    )

    op.create_table(
        "research_evidence_intake_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "result_id",
            sa.Integer(),
            sa.ForeignKey("research_evidence_intakes.result_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_research_evidence_intake_events_result_id",
        "research_evidence_intake_events",
        ["result_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_research_evidence_intake_events_result_id",
        table_name="research_evidence_intake_events",
    )
    op.drop_table("research_evidence_intake_events")
    op.drop_index(
        "ix_research_evidence_intakes_setting_id",
        table_name="research_evidence_intakes",
    )
    op.drop_index(
        "ix_research_evidence_intakes_canonical_id",
        table_name="research_evidence_intakes",
    )
    op.drop_index(
        "ix_research_evidence_intakes_status",
        table_name="research_evidence_intakes",
    )
    op.drop_table("research_evidence_intakes")
