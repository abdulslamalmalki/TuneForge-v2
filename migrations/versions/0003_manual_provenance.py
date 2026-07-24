"""Add source provenance metadata and its append-only audit trail.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("manuals", sa.Column("document_title", sa.Text(), nullable=True))
    op.add_column("manuals", sa.Column("source_url", sa.Text(), nullable=True))
    op.add_column("manuals", sa.Column("document_version", sa.Text(), nullable=True))
    op.add_column("manuals", sa.Column("published_on", sa.Text(), nullable=True))
    op.add_column("manuals", sa.Column("retrieved_at", sa.Text(), nullable=True))
    op.add_column("manuals", sa.Column("language", sa.Text(), nullable=True))
    op.add_column("manuals", sa.Column("board_revision", sa.Text(), nullable=True))
    op.add_column("manuals", sa.Column("chipset", sa.Text(), nullable=True))

    # Preserve existing installations without inventing provenance. The file
    # name is the best available title; retrieval time is the original upload
    # time. Source URL and hardware context stay NULL until a curator backfills
    # them through the application.
    op.execute("UPDATE manuals SET document_title = original_filename")
    op.execute("UPDATE manuals SET retrieved_at = created_at")
    op.execute("UPDATE manuals SET language = 'en'")

    with op.batch_alter_table("manuals") as batch_op:
        batch_op.alter_column("document_title", existing_type=sa.Text(), nullable=False)
        batch_op.alter_column("retrieved_at", existing_type=sa.Text(), nullable=False)
        batch_op.alter_column("language", existing_type=sa.Text(), nullable=False)

    op.create_table(
        "manual_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "manual_id",
            sa.Integer(),
            sa.ForeignKey("manuals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index("ix_manual_events_manual_id", "manual_events", ["manual_id"])


def downgrade() -> None:
    op.drop_table("manual_events")
    with op.batch_alter_table("manuals") as batch_op:
        batch_op.drop_column("chipset")
        batch_op.drop_column("board_revision")
        batch_op.drop_column("language")
        batch_op.drop_column("retrieved_at")
        batch_op.drop_column("published_on")
        batch_op.drop_column("document_version")
        batch_op.drop_column("source_url")
        batch_op.drop_column("document_title")
