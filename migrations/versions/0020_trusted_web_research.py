"""Add append-audited trusted-web research snapshots.

Revision ID: 0020
Revises: 0019
Create Date: 2026-08-14
"""

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trusted_web_research_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("normalized_query", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("policy_version", sa.Text(), nullable=False),
        sa.Column("result_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("finished_at", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'refused', 'failed')",
            name="ck_trusted_web_research_run_status",
        ),
        sa.CheckConstraint(
            "length(query_text) BETWEEN 1 AND 300",
            name="ck_trusted_web_research_query_length",
        ),
    )
    op.create_index(
        "ix_trusted_web_research_runs_created_at", "trusted_web_research_runs", ["created_at"]
    )
    op.create_index(
        "ix_trusted_web_research_runs_status", "trusted_web_research_runs", ["status"]
    )
    op.create_table(
        "trusted_web_research_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("trusted_web_research_runs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("publisher_id", sa.Text(), nullable=False),
        sa.Column("publisher_name", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("evidence_kind", sa.Text(), nullable=False),
        sa.Column("excerpt_text", sa.Text(), nullable=True),
        sa.Column("content_sha256", sa.Text(), nullable=True),
        sa.Column("retrieved_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "evidence_kind IN ('official_page_excerpt', 'official_link_only')",
            name="ck_trusted_web_research_result_kind",
        ),
    )
    op.create_index(
        "ux_trusted_web_research_results_run_ordinal",
        "trusted_web_research_results",
        ["run_id", "ordinal"],
        unique=True,
    )
    op.create_index(
        "ix_trusted_web_research_results_run_id",
        "trusted_web_research_results",
        ["run_id"],
    )
    op.create_index(
        "ix_trusted_web_research_results_publisher_id",
        "trusted_web_research_results",
        ["publisher_id"],
    )
    op.create_table(
        "trusted_web_research_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("trusted_web_research_runs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_trusted_web_research_events_run_id",
        "trusted_web_research_events",
        ["run_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_trusted_web_research_events_run_id", table_name="trusted_web_research_events"
    )
    op.drop_table("trusted_web_research_events")
    op.drop_index(
        "ix_trusted_web_research_results_publisher_id",
        table_name="trusted_web_research_results",
    )
    op.drop_index(
        "ix_trusted_web_research_results_run_id",
        table_name="trusted_web_research_results",
    )
    op.drop_index(
        "ux_trusted_web_research_results_run_ordinal",
        table_name="trusted_web_research_results",
    )
    op.drop_table("trusted_web_research_results")
    op.drop_index(
        "ix_trusted_web_research_runs_status", table_name="trusted_web_research_runs"
    )
    op.drop_index(
        "ix_trusted_web_research_runs_created_at", table_name="trusted_web_research_runs"
    )
    op.drop_table("trusted_web_research_runs")
