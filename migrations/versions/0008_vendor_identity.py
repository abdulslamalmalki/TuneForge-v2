"""Add stable vendor identities, aliases, and manual references.

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-16
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

_VENDORS = (
    (
        "asus",
        "ASUS",
        (
            "ASUS",
            "ASUSTeK",
            "ASUSTeK Computer",
            "ASUSTeK Computer Inc.",
            "ASUSTeK Computer Incorporated",
            "Republic of Gamers",
            "ROG",
        ),
    ),
    (
        "msi",
        "MSI",
        (
            "MSI",
            "Micro-Star",
            "Micro Star",
            "Micro-Star International",
            "Micro-Star International Co., Ltd.",
        ),
    ),
    (
        "gigabyte",
        "GIGABYTE",
        (
            "GIGABYTE",
            "Gigabyte Technology",
            "GIGABYTE Technology Co., Ltd.",
            "GIGA-BYTE Technology",
            "AORUS",
        ),
    ),
    (
        "asrock",
        "ASRock",
        (
            "ASRock",
            "ASRock Inc.",
            "ASRock Incorporation",
        ),
    ),
)


def _normalized_key(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def upgrade() -> None:
    op.create_table(
        "vendors",
        sa.Column("vendor_id", sa.Text(), primary_key=True),
        sa.Column("canonical_name", sa.Text(), nullable=False, unique=True),
    )
    op.create_table(
        "vendor_aliases",
        sa.Column("normalized_alias", sa.Text(), primary_key=True),
        sa.Column(
            "vendor_id",
            sa.Text(),
            sa.ForeignKey("vendors.vendor_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("display_alias", sa.Text(), nullable=False),
    )
    op.create_index("ix_vendor_aliases_vendor_id", "vendor_aliases", ["vendor_id"])
    with op.batch_alter_table("manuals") as batch_op:
        batch_op.add_column(sa.Column("vendor_id", sa.Text(), nullable=True))
        batch_op.create_foreign_key(
            "fk_manuals_vendor_id_vendors",
            "vendors",
            ["vendor_id"],
            ["vendor_id"],
            ondelete="RESTRICT",
        )
    op.create_index("ix_manuals_vendor_id", "manuals", ["vendor_id"])

    connection = op.get_bind()
    definitions_by_key: dict[str, tuple[str, str]] = {}
    for vendor_id, canonical_name, aliases in _VENDORS:
        connection.execute(
            sa.text(
                "INSERT INTO vendors (vendor_id, canonical_name) "
                "VALUES (:vendor_id, :canonical_name)"
            ),
            {"vendor_id": vendor_id, "canonical_name": canonical_name},
        )
        for alias in (vendor_id, canonical_name, *aliases):
            key = _normalized_key(alias)
            definitions_by_key[key] = (vendor_id, canonical_name)
            connection.execute(
                sa.text(
                    "INSERT OR IGNORE INTO vendor_aliases "
                    "(normalized_alias, vendor_id, display_alias) "
                    "VALUES (:normalized_alias, :vendor_id, :display_alias)"
                ),
                {
                    "normalized_alias": key,
                    "vendor_id": vendor_id,
                    "display_alias": alias,
                },
            )

    rows = connection.execute(sa.text("SELECT id, vendor, created_at FROM manuals")).mappings()
    unknown: list[str] = []
    for row in rows:
        raw_vendor = row["vendor"] or ""
        key = _normalized_key(raw_vendor)
        if not key:
            continue
        match = definitions_by_key.get(key)
        if match is None:
            unknown.append(f"manual {row['id']}: {raw_vendor!r}")
            continue
        vendor_id, canonical_name = match
        connection.execute(
            sa.text(
                "UPDATE manuals SET vendor_id = :vendor_id, vendor = :canonical_name "
                "WHERE id = :manual_id"
            ),
            {
                "vendor_id": vendor_id,
                "canonical_name": canonical_name,
                "manual_id": row["id"],
            },
        )
        if " ".join(raw_vendor.split()) != canonical_name:
            detail = {
                "normalization_version": "1.0",
                "raw_value": raw_vendor,
                "vendor_id": vendor_id,
                "canonical_name": canonical_name,
            }
            connection.execute(
                sa.text(
                    "INSERT INTO manual_events "
                    "(manual_id, action, actor, detail, created_at) "
                    "VALUES (:manual_id, 'vendor_normalized', 'system-migration', "
                    ":detail, :created_at)"
                ),
                {
                    "manual_id": row["id"],
                    "detail": json.dumps(detail, sort_keys=True),
                    "created_at": row["created_at"],
                },
            )
    if unknown:
        raise RuntimeError(
            "Vendor normalization cannot backfill unknown historical labels: " + "; ".join(unknown)
        )


def downgrade() -> None:
    op.drop_index("ix_manuals_vendor_id", table_name="manuals")
    with op.batch_alter_table("manuals") as batch_op:
        batch_op.drop_constraint("fk_manuals_vendor_id_vendors", type_="foreignkey")
        batch_op.drop_column("vendor_id")
    op.drop_table("vendor_aliases")
    op.drop_table("vendors")
