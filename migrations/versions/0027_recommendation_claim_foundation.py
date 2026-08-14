"""Add the reviewed recommendation claim foundation.

Revision ID: 0027
Revises: 0026
Create Date: 2026-08-14
"""

import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recommendation_claims",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "canonical_id",
            sa.Text(),
            sa.ForeignKey("canonical_settings.canonical_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("recommendation_text", sa.Text(), nullable=False),
        sa.Column("recommended_action", sa.Text(), nullable=False),
        sa.Column("value_mode", sa.Text(), nullable=False),
        sa.Column("exact_value", sa.Text(), nullable=True),
        sa.Column("minimum_value", sa.Text(), nullable=True),
        sa.Column("maximum_value", sa.Text(), nullable=True),
        sa.Column("value_unit", sa.Text(), nullable=True),
        sa.Column("use_case", sa.Text(), nullable=False),
        sa.Column("risk_level", sa.Text(), nullable=False),
        sa.Column("prerequisites_json", sa.Text(), nullable=False),
        sa.Column("confidence_level", sa.Text(), nullable=False),
        sa.Column("conflict_state", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("reviewer", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "recommended_action IN ('enable', 'disable', 'set_value', 'set_range', "
            "'leave_auto', 'avoid', 'informational')",
            name="ck_recommendation_claim_action",
        ),
        sa.CheckConstraint(
            "value_mode IN ('none', 'exact', 'range')",
            name="ck_recommendation_claim_value_mode",
        ),
        sa.CheckConstraint(
            "(recommended_action = 'set_value' AND value_mode = 'exact') OR "
            "(recommended_action = 'set_range' AND value_mode = 'range') OR "
            "(recommended_action NOT IN ('set_value', 'set_range') AND value_mode = 'none')",
            name="ck_recommendation_claim_action_value_mode",
        ),
        sa.CheckConstraint(
            "(value_mode = 'none' AND exact_value IS NULL AND minimum_value IS NULL "
            "AND maximum_value IS NULL AND value_unit IS NULL) OR "
            "(value_mode = 'exact' AND exact_value IS NOT NULL AND minimum_value IS NULL "
            "AND maximum_value IS NULL) OR "
            "(value_mode = 'range' AND exact_value IS NULL AND minimum_value IS NOT NULL "
            "AND maximum_value IS NOT NULL)",
            name="ck_recommendation_claim_value_shape",
        ),
        sa.CheckConstraint(
            "use_case IN ('gaming', 'productivity', 'efficiency', 'quiet_operation', "
            "'overclocking', 'undervolting', 'memory_tuning', "
            "'stability_troubleshooting')",
            name="ck_recommendation_claim_use_case",
        ),
        sa.CheckConstraint(
            "risk_level IN ('informational', 'low', 'moderate', 'advanced', 'high')",
            name="ck_recommendation_claim_risk",
        ),
        sa.CheckConstraint(
            "confidence_level IN ('insufficient', 'moderate', 'high', 'very_high')",
            name="ck_recommendation_claim_confidence",
        ),
        sa.CheckConstraint(
            "conflict_state IN ('not_assessed', 'none', 'potential', 'confirmed', 'resolved')",
            name="ck_recommendation_claim_conflict",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'approved', 'rejected')",
            name="ck_recommendation_claim_status",
        ),
    )
    op.create_index(
        "ix_recommendation_claims_canonical_id",
        "recommendation_claims",
        ["canonical_id"],
    )
    op.create_index(
        "ix_recommendation_claims_status",
        "recommendation_claims",
        ["status"],
    )
    op.create_index(
        "ix_recommendation_claims_use_case",
        "recommendation_claims",
        ["use_case"],
    )
    op.create_index(
        "ix_recommendation_claims_risk_level",
        "recommendation_claims",
        ["risk_level"],
    )

    op.create_table(
        "recommendation_claim_evidence",
        sa.Column(
            "claim_id",
            sa.Integer(),
            sa.ForeignKey("recommendation_claims.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "passage_id",
            sa.Integer(),
            sa.ForeignKey("evidence_passages.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("relation", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "relation = 'supports'",
            name="ck_recommendation_evidence_relation",
        ),
    )
    op.create_index(
        "ix_recommendation_claim_evidence_passage_id",
        "recommendation_claim_evidence",
        ["passage_id"],
    )
    op.create_table(
        "recommendation_claim_source_settings",
        sa.Column(
            "claim_id",
            sa.Integer(),
            sa.ForeignKey("recommendation_claims.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "setting_id",
            sa.Integer(),
            sa.ForeignKey("settings.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("relation", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "relation = 'supports'",
            name="ck_recommendation_source_setting_relation",
        ),
    )
    op.create_index(
        "ix_recommendation_claim_source_settings_setting_id",
        "recommendation_claim_source_settings",
        ["setting_id"],
    )

    op.create_table(
        "recommendation_claim_applicability_targets",
        sa.Column(
            "claim_id",
            sa.Integer(),
            sa.ForeignKey("recommendation_claims.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("ordinal", sa.Integer(), primary_key=True),
        sa.Column(
            "cpu_model_id",
            sa.Text(),
            sa.ForeignKey("cpu_models.cpu_model_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "cpu_family_id",
            sa.Text(),
            sa.ForeignKey("cpu_families.family_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "cpu_architecture_id",
            sa.Text(),
            sa.ForeignKey("cpu_architectures.architecture_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "motherboard_model_id",
            sa.Text(),
            sa.ForeignKey("motherboard_models.model_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "chipset_id",
            sa.Text(),
            sa.ForeignKey("chipsets.chipset_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "chipset_family_id",
            sa.Text(),
            sa.ForeignKey("chipset_families.family_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "platform_id",
            sa.Text(),
            sa.ForeignKey("hardware_platforms.platform_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "vendor_id",
            sa.Text(),
            sa.ForeignKey("vendors.vendor_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "cpu_vendor_id",
            sa.Text(),
            sa.ForeignKey("cpu_vendors.vendor_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "motherboard_family_id",
            sa.Text(),
            sa.ForeignKey("motherboard_families.family_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("evidence_level", sa.Text(), nullable=False),
        sa.Column("evidence_basis", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "(cpu_model_id IS NOT NULL) + (cpu_family_id IS NOT NULL) + "
            "(cpu_architecture_id IS NOT NULL) + (motherboard_model_id IS NOT NULL) + "
            "(chipset_id IS NOT NULL) + (chipset_family_id IS NOT NULL) + "
            "(platform_id IS NOT NULL) + (vendor_id IS NOT NULL) + "
            "(cpu_vendor_id IS NOT NULL) + (motherboard_family_id IS NOT NULL) = 1",
            name="ck_recommendation_applicability_exactly_one_target",
        ),
        sa.CheckConstraint(
            "evidence_level IN ('exact_model', 'exact_cpu', 'exact_chipset', "
            "'family', 'architecture', 'platform', 'vendor')",
            name="ck_recommendation_applicability_evidence_level",
        ),
    )
    op.create_table(
        "recommendation_claim_bios_scopes",
        sa.Column(
            "claim_id",
            sa.Integer(),
            sa.ForeignKey("recommendation_claims.id", ondelete="CASCADE"),
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
            name="ck_recommendation_bios_scope_exactly_one_context",
        ),
        sa.CheckConstraint(
            "bios_version_exact IS NOT NULL OR valid_from IS NOT NULL OR "
            "valid_until IS NOT NULL OR introduced_in IS NOT NULL OR "
            "changed_in IS NOT NULL OR deprecated_in IS NOT NULL",
            name="ck_recommendation_bios_scope_has_version",
        ),
        sa.CheckConstraint(
            "NOT (bios_version_exact IS NOT NULL AND "
            "(valid_from IS NOT NULL OR valid_until IS NOT NULL))",
            name="ck_recommendation_bios_scope_exact_or_range",
        ),
        sa.CheckConstraint(
            "comparison_mode IN ('opaque', 'integer_numeric', 'dotted_numeric')",
            name="ck_recommendation_bios_scope_comparison_mode",
        ),
        sa.CheckConstraint(
            "comparison_mode != 'opaque' OR "
            "(valid_from IS NULL AND valid_until IS NULL)",
            name="ck_recommendation_bios_scope_opaque_not_ordered",
        ),
        sa.CheckConstraint(
            "length(version_notes) BETWEEN 1 AND 1000",
            name="ck_recommendation_bios_scope_notes_length",
        ),
        sa.CheckConstraint(
            "length(evidence_basis) BETWEEN 1 AND 1000",
            name="ck_recommendation_bios_scope_basis_length",
        ),
    )
    op.create_index(
        "ix_recommendation_bios_scopes_model",
        "recommendation_claim_bios_scopes",
        ["motherboard_model_id"],
    )
    op.create_index(
        "ix_recommendation_bios_scopes_vendor",
        "recommendation_claim_bios_scopes",
        ["vendor_id"],
    )
    op.create_index(
        "ix_recommendation_bios_scopes_exact",
        "recommendation_claim_bios_scopes",
        ["bios_version_exact"],
    )

    op.create_table(
        "recommendation_claim_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "claim_id",
            sa.Integer(),
            sa.ForeignKey("recommendation_claims.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_recommendation_claim_events_claim_id",
        "recommendation_claim_events",
        ["claim_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_recommendation_claim_events_claim_id",
        table_name="recommendation_claim_events",
    )
    op.drop_table("recommendation_claim_events")
    op.drop_index(
        "ix_recommendation_bios_scopes_exact",
        table_name="recommendation_claim_bios_scopes",
    )
    op.drop_index(
        "ix_recommendation_bios_scopes_vendor",
        table_name="recommendation_claim_bios_scopes",
    )
    op.drop_index(
        "ix_recommendation_bios_scopes_model",
        table_name="recommendation_claim_bios_scopes",
    )
    op.drop_table("recommendation_claim_bios_scopes")
    op.drop_table("recommendation_claim_applicability_targets")
    op.drop_index(
        "ix_recommendation_claim_source_settings_setting_id",
        table_name="recommendation_claim_source_settings",
    )
    op.drop_table("recommendation_claim_source_settings")
    op.drop_index(
        "ix_recommendation_claim_evidence_passage_id",
        table_name="recommendation_claim_evidence",
    )
    op.drop_table("recommendation_claim_evidence")
    op.drop_index("ix_recommendation_claims_risk_level", table_name="recommendation_claims")
    op.drop_index("ix_recommendation_claims_use_case", table_name="recommendation_claims")
    op.drop_index("ix_recommendation_claims_status", table_name="recommendation_claims")
    op.drop_index("ix_recommendation_claims_canonical_id", table_name="recommendation_claims")
    op.drop_table("recommendation_claims")
