"""supplier contacts and customer master data

Revision ID: 0019_master_data
Revises: 0018_worker_commands
Create Date: 2026-11-18 02:10:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0019_master_data"
down_revision: str | None = "0018_worker_commands"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("suppliers", sa.Column("legal_name", sa.String(length=256), nullable=True))
    op.add_column("suppliers", sa.Column("phone", sa.String(length=64), nullable=True))
    op.add_column("suppliers", sa.Column("street", sa.String(length=256), nullable=True))
    op.add_column("suppliers", sa.Column("postal_code", sa.String(length=32), nullable=True))
    op.add_column("suppliers", sa.Column("city", sa.String(length=128), nullable=True))
    op.add_column(
        "suppliers",
        sa.Column(
            "country_code",
            sa.String(length=2),
            server_default=sa.text("'DE'"),
            nullable=False,
        ),
    )
    op.add_column("suppliers", sa.Column("payment_terms", sa.String(length=512), nullable=True))
    op.add_column("suppliers", sa.Column("delivery_days", sa.Integer(), nullable=True))
    op.add_column("suppliers", sa.Column("notes", sa.Text(), nullable=True))

    op.create_table(
        "supplier_contacts",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("supplier_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("role", sa.String(length=128), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("phone", sa.String(length=64), nullable=True),
        sa.Column("is_primary", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_supplier_contacts_supplier_id", "supplier_contacts", ["supplier_id"])
    op.create_index(
        "uq_supplier_contacts_primary",
        "supplier_contacts",
        ["supplier_id"],
        unique=True,
        postgresql_where=sa.text("is_primary"),
    )

    op.create_table(
        "customers",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("legal_name", sa.String(length=256), nullable=True),
        sa.Column("street", sa.String(length=256), nullable=False),
        sa.Column("postal_code", sa.String(length=32), nullable=False),
        sa.Column("city", sa.String(length=128), nullable=False),
        sa.Column(
            "country_code",
            sa.String(length=2),
            server_default=sa.text("'DE'"),
            nullable=False,
        ),
        sa.Column("vat_id", sa.String(length=32), nullable=True),
        sa.Column("leitweg_id", sa.String(length=46), nullable=True),
        sa.Column("payment_terms", sa.String(length=512), nullable=True),
        sa.Column("contact_name", sa.String(length=256), nullable=True),
        sa.Column("contact_email", sa.String(length=320), nullable=True),
        sa.Column("phone", sa.String(length=64), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_customers_name"),
    )
    op.create_index("ix_customers_active", "customers", ["is_active"])


def downgrade() -> None:
    op.drop_index("ix_customers_active", table_name="customers")
    op.drop_table("customers")
    op.drop_index("uq_supplier_contacts_primary", table_name="supplier_contacts")
    op.drop_index("ix_supplier_contacts_supplier_id", table_name="supplier_contacts")
    op.drop_table("supplier_contacts")
    op.drop_column("suppliers", "notes")
    op.drop_column("suppliers", "delivery_days")
    op.drop_column("suppliers", "payment_terms")
    op.drop_column("suppliers", "country_code")
    op.drop_column("suppliers", "city")
    op.drop_column("suppliers", "postal_code")
    op.drop_column("suppliers", "street")
    op.drop_column("suppliers", "phone")
    op.drop_column("suppliers", "legal_name")
