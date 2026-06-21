"""Versioned-schema guard.

The product-versioning model (one library per version, bindings in
version_files) is a from-scratch rebuild: decision C9 keeps no legacy
data. Every store calls `ensure_versioned_schema(path)` before opening
its own connection; the first caller on a legacy database drops all
known tables so each store's CREATE TABLE recreates the new shape.

Legacy detection: the DB has any known table but no `versions` table,
or a known table still carries a pre-versioning column (e.g.
templates.product_id, files.product_id, products.library_id).
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path

logger = logging.getLogger("smdr2.dbschema")

_KNOWN_TABLES = (
    "libraries", "classes", "templates", "files", "products",
    "versions", "version_files",
)

_guard_lock = threading.Lock()
_checked_paths: set[str] = set()


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _has_col(conn: sqlite3.Connection, table: str, col: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == col for r in rows)


def _is_legacy(conn: sqlite3.Connection) -> bool:
    any_known = any(_has_table(conn, t) for t in _KNOWN_TABLES)
    if not any_known:
        return False  # fresh DB
    if not _has_table(conn, "versions"):
        return True
    if _has_table(conn, "templates") and _has_col(conn, "templates", "product_id"):
        return True
    if _has_table(conn, "files") and _has_col(conn, "files", "product_id"):
        return True
    if _has_table(conn, "products") and _has_col(conn, "products", "library_id"):
        return True
    return False


def ensure_versioned_schema(path: Path | str) -> None:
    """Drop all known tables when the DB predates the versioned schema.

    Idempotent and order-independent: every store calls this before its
    own CREATE TABLE; only the first call on a legacy file does work.
    """
    from app.db import resolve_url
    if not resolve_url(path).startswith("sqlite"):
        # MariaDB schema is owned by Alembic migrations; the legacy-file
        # guard is meaningless there (prod starts empty per C9).
        return
    key = str(Path(path).resolve())
    with _guard_lock:
        if key in _checked_paths:
            return
        p = Path(path)
        if p.exists():
            conn = sqlite3.connect(str(p))
            try:
                if _is_legacy(conn):
                    logger.warning(
                        "pre-versioning schema detected at %s — rebuilding "
                        "from scratch (no data preserved, per decision C9)",
                        p,
                    )
                    conn.execute("PRAGMA foreign_keys = OFF")
                    with conn:
                        for t in _KNOWN_TABLES:
                            conn.execute(f"DROP TABLE IF EXISTS {t}")
            finally:
                conn.close()
        _checked_paths.add(key)
