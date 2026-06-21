"""products.customer_id — customer grouping above product
(docs/schema-auth-jobs.md §2).

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-12
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("customer_id", sa.String(64), nullable=False,
                  server_default="uncategorized"),
    )
    op.create_index("idx_products_customer", "products", ["customer_id"])


def downgrade() -> None:
    op.drop_index("idx_products_customer", table_name="products")
    op.drop_column("products", "customer_id")
