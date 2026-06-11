"""Initial schema — all twelve tables.

Authoritative DDL for MariaDB (and any Alembic-managed DB). The per-store
`*_SCHEMA` constants remain only for SQLite test/dev bootstrap; dialect
mapping follows docs/schema-auth-jobs.md §8:

- TEXT ids → VARCHAR(64); people/display fields → VARCHAR(255)
- REAL epoch timestamps → DOUBLE (kept as floats, not DATETIME)
- JSON-in-TEXT → LONGTEXT on MySQL (entity_point_sets routinely > 64KB)
- INTEGER PK AUTOINCREMENT → BIGINT AUTO_INCREMENT
- every table: InnoDB + utf8mb4/utf8mb4_unicode_ci

Revision ID: 0001
Revises:
Create Date: 2026-06-11
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.mysql import LONGTEXT

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

MYSQL_ARGS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}


def _id(name: str, *args, **kw) -> sa.Column:  # short VARCHAR id column
    return sa.Column(name, sa.String(64), *args, **kw)


JSON_TEXT = sa.Text().with_variant(LONGTEXT(), "mysql")
EPOCH = sa.Double()
BIG_AUTO = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    # ---- library domain ----------------------------------------------------
    op.create_table(
        "libraries",
        _id("id", primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("created_at", EPOCH, nullable=False),
        **MYSQL_ARGS,
    )
    op.create_table(
        "classes",
        _id("library_id", sa.ForeignKey("libraries.id", ondelete="CASCADE"),
            primary_key=True),
        sa.Column("name", sa.String(64), primary_key=True),
        sa.Column("rank", sa.Integer, nullable=False),
        sa.Column("created_at", EPOCH, nullable=False),
        sa.Column("match_strategy", sa.String(32), nullable=False,
                  server_default="chamfer"),
        sa.Column("bbox_ratio", sa.Double),
        **MYSQL_ARGS,
    )
    op.create_table(
        "templates",
        _id("id", primary_key=True),
        _id("library_id", sa.ForeignKey("libraries.id", ondelete="CASCADE"),
            nullable=False),
        sa.Column("class_name", sa.String(64), nullable=False),
        sa.Column("entity_point_sets", JSON_TEXT, nullable=False),
        sa.Column("centroid_x", sa.Double, nullable=False),
        sa.Column("centroid_y", sa.Double, nullable=False),
        sa.Column("bbox_xmin", sa.Double, nullable=False),
        sa.Column("bbox_ymin", sa.Double, nullable=False),
        sa.Column("bbox_xmax", sa.Double, nullable=False),
        sa.Column("bbox_ymax", sa.Double, nullable=False),
        sa.Column("created_at", EPOCH, nullable=False),
        sa.Column("entity_kinds", JSON_TEXT),
        **MYSQL_ARGS,
    )
    op.create_index("idx_templates_lib_class", "templates",
                    ["library_id", "class_name"])

    # ---- product / version domain -------------------------------------------
    op.create_table(
        "products",
        _id("id", primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("created_at", EPOCH, nullable=False),
        **MYSQL_ARGS,
    )
    op.create_table(
        "versions",
        _id("id", primary_key=True),
        _id("product_id", nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        _id("library_id", nullable=False, unique=True),
        sa.Column("signed_off_by", sa.String(255)),
        sa.Column("signed_off_at", EPOCH),
        sa.Column("created_at", EPOCH, nullable=False),
        sa.UniqueConstraint("product_id", "label", name="uq_versions_product_label"),
        **MYSQL_ARGS,
    )
    op.create_index("idx_versions_product", "versions", ["product_id"])

    # ---- files / bindings ------------------------------------------------------
    op.create_table(
        "files",
        _id("id", primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("size", sa.BigInteger, nullable=False),
        sa.Column("uploaded_at", EPOCH, nullable=False),
        sa.Column("insunits", sa.Integer),
        sa.Column("dxf_recover_notes", JSON_TEXT),
        **MYSQL_ARGS,
    )
    op.create_index("idx_files_uploaded_at", "files", ["uploaded_at"])
    op.create_table(
        "version_files",
        _id("version_id", primary_key=True),
        _id("file_id", sa.ForeignKey("files.id"), primary_key=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("dxf_view", sa.String(16)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error", JSON_TEXT),
        sa.Column("match_saved", sa.Integer, nullable=False, server_default="0"),
        sa.Column("selected_layers", JSON_TEXT),
        sa.Column("top_view_rect", JSON_TEXT),
        sa.Column("bottom_view_rect", JSON_TEXT),
        sa.Column("side_view_rect", JSON_TEXT),
        sa.Column("applied_scale", sa.Double, nullable=False, server_default="1.0"),
        sa.Column("user_unit_override", sa.String(16)),
        sa.Column("chosen_layout", sa.String(255)),
        sa.Column("parsed_at", EPOCH),
        sa.Column("primitive_count", sa.Integer),
        sa.Column("bbox_xmin", sa.Double),
        sa.Column("bbox_ymin", sa.Double),
        sa.Column("bbox_xmax", sa.Double),
        sa.Column("bbox_ymax", sa.Double),
        sa.Column("background", sa.String(32)),
        sa.Column("created_at", EPOCH, nullable=False),
        **MYSQL_ARGS,
    )
    op.create_index("idx_vf_version", "version_files", ["version_id"])
    op.create_index("idx_vf_file", "version_files", ["file_id"])
    op.create_index("idx_vf_version_role", "version_files", ["version_id", "role"])

    # ---- auth domain (docs/schema-auth-jobs.md §1–§6) -----------------------------
    op.create_table(
        "users",
        sa.Column("userid", sa.String(255), primary_key=True),
        sa.Column("oidc_sub", sa.String(255)),
        sa.Column("email", sa.String(255)),
        sa.Column("name", sa.String(255)),
        sa.Column("deptid", sa.String(64)),
        sa.Column("deptname", sa.String(255)),
        sa.Column("company", sa.String(255)),
        sa.Column("twsitecode", sa.String(64)),
        sa.Column("created_at", EPOCH, nullable=False),
        sa.Column("last_login_at", EPOCH),
        **MYSQL_ARGS,
    )
    op.create_index("idx_users_deptid", "users", ["deptid"])
    op.create_table(
        "customers",
        _id("id", primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("created_at", EPOCH, nullable=False),
        **MYSQL_ARGS,
    )
    op.create_table(
        "role_grants",
        _id("id", primary_key=True),
        sa.Column("grantee_type", sa.String(16), nullable=False),
        sa.Column("grantee_id", sa.String(64), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("scope_type", sa.String(16), nullable=False),
        sa.Column("scope_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("granted_by", sa.String(255), nullable=False),
        sa.Column("granted_at", EPOCH, nullable=False),
        sa.UniqueConstraint("grantee_type", "grantee_id", "role",
                            "scope_type", "scope_id", name="uq_grants"),
        sa.CheckConstraint("grantee_type IN ('user','dept')",
                           name="ck_grants_grantee_type"),
        sa.CheckConstraint("role IN ('admin','editor','viewer')",
                           name="ck_grants_role"),
        sa.CheckConstraint("scope_type IN ('global','customer','product')",
                           name="ck_grants_scope_type"),
        sa.CheckConstraint("(scope_type = 'global') = (scope_id = '')",
                           name="ck_grants_global_sentinel"),
        **MYSQL_ARGS,
    )
    op.create_index("idx_grants_grantee", "role_grants",
                    ["grantee_type", "grantee_id"])
    op.create_index("idx_grants_scope", "role_grants",
                    ["scope_type", "scope_id"])
    op.create_table(
        "audit_log",
        sa.Column("id", BIG_AUTO, primary_key=True, autoincrement=True),
        sa.Column("at", EPOCH, nullable=False),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        _id("product_id"),
        _id("version_id"),
        sa.Column("target_type", sa.String(32), nullable=False),
        sa.Column("target_id", sa.String(255), nullable=False),
        sa.Column("detail", JSON_TEXT),
        **MYSQL_ARGS,
    )
    op.create_index("idx_audit_at", "audit_log", ["at"])
    op.create_index("idx_audit_target", "audit_log", ["target_type", "target_id"])
    op.create_index("idx_audit_product", "audit_log", ["product_id"])
    op.create_table(
        "product_edit_locks",
        _id("product_id", primary_key=True),
        sa.Column("held_by", sa.String(255), nullable=False),
        sa.Column("acquired_at", EPOCH, nullable=False),
        sa.Column("heartbeat_at", EPOCH, nullable=False),
        **MYSQL_ARGS,
    )


def downgrade() -> None:
    for t in (
        "product_edit_locks", "audit_log", "role_grants", "customers",
        "users", "version_files", "files", "versions", "products",
        "templates", "classes", "libraries",
    ):
        op.drop_table(t)
