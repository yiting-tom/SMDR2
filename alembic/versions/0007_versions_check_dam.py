"""versions.check_dam — per-version 'check DAM' toggle, surfaced in the
DRC manifest as `check_dam`. Defaults off.

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-15
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "versions",
        sa.Column(
            "check_dam",
            sa.Boolean,
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("versions", "check_dam")
