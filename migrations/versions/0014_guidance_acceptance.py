"""Add reviewer-controlled guidance claims and evidence links.

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-17
"""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "guidance_claims",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("claim_text", sa.Text(), nullable=False),
        sa.Column("claim_type", sa.Text(), nullable=False),
        sa.Column("applicability_scope", sa.Text(), nullable=False),
        sa.Column(
            "cpu_model_id",
            sa.Text(),
            sa.ForeignKey("cpu_models.cpu_model_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("reviewer", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "claim_type IN ('definition', 'behavior', 'constraint', 'safety')",
            name="ck_guidance_claim_type",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'approved', 'rejected')",
            name="ck_guidance_claim_status",
        ),
        sa.CheckConstraint(
            "(applicability_scope = 'source_scope' AND cpu_model_id IS NULL) OR "
            "(applicability_scope = 'cpu_model' AND cpu_model_id IS NOT NULL)",
            name="ck_guidance_claim_applicability",
        ),
    )
    op.create_index("ix_guidance_claims_status", "guidance_claims", ["status"])
    op.create_index("ix_guidance_claims_claim_type", "guidance_claims", ["claim_type"])
    op.create_index("ix_guidance_claims_cpu_model_id", "guidance_claims", ["cpu_model_id"])
    op.create_table(
        "guidance_claim_evidence",
        sa.Column(
            "claim_id",
            sa.Integer(),
            sa.ForeignKey("guidance_claims.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "passage_id",
            sa.Integer(),
            sa.ForeignKey("evidence_passages.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("relation", sa.Text(), nullable=False),
        sa.CheckConstraint("relation IN ('supports')", name="ck_guidance_evidence_relation"),
    )
    op.create_index(
        "ix_guidance_claim_evidence_passage_id",
        "guidance_claim_evidence",
        ["passage_id"],
    )
    op.create_table(
        "guidance_claim_settings",
        sa.Column(
            "claim_id",
            sa.Integer(),
            sa.ForeignKey("guidance_claims.id", ondelete="CASCADE"),
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
        "ix_guidance_claim_settings_canonical_id",
        "guidance_claim_settings",
        ["canonical_id"],
    )
    op.create_table(
        "guidance_claim_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "claim_id",
            sa.Integer(),
            sa.ForeignKey("guidance_claims.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index("ix_guidance_claim_events_claim_id", "guidance_claim_events", ["claim_id"])


def downgrade() -> None:
    op.drop_index("ix_guidance_claim_events_claim_id", table_name="guidance_claim_events")
    op.drop_table("guidance_claim_events")
    op.drop_index("ix_guidance_claim_settings_canonical_id", table_name="guidance_claim_settings")
    op.drop_table("guidance_claim_settings")
    op.drop_index("ix_guidance_claim_evidence_passage_id", table_name="guidance_claim_evidence")
    op.drop_table("guidance_claim_evidence")
    op.drop_index("ix_guidance_claims_cpu_model_id", table_name="guidance_claims")
    op.drop_index("ix_guidance_claims_claim_type", table_name="guidance_claims")
    op.drop_index("ix_guidance_claims_status", table_name="guidance_claims")
    op.drop_table("guidance_claims")
