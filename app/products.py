"""Products — top-level grouping for cross-DXF design rule checking.

A *product* (e.g., one IC package design) bundles DXFs by role under
a single customer's library. Valid roles are SBT, BD, POD, RING and
LID — all five are independent and may coexist on the same product.
Rule checking is product-scoped: it runs once across all the
role-bound DXFs after each one has had its Match JSON saved.
"""

from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.storage import DB_PATH


VALID_ROLES: tuple[str, ...] = ("SBT", "BD", "POD", "RING", "LID")


PRODUCTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    library_id  TEXT NOT NULL,
    created_at  REAL NOT NULL,
    FOREIGN KEY (library_id) REFERENCES libraries(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_products_library ON products(library_id);
"""


@dataclass
class Product:
    id: str
    name: str
    library_id: str
    created_at: float

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "library_id": self.library_id,
            "created_at": self.created_at,
        }


class ProductStore:
    """SQLite-backed product CRUD; shares the library.sqlite file."""

    def __init__(self, path: Path | str = DB_PATH) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.lock = threading.RLock()
        with self.lock, self.conn:
            self.conn.executescript(PRODUCTS_SCHEMA)

    def create(self, name: str, library_id: str) -> Product:
        pid = str(uuid.uuid4())[:12]
        rec = Product(id=pid, name=name, library_id=library_id, created_at=time.time())
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO products (id, name, library_id, created_at) VALUES (?, ?, ?, ?)",
                (rec.id, rec.name, rec.library_id, rec.created_at),
            )
        return rec

    def delete(self, product_id: str) -> bool:
        with self.lock, self.conn:
            cur = self.conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
            return cur.rowcount > 0

    def update_library(self, product_id: str, library_id: str) -> bool:
        with self.lock, self.conn:
            cur = self.conn.execute(
                "UPDATE products SET library_id = ? WHERE id = ?",
                (library_id, product_id),
            )
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


def _row_to_product(row: sqlite3.Row) -> Product:
    return Product(
        id=row["id"],
        name=row["name"],
        library_id=row["library_id"],
        created_at=row["created_at"],
    )


# Module-level singleton.
PRODUCT_STORE = ProductStore()
