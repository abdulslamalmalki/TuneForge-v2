"""Add reviewed multi-target hardware applicability for guidance claims.

Revision ID: 0024
Revises: 0023
Create Date: 2026-08-14
"""

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cpu_vendors",
        sa.Column("vendor_id", sa.Text(), primary_key=True),
        sa.Column("canonical_name", sa.Text(), nullable=False, unique=True),
    )
    op.bulk_insert(
        sa.table(
            "cpu_vendors",
            sa.column("vendor_id", sa.Text()),
            sa.column("canonical_name", sa.Text()),
        ),
        [
            {"vendor_id": "amd", "canonical_name": "AMD"},
            {"vendor_id": "intel", "canonical_name": "Intel"},
        ],
    )
    op.create_table(
        "motherboard_families",
        sa.Column("family_id", sa.Text(), primary_key=True),
        sa.Column(
            "vendor_id",
            sa.Text(),
            sa.ForeignKey("vendors.vendor_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("canonical_name", sa.Text(), nullable=False, unique=True),
    )
    op.create_index("ix_motherboard_families_vendor_id", "motherboard_families", ["vendor_id"])
    op.bulk_insert(
        sa.table(
            "motherboard_families",
            sa.column("family_id", sa.Text()),
            sa.column("vendor_id", sa.Text()),
            sa.column("canonical_name", sa.Text()),
        ),
        [
            {
                "family_id": "asus-tuf-gaming",
                "vendor_id": "asus",
                "canonical_name": "ASUS TUF Gaming",
            },
            {
                "family_id": "asrock-steel-legend",
                "vendor_id": "asrock",
                "canonical_name": "ASRock Steel Legend",
            },
            {
                "family_id": "asrock-taichi",
                "vendor_id": "asrock",
                "canonical_name": "ASRock Taichi",
            },
            {
                "family_id": "gigabyte-aorus",
                "vendor_id": "gigabyte",
                "canonical_name": "GIGABYTE AORUS",
            },
            {
                "family_id": "msi-mpg",
                "vendor_id": "msi",
                "canonical_name": "MSI MPG",
            },
        ],
    )
    with op.batch_alter_table("motherboard_models") as batch_op:
        batch_op.add_column(sa.Column("family_id", sa.Text(), nullable=True))
        batch_op.create_foreign_key(
            "fk_motherboard_models_family_id",
            "motherboard_families",
            ["family_id"],
            ["family_id"],
            ondelete="RESTRICT",
        )
        batch_op.create_index("ix_motherboard_models_family_id", ["family_id"])
    family_updates = (
        ("asus-tuf-gaming", "asus", "%TUF GAMING%"),
        ("asrock-steel-legend", "asrock", "%Steel Legend%"),
        ("asrock-taichi", "asrock", "%Taichi%"),
        ("gigabyte-aorus", "gigabyte", "%AORUS%"),
        ("msi-mpg", "msi", "MPG %"),
    )
    connection = op.get_bind()
    for family_id, vendor_id, pattern in family_updates:
        connection.execute(
            sa.text(
                "UPDATE motherboard_models SET family_id = :family_id "
                "WHERE vendor_id = :vendor_id AND canonical_name LIKE :pattern"
            ),
            {"family_id": family_id, "vendor_id": vendor_id, "pattern": pattern},
        )

    with op.batch_alter_table("guidance_claims") as batch_op:
        batch_op.drop_constraint("ck_guidance_claim_applicability", type_="check")
        batch_op.create_check_constraint(
            "ck_guidance_claim_applicability",
            "(applicability_scope = 'source_scope' AND cpu_model_id IS NULL) OR "
            "(applicability_scope = 'cpu_model' AND cpu_model_id IS NOT NULL) OR "
            "(applicability_scope = 'reviewed_hardware' AND cpu_model_id IS NULL)",
        )

    op.create_table(
        "guidance_claim_applicability_targets",
        sa.Column(
            "claim_id",
            sa.Integer(),
            sa.ForeignKey("guidance_claims.id", ondelete="CASCADE"),
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
            name="ck_guidance_applicability_exactly_one_target",
        ),
        sa.CheckConstraint(
            "evidence_level IN ('exact_model', 'exact_cpu', 'exact_chipset', "
            "'family', 'architecture', 'platform', 'vendor')",
            name="ck_guidance_applicability_evidence_level",
        ),
    )
    for column in (
        "cpu_model_id",
        "cpu_family_id",
        "cpu_architecture_id",
        "motherboard_model_id",
        "chipset_id",
        "chipset_family_id",
        "platform_id",
        "vendor_id",
        "cpu_vendor_id",
        "motherboard_family_id",
    ):
        op.create_index(
            f"ix_guidance_claim_applicability_{column}",
            "guidance_claim_applicability_targets",
            [column],
        )
    op.execute(
        "INSERT INTO guidance_claim_applicability_targets "
        "(claim_id, ordinal, cpu_model_id, evidence_level, evidence_basis) "
        "SELECT id, 0, cpu_model_id, 'exact_cpu', "
        "'Backfilled from legacy reviewed CPU-model scope.' "
        "FROM guidance_claims WHERE applicability_scope = 'cpu_model'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE guidance_claims SET applicability_scope = 'source_scope' "
        "WHERE applicability_scope = 'reviewed_hardware'"
    )
    for column in reversed(
        (
            "cpu_model_id",
            "cpu_family_id",
            "cpu_architecture_id",
            "motherboard_model_id",
            "chipset_id",
            "chipset_family_id",
            "platform_id",
            "vendor_id",
            "cpu_vendor_id",
            "motherboard_family_id",
        )
    ):
        op.drop_index(
            f"ix_guidance_claim_applicability_{column}",
            table_name="guidance_claim_applicability_targets",
        )
    op.drop_table("guidance_claim_applicability_targets")
    with op.batch_alter_table("motherboard_models") as batch_op:
        batch_op.drop_index("ix_motherboard_models_family_id")
        batch_op.drop_constraint("fk_motherboard_models_family_id", type_="foreignkey")
        batch_op.drop_column("family_id")
    op.drop_index("ix_motherboard_families_vendor_id", table_name="motherboard_families")
    op.drop_table("motherboard_families")
    op.drop_table("cpu_vendors")
    with op.batch_alter_table("guidance_claims") as batch_op:
        batch_op.drop_constraint("ck_guidance_claim_applicability", type_="check")
        batch_op.create_check_constraint(
            "ck_guidance_claim_applicability",
            "(applicability_scope = 'source_scope' AND cpu_model_id IS NULL) OR "
            "(applicability_scope = 'cpu_model' AND cpu_model_id IS NOT NULL)",
        )
