"""Add reviewer-controlled knowledge conflict records.

Revision ID: 0026
Revises: 0025
Create Date: 2026-08-14
"""

import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "guidance_claim_conflicts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "left_claim_id",
            sa.Integer(),
            sa.ForeignKey("guidance_claims.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "right_claim_id",
            sa.Integer(),
            sa.ForeignKey("guidance_claims.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("detection_reason", sa.Text(), nullable=False),
        sa.Column("hardware_relationship", sa.Text(), nullable=False),
        sa.Column("bios_relationship", sa.Text(), nullable=False),
        sa.Column("left_evidence_strength", sa.Text(), nullable=False),
        sa.Column("right_evidence_strength", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("reviewer_note", sa.Text(), nullable=True),
        sa.Column("reviewer", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "left_claim_id < right_claim_id",
            name="ck_guidance_conflict_ordered_pair",
        ),
        sa.CheckConstraint(
            "hardware_relationship IN ('overlap', 'disjoint', 'unknown')",
            name="ck_guidance_conflict_hardware_relationship",
        ),
        sa.CheckConstraint(
            "bios_relationship IN "
            "('overlap', 'version_difference', 'disjoint_context', 'unknown')",
            name="ck_guidance_conflict_bios_relationship",
        ),
        sa.CheckConstraint(
            "left_evidence_strength IN "
            "('primary_official', 'official_manual', 'mixed_official') AND "
            "right_evidence_strength IN "
            "('primary_official', 'official_manual', 'mixed_official')",
            name="ck_guidance_conflict_evidence_strength",
        ),
        sa.CheckConstraint(
            "status IN "
            "('pending', 'confirmed', 'resolved_scope', 'resolved_version', 'dismissed')",
            name="ck_guidance_conflict_status",
        ),
        sa.CheckConstraint(
            "length(detection_reason) BETWEEN 1 AND 1000",
            name="ck_guidance_conflict_reason_length",
        ),
        sa.CheckConstraint(
            "reviewer_note IS NULL OR length(reviewer_note) BETWEEN 1 AND 2000",
            name="ck_guidance_conflict_note_length",
        ),
    )
    op.create_index(
        "ux_guidance_claim_conflict_pair",
        "guidance_claim_conflicts",
        ["left_claim_id", "right_claim_id"],
        unique=True,
    )
    op.create_index(
        "ix_guidance_claim_conflicts_status",
        "guidance_claim_conflicts",
        ["status"],
    )
    op.create_table(
        "guidance_claim_conflict_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "conflict_id",
            sa.Integer(),
            sa.ForeignKey("guidance_claim_conflicts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_guidance_claim_conflict_events_conflict",
        "guidance_claim_conflict_events",
        ["conflict_id", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_guidance_claim_conflict_events_conflict",
        table_name="guidance_claim_conflict_events",
    )
    op.drop_table("guidance_claim_conflict_events")
    op.drop_index(
        "ix_guidance_claim_conflicts_status",
        table_name="guidance_claim_conflicts",
    )
    op.drop_index(
        "ux_guidance_claim_conflict_pair",
        table_name="guidance_claim_conflicts",
    )
    op.drop_table("guidance_claim_conflicts")
