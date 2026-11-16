"""business logic finalization: millimetre thickness, margin precision, source format

Revision ID: 0015_business_logic_finalization
Revises: 0014_mfa_recovery_codes
Create Date: 2026-11-16 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0015_business_logic_finalization"
down_revision: str | None = "0014_mfa_recovery_codes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SOURCE_FORMATS = "'GAEB_X83', 'GAEB_D83', 'GAEB_X84', 'PDF_DOCLING', 'PORTAL', 'API', 'MANUAL'"


def upgrade() -> None:
    # Thickness is stored canonically in millimetres. Existing rows were
    # captured in micrometres, so convert before dropping the old column.
    op.add_column("tender_items", sa.Column("thickness_mm", sa.Numeric(6, 3), nullable=True))
    op.execute(
        "UPDATE tender_items SET thickness_mm = ROUND(thickness_um / 1000.0, 3) "
        "WHERE thickness_um IS NOT NULL"
    )
    op.execute("ALTER TABLE tender_items DROP CONSTRAINT IF EXISTS thickness_range")
    op.execute("ALTER TABLE tender_items DROP CONSTRAINT IF EXISTS ck_tender_items_thickness_range")
    op.drop_column("tender_items", "thickness_um")
    op.create_check_constraint(
        "thickness_range",
        "tender_items",
        "thickness_mm IS NULL OR (thickness_mm >= 0.001 AND thickness_mm <= 2.000)",
    )

    # Margins are decimal fractions with four places (0.0001 = 0.01 %).
    op.alter_column(
        "customer_offers",
        "margin_rate",
        existing_type=sa.Numeric(9, 6),
        type_=sa.Numeric(6, 4),
        existing_nullable=False,
    )
    op.alter_column(
        "customer_offer_lines",
        "margin_rate",
        existing_type=sa.Numeric(9, 6),
        type_=sa.Numeric(6, 4),
        existing_nullable=False,
    )

    # The source-format enum naming is final: the database now rejects any
    # value the Pydantic enum does not define. NOT VALID keeps pre-existing
    # rows upgradeable while still enforcing every new/updated row.
    op.execute(
        "ALTER TABLE tenders ADD CONSTRAINT ck_tenders_source_format_valid "
        f"CHECK (source_format IN ({_SOURCE_FORMATS})) NOT VALID"
    )
    # Enforce the four-eyes separation for all new/updated rows without failing
    # the migration on legacy approvals that may predate this rule.
    op.execute(
        "ALTER TABLE customer_offers ADD CONSTRAINT ck_customer_offers_four_eyes "
        "CHECK (approved_by IS NULL OR owner IS NULL OR lower(approved_by) <> lower(owner)) "
        "NOT VALID"
    )


def downgrade() -> None:
    op.drop_constraint("ck_customer_offers_four_eyes", "customer_offers", type_="check")
    op.drop_constraint("ck_tenders_source_format_valid", "tenders", type_="check")

    op.alter_column(
        "customer_offer_lines",
        "margin_rate",
        existing_type=sa.Numeric(6, 4),
        type_=sa.Numeric(9, 6),
        existing_nullable=False,
    )
    op.alter_column(
        "customer_offers",
        "margin_rate",
        existing_type=sa.Numeric(6, 4),
        type_=sa.Numeric(9, 6),
        existing_nullable=False,
    )

    op.add_column("tender_items", sa.Column("thickness_um", sa.Numeric(10, 2), nullable=True))
    op.execute(
        "UPDATE tender_items SET thickness_um = ROUND(thickness_mm * 1000.0, 2) "
        "WHERE thickness_mm IS NOT NULL"
    )
    op.execute(
        "ALTER TABLE tender_items DROP CONSTRAINT IF EXISTS ck_tender_items_thickness_range"
    )
    op.drop_column("tender_items", "thickness_mm")
    op.create_check_constraint(
        "thickness_range",
        "tender_items",
        "thickness_um IS NULL OR (thickness_um > 0 AND thickness_um <= 2000)",
    )
