"""Add reviewed BIOS-version applicability to guidance claims.

Revision ID: 0025
Revises: 0024
Create Date: 2026-08-14
"""

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "guidance_claim_bios_scopes",
        sa.Column(
            "claim_id",
            sa.Integer(),
            sa.ForeignKey("guidance_claims.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("ordinal", sa.Integer(), primary_key=True),
        sa.Column(
            "motherboard_model_id",
            sa.Text(),
            sa.ForeignKey("motherboard_models.model_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "vendor_id",
            sa.Text(),
            sa.ForeignKey("vendors.vendor_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("bios_version_exact", sa.Text(), nullable=True),
        sa.Column("valid_from", sa.Text(), nullable=True),
        sa.Column("valid_until", sa.Text(), nullable=True),
        sa.Column("introduced_in", sa.Text(), nullable=True),
        sa.Column("changed_in", sa.Text(), nullable=True),
        sa.Column("deprecated_in", sa.Text(), nullable=True),
        sa.Column("comparison_mode", sa.Text(), nullable=False),
        sa.Column("version_notes", sa.Text(), nullable=False),
        sa.Column("evidence_basis", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "(motherboard_model_id IS NOT NULL) + (vendor_id IS NOT NULL) = 1",
            name="ck_guidance_bios_scope_exactly_one_context",
        ),
        sa.CheckConstraint(
            "bios_version_exact IS NOT NULL OR valid_from IS NOT NULL OR "
            "valid_until IS NOT NULL OR introduced_in IS NOT NULL OR "
            "changed_in IS NOT NULL OR deprecated_in IS NOT NULL",
            name="ck_guidance_bios_scope_has_version",
        ),
        sa.CheckConstraint(
            "NOT (bios_version_exact IS NOT NULL AND "
            "(valid_from IS NOT NULL OR valid_until IS NOT NULL))",
            name="ck_guidance_bios_scope_exact_or_range",
        ),
        sa.CheckConstraint(
            "comparison_mode IN ('opaque', 'integer_numeric', 'dotted_numeric')",
            name="ck_guidance_bios_scope_comparison_mode",
        ),
        sa.CheckConstraint(
            "comparison_mode != 'opaque' OR "
            "(valid_from IS NULL AND valid_until IS NULL)",
            name="ck_guidance_bios_scope_opaque_not_ordered",
        ),
        sa.CheckConstraint(
            "length(version_notes) BETWEEN 1 AND 1000",
            name="ck_guidance_bios_scope_notes_length",
        ),
        sa.CheckConstraint(
            "length(evidence_basis) BETWEEN 1 AND 1000",
            name="ck_guidance_bios_scope_basis_length",
        ),
    )
    op.create_index(
        "ix_guidance_claim_bios_scopes_model",
        "guidance_claim_bios_scopes",
        ["motherboard_model_id"],
    )
    op.create_index(
        "ix_guidance_claim_bios_scopes_vendor",
        "guidance_claim_bios_scopes",
        ["vendor_id"],
    )
    op.create_index(
        "ix_guidance_claim_bios_scopes_exact",
        "guidance_claim_bios_scopes",
        ["bios_version_exact"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_guidance_claim_bios_scopes_exact",
        table_name="guidance_claim_bios_scopes",
    )
    op.drop_index(
        "ix_guidance_claim_bios_scopes_vendor",
        table_name="guidance_claim_bios_scopes",
    )
    op.drop_index(
        "ix_guidance_claim_bios_scopes_model",
        table_name="guidance_claim_bios_scopes",
    )
    op.drop_table("guidance_claim_bios_scopes")
