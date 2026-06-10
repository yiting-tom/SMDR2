"""Products — the identity that owns versions (route 1, 2026-06-10).

A *product* (one IC package design) is a container of versions; each
version owns its library (templates + match config) and role-file
bindings. Rules are product-level and version-independent. Products no
longer reference a library directly — `products.library_id` died with
the shared-library topology.

Valid DXF roles are SBT, BD, POD, RING and LID — all five independent.
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.dbschema import ensure_versioned_schema
from app.storage import DB_PATH


VALID_ROLES: tuple[str, ...] = ("SBT", "BD", "POD", "RING", "LID")


PRODUCTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  REAL NOT NULL
);
"""


@dataclass
class Product:
    id: str
    name: str
    created_at: float

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "created_at": self.created_at,
        }


class ProductStore:
    """SQLite-backed product CRUD; shares the library.sqlite file.

    Creation of a product happens through
    `app.versions.VersionStore.create_product` so the product row and its
    mandatory first version land in one transaction (C7: no version-less
    products). This store covers reads and deletion.
    """

    def __init__(self, path: Path | str = DB_PATH) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        ensure_versioned_schema(path)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.lock = threading.RLock()
        with self.lock, self.conn:
            self.conn.executescript(PRODUCTS_SCHEMA)

    def delete(self, product_id: str) -> bool:
        """Remove the product row only — the API layer cascades versions
        (VersionStore.delete_for_product) and artifacts first."""
        with self.lock, self.conn:
            cur = self.conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
            return cur.rowcount > 0

    def get(self, product_id: str) -> Product | None:
        with self.lock:
            row = self.conn.execute(
                "SELECT * FROM products WHERE id = ?", (product_id,)
            ).fetchone()
        return _row_to_product(row) if row else None

    def list_all(self) -> list[Product]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM products ORDER BY created_at DESC"
            ).fetchall()
        return [_row_to_product(r) for r in rows]


def new_product_id() -> str:
    return str(uuid.uuid4())[:12]


def _row_to_product(row: sqlite3.Row) -> Product:
    return Product(
        id=row["id"],
        name=row["name"],
        created_at=row["created_at"],
    )


# Module-level singleton.
PRODUCT_STORE = ProductStore()
