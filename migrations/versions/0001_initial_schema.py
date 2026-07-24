"""Initial schema: manuals, extraction_runs, settings, review_events, settings_fts.

Mirrors Implementation Plan section 9.

Revision ID: 0001
Revises:
Create Date: 2026-07-10

"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "manuals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("stored_filename", sa.Text(), nullable=False, unique=True),
        sa.Column("sha256", sa.Text(), nullable=False, unique=True),
        sa.Column("file_size_bytes", sa.Integer(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("vendor", sa.Text(), nullable=True),
        sa.Column("model_name", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
    )

    op.create_table(
        "extraction_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "manual_id",
            sa.Integer(),
            sa.ForeignKey("manuals.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("pages_processed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("settings_found", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("parser_version", sa.Text(), nullable=False),
        sa.Column("started_at", sa.Text(), nullable=True),
        sa.Column("finished_at", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
    )

    op.create_table(
        "settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "manual_id",
            sa.Integer(),
            sa.ForeignKey("manuals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "extraction_run_id",
            sa.Integer(),
            sa.ForeignKey("extraction_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("extracted_name", sa.Text(), nullable=False),
        sa.Column("extracted_description", sa.Text(), nullable=False),
        sa.Column("edited_name", sa.Text(), nullable=True),
        sa.Column("edited_description", sa.Text(), nullable=True),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="draft"),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
    )
    op.create_index("ix_settings_status", "settings", ["status"])
    op.create_index("ix_settings_manual_id_status", "settings", ["manual_id", "status"])
    op.create_index("ix_settings_extraction_run_id", "settings", ["extraction_run_id"])

    op.create_table(
        "review_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "setting_id",
            sa.Integer(),
            sa.ForeignKey("settings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index("ix_review_events_setting_id", "review_events", ["setting_id"])

    # Search index over approved settings; rowid carries setting_id.
    op.execute("CREATE VIRTUAL TABLE settings_fts USING fts5(name, description)")


def downgrade() -> None:
    op.execute("DROP TABLE settings_fts")
    op.drop_table("review_events")
    op.drop_table("settings")
    op.drop_table("extraction_runs")
    op.drop_table("manuals")
