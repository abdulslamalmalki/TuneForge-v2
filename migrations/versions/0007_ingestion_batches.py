"""Add persistent ingestion batches and isolated batch items.

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ingestion_batches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("total_count", sa.Integer(), nullable=False),
        sa.Column("accepted_count", sa.Integer(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("finished_at", sa.Text(), nullable=True),
    )
    op.create_table(
        "ingestion_batch_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "batch_id",
            sa.Integer(),
            sa.ForeignKey("ingestion_batches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "manual_id",
            sa.Integer(),
            sa.ForeignKey("manuals.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("intake_status", sa.Text(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index("ix_ingestion_batch_items_batch_id", "ingestion_batch_items", ["batch_id"])
    op.create_index("ix_ingestion_batch_items_manual_id", "ingestion_batch_items", ["manual_id"])


def downgrade() -> None:
    op.drop_table("ingestion_batch_items")
    op.drop_table("ingestion_batches")
