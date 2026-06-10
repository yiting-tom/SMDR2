"""Versions — the snapshot unit under a product (route 1, 2026-06-10).

Each version owns exactly one library (templates + per-class match
config) and a set of role-file bindings (`version_files`, managed by
`app.files.FileStore`). Creating a new version clones the source
version's library content and bindings in a single transaction; derived
artifacts are NOT cloned (they are recomputed per version — artifact
paths key on `(version_id, file_id)`).

Sign-off freezes a version: `signed_off_by` non-null means every
mutating endpoint must reject with 409 (enforced in app.main via the
`require_unsigned` dependency). Identity is a dev placeholder
(`SMDR2_DEV_USER`) until the auth change lands.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.dbschema import ensure_versioned_schema
from app.storage import DB_PATH

VERSIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS versions (
    id            TEXT PRIMARY KEY,
    product_id    TEXT NOT NULL,
    label         TEXT NOT NULL,
    library_id    TEXT NOT NULL UNIQUE,
    signed_off_by TEXT,
    signed_off_at REAL,
    created_at    REAL NOT NULL,
    UNIQUE (product_id, label)
);

CREATE INDEX IF NOT EXISTS idx_versions_product ON versions(product_id);
"""


class DuplicateLabel(ValueError):
    """label already exists within the product → HTTP 409."""


class SignedOff(ValueError):
    """target version is signed off → HTTP 409."""

    def __init__(self, by: str, at: float | None) -> None:
        super().__init__(f"version signed off by {by}")
        self.by = by
        self.at = at


@dataclass
class Version:
    id: str
    product_id: str
    label: str
    library_id: str
    signed_off_by: str | None
    signed_off_at: float | None
    created_at: float

    @property
    def is_signed_off(self) -> bool:
        return self.signed_off_by is not None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "product_id": self.product_id,
            "label": self.label,
            "library_id": self.library_id,
            "signed_off_by": self.signed_off_by,
            "signed_off_at": self.signed_off_at,
            "created_at": self.created_at,
        }


def _row_to_version(row: sqlite3.Row) -> Version:
    return Version(
        id=row["id"],
        product_id=row["product_id"],
        label=row["label"],
        library_id=row["library_id"],
        signed_off_by=row["signed_off_by"],
        signed_off_at=row["signed_off_at"],
        created_at=row["created_at"],
    )


def new_id() -> str:
    return str(uuid.uuid4())[:12]


