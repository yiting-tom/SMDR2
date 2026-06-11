"""sessions table — server-side BFF sessions (docs/schema-auth-jobs.md §4).

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-12
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

MYSQL_ARGS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(64), primary_key=True),  # SHA-256 hex
        sa.Column("userid", sa.String(255), nullable=False),
        sa.Column("created_at", sa.Double, nullable=False),
        sa.Column("last_seen_at", sa.Double, nullable=False),
        sa.Column("expires_at", sa.Double, nullable=False),
        sa.Column("csrf_token", sa.String(64), nullable=False),
        **MYSQL_ARGS,
    )
    op.create_index("idx_sessions_user", "sessions", ["userid"])
    op.create_index("idx_sessions_expires", "sessions", ["expires_at"])


def downgrade() -> None:
    op.drop_table("sessions")
