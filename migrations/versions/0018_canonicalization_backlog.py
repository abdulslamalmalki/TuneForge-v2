"""Add conservative canonicalization evidence and automatic mapping provenance.

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-23
"""

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("setting_canonical_mappings") as batch_op:
        batch_op.drop_constraint(
            "ck_setting_canonical_mapping_method",
            type_="check",
        )
        batch_op.create_check_constraint(
            "ck_setting_canonical_mapping_method",
            "mapping_method IN ('reviewer', 'seed', 'automatic')",
        )

    with op.batch_alter_table("canonical_mapping_suggestions") as batch_op:
        batch_op.drop_constraint(
            "ck_canonical_mapping_suggestion_match_type",
            type_="check",
        )
        batch_op.add_column(
            sa.Column(
                "confidence_basis_points",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column(
                "evidence_json",
                sa.Text(),
                nullable=False,
                server_default="{}",
            )
        )
        batch_op.add_column(
            sa.Column(
                "engine_version",
                sa.Text(),
                nullable=False,
                server_default="legacy",
            )
        )
        batch_op.create_check_constraint(
            "ck_canonical_mapping_suggestion_match_type",
            "match_type IN ('exact_alias', 'contextual_evidence')",
        )
        batch_op.create_check_constraint(
            "ck_canonical_mapping_suggestion_confidence",
            "confidence_basis_points BETWEEN 0 AND 10000",
        )


def downgrade() -> None:
    op.execute(
        "DELETE FROM canonical_mapping_suggestions "
        "WHERE match_type = 'contextual_evidence'"
    )
    op.execute(
        "DELETE FROM setting_canonical_mappings "
        "WHERE mapping_method = 'automatic'"
    )
    with op.batch_alter_table("canonical_mapping_suggestions") as batch_op:
        batch_op.drop_constraint(
            "ck_canonical_mapping_suggestion_confidence",
            type_="check",
        )
        batch_op.drop_constraint(
            "ck_canonical_mapping_suggestion_match_type",
            type_="check",
        )
        batch_op.create_check_constraint(
            "ck_canonical_mapping_suggestion_match_type",
            "match_type IN ('exact_alias')",
        )
        batch_op.drop_column("engine_version")
        batch_op.drop_column("evidence_json")
        batch_op.drop_column("confidence_basis_points")

    with op.batch_alter_table("setting_canonical_mappings") as batch_op:
        batch_op.drop_constraint(
            "ck_setting_canonical_mapping_method",
            type_="check",
        )
        batch_op.create_check_constraint(
            "ck_setting_canonical_mapping_method",
            "mapping_method IN ('reviewer', 'seed')",
        )
