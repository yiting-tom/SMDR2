"""DXF file metadata store, persisted alongside the template library.

Each uploaded DXF has a row tracking its lifecycle:
    discovering_layers → awaiting_layers → preprocessing → ready_to_match
                                                       → error

The actual DXF bytes and the parsed JSON live on disk under data/uploads/ and
data/parsed/ respectively (see `app.storage`). This module only manages the
SQLite-backed metadata.
"""

from __future__ import annotations

import json as _json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from app.storage import DB_PATH

# Lifecycle states. The visible-to-user pipeline is:
#   discovering_layers → awaiting_layers → preprocessing → ready_to_match
#                                                     → checking_rules → report
# (error short-circuits from anywhere.)
DISCOVERING_LAYERS = "discovering_layers"
AWAITING_LAYERS = "awaiting_layers"
PREPROCESSING = "preprocessing"
READY = "ready_to_match"
CHECKING = "checking_rules"
REPORT = "report"
ERROR = "error"

ALL_STATUSES = (
    DISCOVERING_LAYERS,
    AWAITING_LAYERS,
    PREPROCESSING,
    READY,
    CHECKING,
    REPORT,
    ERROR,
)


FILES_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    size            INTEGER NOT NULL,
    uploaded_at     REAL NOT NULL,
    status          TEXT NOT NULL,
    error           TEXT,
    parsed_at       REAL,
    primitive_count INTEGER,
    bbox_xmin       REAL,
    bbox_ymin       REAL,
    bbox_xmax       REAL,
    bbox_ymax       REAL,
    background      TEXT,
    library_id      TEXT NOT NULL DEFAULT 'default',
    product_id      TEXT,
    dxf_role        TEXT,
    match_saved     INTEGER NOT NULL DEFAULT 0,
    selected_layers TEXT,
    frontside_rect  TEXT,
    bottomside_rect TEXT
);

