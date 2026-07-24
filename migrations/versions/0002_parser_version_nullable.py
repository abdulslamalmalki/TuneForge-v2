"""Make extraction_runs.parser_version nullable.

A pending run has not been processed, so no parser version exists until
parsing occurs; NULL is the correct pre-processing value (approved
architectural decision superseding the NOT NULL in plan section 9).

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-10

"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("extraction_runs") as batch_op:
        batch_op.alter_column("parser_version", existing_type=sa.Text(), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("extraction_runs") as batch_op:
        batch_op.alter_column("parser_version", existing_type=sa.Text(), nullable=False)
