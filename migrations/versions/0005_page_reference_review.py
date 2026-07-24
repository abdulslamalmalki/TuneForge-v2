"""Add explicit review state for extracted PDF page references.

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("settings", sa.Column("edited_page_number", sa.Integer(), nullable=True))
    op.add_column(
        "settings",
        sa.Column(
            "page_review_status",
            sa.Text(),
            nullable=False,
            server_default="pending",
        ),
    )


def downgrade() -> None:
    with op.batch_alter_table("settings") as batch_op:
        batch_op.drop_column("page_review_status")
        batch_op.drop_column("edited_page_number")
