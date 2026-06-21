"""jobs table — DB-backed queue replacing the in-memory _jobs dict
(docs/schema-auth-jobs.md §7, specs/job-queue).

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-11
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.mysql import LONGTEXT

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

MYSQL_ARGS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}

JSON_TEXT = sa.Text().with_variant(LONGTEXT(), "mysql")
EPOCH = sa.Double()


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("version_id", sa.String(64)),
        sa.Column("file_id", sa.String(64)),
        sa.Column("product_id", sa.String(64)),
        sa.Column("parent_id", sa.String(64)),
        sa.Column("payload", JSON_TEXT, nullable=False),
        sa.Column("result", JSON_TEXT),
        sa.Column("error", JSON_TEXT),
        sa.Column("total", sa.Integer),
        sa.Column("done", sa.Integer),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("submitted_by", sa.String(255)),
        sa.Column("submitted_at", EPOCH, nullable=False),
        sa.Column("started_at", EPOCH),
        sa.Column("completed_at", EPOCH),
        sa.Column("claimed_by", sa.String(255)),
        sa.Column("heartbeat_at", EPOCH),
        **MYSQL_ARGS,
    )
    op.create_index("idx_jobs_queue", "jobs", ["status", "submitted_at"])
    op.create_index("idx_jobs_binding", "jobs",
                    ["kind", "version_id", "file_id", "status"])
    op.create_index("idx_jobs_parent", "jobs", ["parent_id"])


def downgrade() -> None:
    op.drop_table("jobs")
