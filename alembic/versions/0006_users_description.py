"""users.description — corporate JWT `description` claim, formatted
"<uid>, <中文名>, <英文名>"; display only (top-nav identity chip).

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-12
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("description", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("users", "description")
