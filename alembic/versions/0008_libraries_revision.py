"""libraries.revision — pre-match staleness signal
(change: fix-stale-prematch-cache).

Monotonic counter bumped on every template/class write so a stamped
pre-match snapshot can be detected as stale on read.

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-21
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "libraries",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("libraries", "revision")
