"""versions.evidence_name/evidence_type — optional sign-off proof image
metadata (openspec/changes/add-signoff-evidence). Bytes live in the blob
store at sign_off_evidence/{version_id}.

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-12
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("versions", sa.Column("evidence_name", sa.Text, nullable=True))
    op.add_column("versions", sa.Column("evidence_type", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("versions", "evidence_type")
    op.drop_column("versions", "evidence_name")