CREATE INDEX IF NOT EXISTS idx_files_status ON files(status);
CREATE INDEX IF NOT EXISTS idx_files_uploaded_at ON files(uploaded_at);
"""


@dataclass
class FileRecord:
    id: str
    name: str
    size: int
    uploaded_at: float
    status: str
    library_id: str = "default"
    product_id: str | None = None
    dxf_role: str | None = None
    match_saved: bool = False
    error: str | None = None
    parsed_at: float | None = None
    primitive_count: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    background: str | None = None
    # User-chosen layer subset, persisted between phase 1 and phase 2 and
    # reused on re-preprocess. None = legacy (treat as "all layers").
    selected_layers: list[str] | None = None
    # Per-file frontside/bottomside rectangles, world coords, axis-aligned,
    # normalised so x0<=x1, y0<=y1. None = side not marked.
    frontside_rect: dict | None = None
    bottomside_rect: dict | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "size": self.size,
            "uploaded_at": self.uploaded_at,
            "status": self.status,
            "library_id": self.library_id,
            "product_id": self.product_id,
            "dxf_role": self.dxf_role,
            "match_saved": self.match_saved,
            "error": self.error,
            "parsed_at": self.parsed_at,
            "primitive_count": self.primitive_count,
            "bbox": list(self.bbox) if self.bbox else None,
            "background": self.background,
            "selected_layers": (
                list(self.selected_layers)
                if self.selected_layers is not None else None
            ),
            "frontside_rect": dict(self.frontside_rect) if self.frontside_rect else None,
            "bottomside_rect": dict(self.bottomside_rect) if self.bottomside_rect else None,
        }


class FileStore:
    """Thread-safe SQLite-backed file metadata."""

    def __init__(self, path: Path | str = DB_PATH) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.lock = threading.RLock()
        with self.lock, self.conn:
            self.conn.executescript(FILES_SCHEMA)
            # Status migration
            self.conn.execute(
                "UPDATE files SET status = ? WHERE status IN ('queued', 'parsing')",
                (PREPROCESSING,),
            )
            self.conn.execute(
                "UPDATE files SET status = ? WHERE status = 'done'",
                (READY,),
            )
            # Add library_id column for pre-multi-library DBs (must happen
            # before any index referencing the column).
            cols = [r["name"] for r in self.conn.execute("PRAGMA table_info(files)")]
            if "library_id" not in cols:
                self.conn.execute(
                    "ALTER TABLE files ADD COLUMN library_id TEXT NOT NULL DEFAULT 'default'"
                )
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_files_library ON files(library_id)"
            )
            # Product / role / match_saved migration for pre-product DBs.
            cols = [r["name"] for r in self.conn.execute("PRAGMA table_info(files)")]
            if "product_id" not in cols:
                self.conn.execute("ALTER TABLE files ADD COLUMN product_id TEXT")
            if "dxf_role" not in cols:
                self.conn.execute("ALTER TABLE files ADD COLUMN dxf_role TEXT")
            if "match_saved" not in cols:
                self.conn.execute(
                    "ALTER TABLE files ADD COLUMN match_saved INTEGER NOT NULL DEFAULT 0"
                )
            if "selected_layers" not in cols:
                self.conn.execute("ALTER TABLE files ADD COLUMN selected_layers TEXT")
            if "frontside_rect" not in cols:
                self.conn.execute("ALTER TABLE files ADD COLUMN frontside_rect TEXT")
            if "bottomside_rect" not in cols:
                self.conn.execute("ALTER TABLE files ADD COLUMN bottomside_rect TEXT")
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_files_product ON files(product_id)"
            )
            # A product can have at most one file per role.
            self.conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_files_product_role "
                "ON files(product_id, dxf_role) "
                "WHERE product_id IS NOT NULL AND dxf_role IS NOT NULL"
            )

    # ---- writes -----------------------------------------------------------
    def register(
        self,
        file_id: str,
        name: str,
        size: int,
        library_id: str = "default",
        product_id: str | None = None,
        dxf_role: str | None = None,
        initial_status: str = PREPROCESSING,
    ) -> FileRecord:
        rec = FileRecord(
            id=file_id, name=name, size=size,
            uploaded_at=time.time(), status=initial_status,
            library_id=library_id,
            product_id=product_id, dxf_role=dxf_role,
        )
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO files "
                "(id, name, size, uploaded_at, status, library_id, product_id, dxf_role, match_saved) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)",
                (rec.id, rec.name, rec.size, rec.uploaded_at, rec.status,
                 rec.library_id, rec.product_id, rec.dxf_role),
            )
        return rec

    def update_library(self, file_id: str, library_id: str) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE files SET library_id = ? WHERE id = ?",
                (library_id, file_id),
            )

    def update_selected_layers(self, file_id: str, layers: list[str]) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE files SET selected_layers = ? WHERE id = ?",
                (_json.dumps(list(layers)), file_id),
            )

    def clear_selected_layers(self, file_id: str) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE files SET selected_layers = NULL WHERE id = ?",
                (file_id,),
            )

    def update_side_regions(
        self,
        file_id: str,
        frontside_rect: dict | None,
        bottomside_rect: dict | None,
    ) -> None:
        front = _json.dumps(frontside_rect) if frontside_rect else None
        bottom = _json.dumps(bottomside_rect) if bottomside_rect else None
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE files SET frontside_rect = ?, bottomside_rect = ? WHERE id = ?",
                (front, bottom, file_id),
            )

    def clear_side_regions(self, file_id: str) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE files SET frontside_rect = NULL, bottomside_rect = NULL WHERE id = ?",
                (file_id,),
            )

    def set_match_saved(self, file_id: str, value: bool = True) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE files SET match_saved = ? WHERE id = ?",
                (1 if value else 0, file_id),
            )

    def list_by_product(self, product_id: str) -> list[FileRecord]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM files WHERE product_id = ? ORDER BY dxf_role",
                (product_id,),
            ).fetchall()
        return [_row_to_record(r) for r in rows]

    def update_status(self, file_id: str, status: str, error: str | None = None) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE files SET status = ?, error = ? WHERE id = ?",
                (status, error, file_id),
            )

    def update_parsed(
        self,
        file_id: str,
        primitive_count: int,
        bbox: tuple[float, float, float, float],
        background: str,
    ) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE files SET status = ?, parsed_at = ?, primitive_count = ?, "
                "bbox_xmin = ?, bbox_ymin = ?, bbox_xmax = ?, bbox_ymax = ?, "
                "background = ?, error = NULL WHERE id = ?",
                (READY, time.time(), primitive_count,
                 bbox[0], bbox[1], bbox[2], bbox[3], background, file_id),
            )

    # ---- reads ------------------------------------------------------------
    def get(self, file_id: str) -> FileRecord | None:
        with self.lock:
            row = self.conn.execute(
                "SELECT * FROM files WHERE id = ?", (file_id,)
            ).fetchone()
        return _row_to_record(row) if row else None

    def list_all(self) -> list[FileRecord]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM files ORDER BY uploaded_at DESC"
            ).fetchall()
        return [_row_to_record(r) for r in rows]


def _row_to_record(row: sqlite3.Row) -> FileRecord:
    bbox = None
    if row["bbox_xmin"] is not None:
        bbox = (row["bbox_xmin"], row["bbox_ymin"], row["bbox_xmax"], row["bbox_ymax"])
    try:
        library_id = row["library_id"]
    except (IndexError, KeyError):
        library_id = "default"
    def _get(col, default=None):
        try: return row[col]
        except (IndexError, KeyError): return default
    raw_layers = _get("selected_layers")
    selected_layers: list[str] | None = None
    if raw_layers:
        try:
            parsed = _json.loads(raw_layers)
            if isinstance(parsed, list):
                selected_layers = [str(x) for x in parsed]
        except (ValueError, TypeError):
            selected_layers = None

    def _decode_rect(raw: object) -> dict | None:
        if not raw:
            return None
        try:
            parsed = _json.loads(raw)
        except (ValueError, TypeError):
            return None
        if not isinstance(parsed, dict):
            return None
        try:
            return {
                "x0": float(parsed["x0"]),
                "y0": float(parsed["y0"]),
                "x1": float(parsed["x1"]),
                "y1": float(parsed["y1"]),
            }
        except (KeyError, TypeError, ValueError):
            return None

    frontside_rect = _decode_rect(_get("frontside_rect"))
    bottomside_rect = _decode_rect(_get("bottomside_rect"))
    return FileRecord(
        id=row["id"],
        name=row["name"],
        size=row["size"],
        uploaded_at=row["uploaded_at"],
        status=row["status"],
        library_id=library_id or "default",
        product_id=_get("product_id"),
        dxf_role=_get("dxf_role"),
        match_saved=bool(_get("match_saved", 0)),
        error=row["error"],
        parsed_at=row["parsed_at"],
        primitive_count=row["primitive_count"],
        bbox=bbox,
        background=row["background"],
        selected_layers=selected_layers,
        frontside_rect=frontside_rect,
        bottomside_rect=bottomside_rect,
    )


# Module-level singleton.
FILE_STORE = FileStore()
