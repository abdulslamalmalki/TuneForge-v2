"""Add trusted evidence source intake and immutable passages.

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-17
"""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evidence_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("publisher_id", sa.Text(), nullable=False),
        sa.Column("publisher_name", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("trust_tier", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("document_id", sa.Text(), nullable=True),
        sa.Column("document_version", sa.Text(), nullable=False),
        sa.Column("published_on", sa.Text(), nullable=False),
        sa.Column("retrieved_at", sa.Text(), nullable=False),
        sa.Column("language", sa.Text(), nullable=False),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("stored_filename", sa.Text(), nullable=False, unique=True),
        sa.Column("sha256", sa.Text(), nullable=False, unique=True),
        sa.Column("file_size_bytes", sa.Integer(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("reviewer", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.Text(), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "source_type IN ('official_document')",
            name="ck_evidence_source_type",
        ),
        sa.CheckConstraint(
            "trust_tier IN ('primary_official')",
            name="ck_evidence_source_trust_tier",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="ck_evidence_source_status",
        ),
    )
    op.create_index("ix_evidence_sources_status", "evidence_sources", ["status"])
    op.create_index("ix_evidence_sources_publisher_id", "evidence_sources", ["publisher_id"])
    op.create_table(
        "evidence_extraction_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "source_id",
            sa.Integer(),
            sa.ForeignKey("evidence_sources.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("extractor_version", sa.Text(), nullable=True),
        sa.Column("pages_processed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("passages_found", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.Text(), nullable=True),
        sa.Column("finished_at", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed')",
            name="ck_evidence_extraction_run_status",
        ),
    )
    op.create_table(
        "evidence_passages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "source_id",
            sa.Integer(),
            sa.ForeignKey("evidence_sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "extraction_run_id",
            sa.Integer(),
            sa.ForeignKey("evidence_extraction_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("section_heading", sa.Text(), nullable=True),
        sa.Column("passage_text", sa.Text(), nullable=False),
        sa.Column("text_sha256", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index(
        "ux_evidence_passages_source_ordinal",
        "evidence_passages",
        ["source_id", "ordinal"],
        unique=True,
    )
    op.create_index(
        "ix_evidence_passages_source_page",
        "evidence_passages",
        ["source_id", "page_number"],
    )
    op.create_index(
        "ix_evidence_passages_extraction_run_id",
        "evidence_passages",
        ["extraction_run_id"],
    )
    op.create_table(
        "evidence_source_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "source_id",
            sa.Integer(),
            sa.ForeignKey("evidence_sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_evidence_source_events_source_id",
        "evidence_source_events",
        ["source_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_evidence_source_events_source_id", table_name="evidence_source_events")
    op.drop_table("evidence_source_events")
    op.drop_index("ix_evidence_passages_extraction_run_id", table_name="evidence_passages")
    op.drop_index("ix_evidence_passages_source_page", table_name="evidence_passages")
    op.drop_index("ux_evidence_passages_source_ordinal", table_name="evidence_passages")
    op.drop_table("evidence_passages")
    op.drop_table("evidence_extraction_runs")
    op.drop_index("ix_evidence_sources_publisher_id", table_name="evidence_sources")
    op.drop_index("ix_evidence_sources_status", table_name="evidence_sources")
    op.drop_table("evidence_sources")
