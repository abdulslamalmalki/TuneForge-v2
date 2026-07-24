"""Add curator accounts, server-side sessions, and append-only auth events.

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-19
"""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.Text(), nullable=False, unique=True),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("last_login_at", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "role IN ('administrator', 'reviewer')", name="ck_user_account_role"
        ),
        sa.CheckConstraint(
            "status IN ('active', 'disabled')", name="ck_user_account_status"
        ),
    )
    op.create_index("ix_user_accounts_status", "user_accounts", ["status"])
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("user_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=False),
        sa.Column("last_seen_at", sa.Text(), nullable=False),
        sa.Column("revoked_at", sa.Text(), nullable=True),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])
    op.create_table(
        "auth_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column(
            "subject_user_id",
            sa.Integer(),
            sa.ForeignKey("user_accounts.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index("ix_auth_events_created_at", "auth_events", ["created_at"])
    op.create_index(
        "ix_auth_events_subject_user_id", "auth_events", ["subject_user_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_auth_events_subject_user_id", table_name="auth_events")
    op.drop_index("ix_auth_events_created_at", table_name="auth_events")
    op.drop_table("auth_events")
    op.drop_index("ix_auth_sessions_expires_at", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_user_id", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_index("ix_user_accounts_status", table_name="user_accounts")
    op.drop_table("user_accounts")
