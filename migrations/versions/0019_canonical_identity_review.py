"""Add reviewed menu context and canonical identity proposal lifecycle.

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-24
"""

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("canonical_settings") as batch_op:
        batch_op.add_column(
            sa.Column(
                "status",
                sa.Text(),
                nullable=False,
                server_default="active",
            )
        )
        batch_op.create_check_constraint(
            "ck_canonical_setting_status",
            "status IN ('active', 'deprecated')",
        )
        batch_op.create_index("ix_canonical_settings_status", ["status"])

    op.create_table(
        "canonical_identity_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "canonical_id",
            sa.Text(),
            sa.ForeignKey("canonical_settings.canonical_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_canonical_identity_events_canonical_id",
        "canonical_identity_events",
        ["canonical_id"],
    )
    op.get_bind().exec_driver_sql(
        """
        INSERT INTO canonical_identity_events
            (canonical_id, action, actor, detail, created_at)
        SELECT
            canonical_id,
            'created',
            'migration-0019',
            '{"after":{"status":"active"},"before":null,"source":"migration-0019"}',
            created_at
        FROM canonical_settings
        """
    )

    op.create_table(
        "setting_menu_context_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "setting_id",
            sa.Integer(),
            sa.ForeignKey("settings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_setting_menu_context_events_setting_id",
        "setting_menu_context_events",
        ["setting_id"],
    )
    op.create_table(
        "setting_menu_contexts",
        sa.Column(
            "setting_id",
            sa.Integer(),
            sa.ForeignKey("settings.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("menu_path", sa.Text(), nullable=False),
        sa.Column("normalized_menu_path", sa.Text(), nullable=False),
        sa.Column("source_page_number", sa.Integer(), nullable=False),
        sa.Column("evidence_excerpt", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "current_event_id",
            sa.Integer(),
            sa.ForeignKey("setting_menu_context_events.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("reviewed_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'revoked')",
            name="ck_setting_menu_context_status",
        ),
    )
    op.create_index(
        "ix_setting_menu_contexts_status",
        "setting_menu_contexts",
        ["status"],
    )
    op.create_table(
        "setting_menu_context_citations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "setting_id",
            sa.Integer(),
            sa.ForeignKey("settings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("setting_menu_context_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("evidence_excerpt", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index(
        "ux_setting_menu_context_citations_event_page",
        "setting_menu_context_citations",
        ["event_id", "page_number"],
        unique=True,
    )
    op.create_index(
        "ix_setting_menu_context_citations_setting_id",
        "setting_menu_context_citations",
        ["setting_id"],
    )

    op.create_table(
        "canonical_identity_proposals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("canonical_id", sa.Text(), nullable=False),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("confidence_basis_points", sa.Integer(), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=False),
        sa.Column("engine_version", sa.Text(), nullable=False),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("actor", sa.Text(), nullable=True),
        sa.Column(
            "approved_canonical_id",
            sa.Text(),
            sa.ForeignKey("canonical_settings.canonical_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("reviewed_at", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="ck_canonical_identity_proposal_status",
        ),
        sa.CheckConstraint(
            "confidence_basis_points BETWEEN 0 AND 10000",
            name="ck_canonical_identity_proposal_confidence",
        ),
    )
    op.create_index(
        "ux_canonical_identity_proposals_canonical_id",
        "canonical_identity_proposals",
        ["canonical_id"],
        unique=True,
    )
    op.create_index(
        "ix_canonical_identity_proposals_status",
        "canonical_identity_proposals",
        ["status"],
    )
    op.create_table(
        "canonical_identity_proposal_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "proposal_id",
            sa.Integer(),
            sa.ForeignKey("canonical_identity_proposals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "setting_id",
            sa.Integer(),
            sa.ForeignKey("settings.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index(
        "ux_canonical_identity_proposal_settings",
        "canonical_identity_proposal_settings",
        ["proposal_id", "setting_id"],
        unique=True,
    )
    op.create_index(
        "ix_canonical_identity_proposal_settings_setting_id",
        "canonical_identity_proposal_settings",
        ["setting_id"],
    )
    op.create_table(
        "canonical_identity_proposal_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "proposal_id",
            sa.Integer(),
            sa.ForeignKey("canonical_identity_proposals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_canonical_identity_proposal_events_proposal_id",
        "canonical_identity_proposal_events",
        ["proposal_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_canonical_identity_proposal_events_proposal_id",
        table_name="canonical_identity_proposal_events",
    )
    op.drop_table("canonical_identity_proposal_events")
    op.drop_index(
        "ix_canonical_identity_proposal_settings_setting_id",
        table_name="canonical_identity_proposal_settings",
    )
    op.drop_index(
        "ux_canonical_identity_proposal_settings",
        table_name="canonical_identity_proposal_settings",
    )
    op.drop_table("canonical_identity_proposal_settings")
    op.drop_index(
        "ix_canonical_identity_proposals_status",
        table_name="canonical_identity_proposals",
    )
    op.drop_index(
        "ux_canonical_identity_proposals_canonical_id",
        table_name="canonical_identity_proposals",
    )
    op.drop_table("canonical_identity_proposals")
    op.drop_index(
        "ix_setting_menu_context_citations_setting_id",
        table_name="setting_menu_context_citations",
    )
    op.drop_index(
        "ux_setting_menu_context_citations_event_page",
        table_name="setting_menu_context_citations",
    )
    op.drop_table("setting_menu_context_citations")
    op.drop_index(
        "ix_setting_menu_contexts_status",
        table_name="setting_menu_contexts",
    )
    op.drop_table("setting_menu_contexts")
    op.drop_index(
        "ix_setting_menu_context_events_setting_id",
        table_name="setting_menu_context_events",
    )
    op.drop_table("setting_menu_context_events")
    op.drop_index(
        "ix_canonical_identity_events_canonical_id",
        table_name="canonical_identity_events",
    )
    op.drop_table("canonical_identity_events")

    with op.batch_alter_table("canonical_settings") as batch_op:
        batch_op.drop_index("ix_canonical_settings_status")
        batch_op.drop_constraint("ck_canonical_setting_status", type_="check")
        batch_op.drop_column("status")