class VersionStore:
    """SQLite-backed version CRUD + the clone transaction.

    Shares library.sqlite. Clone copies rows from `templates`,
    `classes`, and `version_files` directly — those tables belong to
    other stores' domains, but the copy must be one transaction, so a
    single connection does all of it.
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
            self.conn.executescript(VERSIONS_SCHEMA)

    # ---- reads -----------------------------------------------------------
    def get(self, version_id: str) -> Version | None:
        with self.lock:
            row = self.conn.execute(
                "SELECT * FROM versions WHERE id = ?", (version_id,)
            ).fetchone()
        return _row_to_version(row) if row else None

    def get_by_library(self, library_id: str) -> Version | None:
        with self.lock:
            row = self.conn.execute(
                "SELECT * FROM versions WHERE library_id = ?", (library_id,)
            ).fetchone()
        return _row_to_version(row) if row else None

    def list_by_product(self, product_id: str) -> list[Version]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM versions WHERE product_id = ? ORDER BY created_at ASC",
                (product_id,),
            ).fetchall()
        return [_row_to_version(r) for r in rows]

    def latest_for_product(self, product_id: str) -> Version | None:
        with self.lock:
            row = self.conn.execute(
                "SELECT * FROM versions WHERE product_id = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (product_id,),
            ).fetchone()
        return _row_to_version(row) if row else None

    # ---- create ----------------------------------------------------------
    def create_product(self, name: str, version_label: str):
        """Create a product row plus its mandatory first version (empty
        library) in ONE transaction — C7: no version-less products.
        Returns (product, version)."""
        from app.products import PRODUCTS_SCHEMA, Product, new_product_id
        label = version_label.strip()
        if not label:
            raise ValueError("version label must be non-empty")
        with self.lock, self.conn:
            self.conn.executescript(PRODUCTS_SCHEMA)
            product = Product(id=new_product_id(), name=name, created_at=time.time())
            self.conn.execute(
                "INSERT INTO products (id, name, created_at) VALUES (?, ?, ?)",
                (product.id, product.name, product.created_at),
            )
            version = self._insert_version_locked(product.id, label)
        return product, version

    def create_first(self, product_id: str, label: str) -> Version:
        """First version for an existing product row (tests / fixtures)."""
        return self._insert_version(product_id, label.strip())

    def create_clone(self, product_id: str, label: str, source: Version) -> Version:
        """New version = clone of `source` (same product): library
        content (templates + class config) and version_files bindings,
        one transaction. Derived artifacts are not copied."""
        label = label.strip()
        with self.lock, self.conn:
            ver = self._insert_version_locked(product_id, label)
            # classes (match config) — copy every row onto the new library.
            self.conn.execute(
                "INSERT INTO classes (library_id, name, rank, created_at, "
                " match_strategy, bbox_ratio) "
                "SELECT ?, name, rank, created_at, match_strategy, bbox_ratio "
                "FROM classes WHERE library_id = ?",
                (ver.library_id, source.library_id),
            )
            # templates — new primary keys, content/created_at preserved.
            rows = self.conn.execute(
                "SELECT class_name, entity_point_sets, centroid_x, centroid_y, "
                " bbox_xmin, bbox_ymin, bbox_xmax, bbox_ymax, created_at, entity_kinds "
                "FROM templates WHERE library_id = ?",
                (source.library_id,),
            ).fetchall()
            self.conn.executemany(
                "INSERT INTO templates (id, library_id, class_name, "
                " entity_point_sets, centroid_x, centroid_y, "
                " bbox_xmin, bbox_ymin, bbox_xmax, bbox_ymax, created_at, entity_kinds) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        str(uuid.uuid4()), ver.library_id, r["class_name"],
                        r["entity_point_sets"], r["centroid_x"], r["centroid_y"],
                        r["bbox_xmin"], r["bbox_ymin"], r["bbox_xmax"], r["bbox_ymax"],
                        r["created_at"], r["entity_kinds"],
                    )
                    for r in rows
                ],
            )
            # bindings — same file_ids and per-version state onto the new
            # version. Lifecycle resets to 'preprocessing': the new
            # version's derived artifacts don't exist yet; the API layer
            # submits preprocess jobs for each copied binding right after
            # the clone commits.
            self.conn.execute(
                "INSERT INTO version_files (version_id, role, file_id, "
                " dxf_view, status, error, match_saved, selected_layers, "
                " top_view_rect, bottom_view_rect, side_view_rect, "
                " applied_scale, user_unit_override, chosen_layout, "
                " parsed_at, primitive_count, "
                " bbox_xmin, bbox_ymin, bbox_xmax, bbox_ymax, background, "
                " created_at) "
                "SELECT ?, role, file_id, dxf_view, 'preprocessing', NULL, 0, "
                " selected_layers, top_view_rect, bottom_view_rect, side_view_rect, "
                " applied_scale, user_unit_override, chosen_layout, "
                " NULL, NULL, NULL, NULL, NULL, NULL, NULL, ? "
                "FROM version_files WHERE version_id = ?",
                (ver.id, time.time(), source.id),
            )
        return ver

    def _insert_version(self, product_id: str, label: str) -> Version:
        with self.lock, self.conn:
            return self._insert_version_locked(product_id, label)

    def _insert_version_locked(self, product_id: str, label: str) -> Version:
        if not label:
            raise ValueError("version label must be non-empty")
        vid = new_id()
        library_id = new_id()
        now = time.time()
        self.conn.execute(
            "INSERT INTO libraries (id, name, created_at) VALUES (?, ?, ?)",
            (library_id, f"version:{vid}", now),
        )
        try:
            self.conn.execute(
                "INSERT INTO versions (id, product_id, label, library_id, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (vid, product_id, label, library_id, now),
            )
        except sqlite3.IntegrityError as e:
            raise DuplicateLabel(
                f"version label {label!r} already exists for product {product_id}"
            ) from e
        return Version(
            id=vid, product_id=product_id, label=label, library_id=library_id,
            signed_off_by=None, signed_off_at=None, created_at=now,
        )

    # ---- sign-off --------------------------------------------------------
    def sign_off(self, version_id: str, by: str) -> Version:
        with self.lock, self.conn:
            row = self.conn.execute(
                "SELECT * FROM versions WHERE id = ?", (version_id,)
            ).fetchone()
            if row is None:
                raise KeyError(version_id)
            if row["signed_off_by"] is not None:
                raise SignedOff(row["signed_off_by"], row["signed_off_at"])
            self.conn.execute(
                "UPDATE versions SET signed_off_by = ?, signed_off_at = ? WHERE id = ?",
                (by, time.time(), version_id),
            )
        v = self.get(version_id)
        assert v is not None
        return v

    def unsign(self, version_id: str) -> Version:
        """Admin-only once auth lands; unrestricted in the dev window."""
        with self.lock, self.conn:
            cur = self.conn.execute(
                "UPDATE versions SET signed_off_by = NULL, signed_off_at = NULL "
                "WHERE id = ?",
                (version_id,),
            )
            if cur.rowcount == 0:
                raise KeyError(version_id)
        v = self.get(version_id)
        assert v is not None
        return v

    # ---- delete (product cascade only) ------------------------------------
    def delete_for_product(self, product_id: str) -> list[Version]:
        """Remove every version of a product. Returns the removed rows so
        the caller can cascade libraries/bindings/artifacts. The only
        deletion path — versions are never individually deletable."""
        versions = self.list_by_product(product_id)
        with self.lock, self.conn:
            for v in versions:
                self.conn.execute(
                    "DELETE FROM version_files WHERE version_id = ?", (v.id,)
                )
                self.conn.execute("DELETE FROM versions WHERE id = ?", (v.id,))
                # library + its classes/templates cascade via FK.
                self.conn.execute(
                    "DELETE FROM libraries WHERE id = ?", (v.library_id,)
                )
        return versions


def summarize(v: Version, extra: dict | None = None) -> dict:
    d = v.to_dict()
    if extra:
        d.update(extra)
    return d


# Lazily-created module singleton (app.main wires it; tests build their own).
VERSION_STORE = VersionStore()


def parse_json_col(raw: str | None) -> dict | list | None:
    return json.loads(raw) if raw else None
