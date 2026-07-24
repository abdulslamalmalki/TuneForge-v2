"""Add the reviewed ASUS AM5 pilot motherboard identity.

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-19
"""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

MODEL_ID = "asus-tuf-gaming-x870-plus-wifi"


def upgrade() -> None:
    connection = op.get_bind()
    exists = connection.scalar(
        sa.text("SELECT 1 FROM motherboard_models WHERE model_id = :model_id"),
        {"model_id": MODEL_ID},
    )
    if exists:
        return
    connection.execute(
        sa.text(
            "INSERT INTO motherboard_models "
            "(model_id, vendor_id, canonical_name, chipset_id, platform_id) "
            "VALUES (:model_id, :vendor_id, :canonical_name, :chipset_id, :platform_id)"
        ),
        {
            "model_id": MODEL_ID,
            "vendor_id": "asus",
            "canonical_name": "TUF GAMING X870-PLUS WIFI",
            "chipset_id": "amd-x870",
            "platform_id": "amd-am5",
        },
    )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text("DELETE FROM motherboard_models WHERE model_id = :model_id"),
        {"model_id": MODEL_ID},
    )
