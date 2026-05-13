"""DXF file metadata store, persisted alongside the template library.

Each uploaded DXF has a row tracking its lifecycle:
    queued → parsing → done   (happy path)
                    → error   (failed)

The actual DXF bytes and the parsed JSON live on disk under data/uploads/ and
data/parsed/ respectively (see `app.storage`). This module only manages the
SQLite-backed metadata.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from app.storage import DB_PATH

# Lifecycle states. The visible-to-user pipeline is:
#   preprocessing → ready_to_match → checking_rules → report
# (error short-circuits from anywhere.)
PREPROCESSING = "preprocessing"
READY = "ready_to_match"
CHECKING = "checking_rules"
REPORT = "report"
ERROR = "error"

ALL_STATUSES = (PREPROCESSING, READY, CHECKING, REPORT, ERROR)


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
    library_id      TEXT NOT NULL DEFAULT 'default'
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
    error: str | None = None
    parsed_at: float | None = None
    primitive_count: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    background: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "size": self.size,
            "uploaded_at": self.uploaded_at,
            "status": self.status,
            "library_id": self.library_id,
            "error": self.error,
            "parsed_at": self.parsed_at,
            "primitive_count": self.primitive_count,
            "bbox": list(self.bbox) if self.bbox else None,
            "background": self.background,
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

    # ---- writes -----------------------------------------------------------
    def register(self, file_id: str, name: str, size: int,
                 library_id: str = "default") -> FileRecord:
        rec = FileRecord(
            id=file_id, name=name, size=size,
            uploaded_at=time.time(), status=PREPROCESSING,
            library_id=library_id,
        )
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO files "
                "(id, name, size, uploaded_at, status, library_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (rec.id, rec.name, rec.size, rec.uploaded_at, rec.status, rec.library_id),
            )
        return rec

    def update_library(self, file_id: str, library_id: str) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE files SET library_id = ? WHERE id = ?",
                (library_id, file_id),
            )

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
    # `library_id` is added by migration; older rows may not have it (defaults).
    try:
        library_id = row["library_id"]
    except (IndexError, KeyError):
        library_id = "default"
    return FileRecord(
        id=row["id"],
        name=row["name"],
        size=row["size"],
        uploaded_at=row["uploaded_at"],
        status=row["status"],
        library_id=library_id or "default",
        error=row["error"],
        parsed_at=row["parsed_at"],
        primitive_count=row["primitive_count"],
        bbox=bbox,
        background=row["background"],
    )


# Module-level singleton.
FILE_STORE = FileStore()
