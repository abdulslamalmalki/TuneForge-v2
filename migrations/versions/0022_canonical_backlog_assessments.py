"""Add complete, append-audited canonical backlog assessments.

Revision ID: 0022
Revises: 0021
Create Date: 2026-08-14
"""

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "canonical_backlog_assessments",
        sa.Column(
            "setting_id",
            sa.Integer(),
            sa.ForeignKey("settings.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column(
            "candidate_canonical_id",
            sa.Text(),
            sa.ForeignKey("canonical_settings.canonical_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("confidence_basis_points", sa.Integer(), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=False),
        sa.Column("engine_version", sa.Text(), nullable=False),
        sa.Column("assessment_method", sa.Text(), nullable=False),
        sa.Column("review_status", sa.Text(), nullable=False),
        sa.Column("reviewer", sa.Text(), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('verified_exact_mapping', 'strong_candidate', "
            "'likely_equivalent', 'partial_equivalence', 'related_not_equivalent', "
            "'vendor_specific', 'needs_human_review', 'needs_research', "
            "'insufficient_evidence', 'conflict')",
            name="ck_canonical_backlog_assessment_outcome",
        ),
        sa.CheckConstraint(
            "confidence_basis_points BETWEEN 0 AND 10000",
            name="ck_canonical_backlog_assessment_confidence",
        ),
        sa.CheckConstraint(
            "assessment_method IN ('deterministic_triage', 'human_review', "
            "'external_research')",
            name="ck_canonical_backlog_assessment_method",
        ),
        sa.CheckConstraint(
            "review_status IN ('system_assessed', 'human_confirmed')",
            name="ck_canonical_backlog_assessment_review_status",
        ),
    )
    op.create_index(
        "ix_canonical_backlog_assessments_outcome",
        "canonical_backlog_assessments",
        ["outcome"],
    )
    op.create_index(
        "ix_canonical_backlog_assessments_candidate",
        "canonical_backlog_assessments",
        ["candidate_canonical_id"],
    )
    op.create_index(
        "ix_canonical_backlog_assessments_review_status",
        "canonical_backlog_assessments",
        ["review_status"],
    )

    op.create_table(
        "canonical_backlog_assessment_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "setting_id",
            sa.Integer(),
            sa.ForeignKey("canonical_backlog_assessments.setting_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_canonical_backlog_assessment_events_setting_id",
        "canonical_backlog_assessment_events",
        ["setting_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_canonical_backlog_assessment_events_setting_id",
        table_name="canonical_backlog_assessment_events",
    )
    op.drop_table("canonical_backlog_assessment_events")
    op.drop_index(
        "ix_canonical_backlog_assessments_review_status",
        table_name="canonical_backlog_assessments",
    )
    op.drop_index(
        "ix_canonical_backlog_assessments_candidate",
        table_name="canonical_backlog_assessments",
    )
    op.drop_index(
        "ix_canonical_backlog_assessments_outcome",
        table_name="canonical_backlog_assessments",
    )
    op.drop_table("canonical_backlog_assessments")
