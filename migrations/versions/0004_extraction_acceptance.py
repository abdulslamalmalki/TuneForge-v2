"""Add curator-recorded missed settings for extraction acceptance metrics.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("manuals", sa.Column("acceptance_reviewed_at", sa.Text(), nullable=True))
    op.add_column("manuals", sa.Column("acceptance_reviewer", sa.Text(), nullable=True))
    op.add_column("manuals", sa.Column("acceptance_notes", sa.Text(), nullable=True))
    op.create_table(
        "missed_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "manual_id",
            sa.Integer(),
            sa.ForeignKey("manuals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("dismissed_at", sa.Text(), nullable=True),
        sa.Column("dismissal_reason", sa.Text(), nullable=True),
        sa.CheckConstraint("page_number > 0", name="ck_missed_settings_page_positive"),
        sa.CheckConstraint("status IN ('active', 'dismissed')", name="ck_missed_settings_status"),
    )
    op.create_index("ix_missed_settings_manual_id", "missed_settings", ["manual_id"])
    op.create_index("ix_missed_settings_manual_status", "missed_settings", ["manual_id", "status"])


def downgrade() -> None:
    op.drop_table("missed_settings")
    with op.batch_alter_table("manuals") as batch_op:
        batch_op.drop_column("acceptance_notes")
        batch_op.drop_column("acceptance_reviewer")
        batch_op.drop_column("acceptance_reviewed_at")
