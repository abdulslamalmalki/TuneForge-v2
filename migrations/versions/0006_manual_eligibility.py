"""Add reviewer-gated manual eligibility fields.

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("manuals", sa.Column("document_type", sa.Text(), nullable=True))
    op.add_column(
        "manuals",
        sa.Column(
            "eligibility_status",
            sa.Text(),
            nullable=False,
            server_default="pending",
        ),
    )
    op.add_column("manuals", sa.Column("eligibility_confidence", sa.Integer(), nullable=True))
    op.add_column("manuals", sa.Column("eligibility_reasons", sa.Text(), nullable=True))
    op.add_column("manuals", sa.Column("eligibility_reviewed_at", sa.Text(), nullable=True))
    op.add_column("manuals", sa.Column("eligibility_reviewer", sa.Text(), nullable=True))
    op.add_column("manuals", sa.Column("eligibility_note", sa.Text(), nullable=True))
    # Existing rows have already entered extraction and cannot be retroactively
    # gated. New rows are explicitly created as pending by the domain service.
    op.execute("UPDATE manuals SET eligibility_status = 'legacy'")


def downgrade() -> None:
    with op.batch_alter_table("manuals") as batch_op:
        batch_op.drop_column("eligibility_note")
        batch_op.drop_column("eligibility_reviewer")
        batch_op.drop_column("eligibility_reviewed_at")
        batch_op.drop_column("eligibility_reasons")
        batch_op.drop_column("eligibility_confidence")
        batch_op.drop_column("eligibility_status")
        batch_op.drop_column("document_type")
