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
    dxf_view        TEXT,
    match_saved     INTEGER NOT NULL DEFAULT 0,
    selected_layers TEXT,
    top_view_rect    TEXT,
    bottom_view_rect TEXT,
    side_view_rect   TEXT,
    insunits        INTEGER,
    applied_scale   REAL NOT NULL DEFAULT 1.0,
    -- Operator-chosen unit interpretation, set from the viewer's unit
    -- picker. One of 'mm' | 'cm' | 'm' | 'inch' | 'μm', or NULL when
    -- the auto-rescale detector has authority. See
    -- `app.dxf.UNIT_TO_SCALE` for the multiplier table.
    user_unit_override TEXT
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
    # 'multi' (the default): per-view geometry derives from in-DXF region
    # rects. 'top' / 'bottom' / 'side': the whole file represents that view;
    # region rects MUST be NULL. None on rows that aren't bound to a product.
    dxf_view: str | None = None
    match_saved: bool = False
    error: str | None = None
    parsed_at: float | None = None
    primitive_count: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    background: str | None = None
    # User-chosen layer subset, persisted between phase 1 and phase 2 and
    # reused on re-preprocess. None = legacy (treat as "all layers").
    selected_layers: list[str] | None = None
    # Per-file top/bottom/side view rectangles, world coords, axis-aligned,
    # normalised so x0<=x1, y0<=y1. None = view not marked. Any subset of
    # the three may be set.
    top_view_rect: dict | None = None
    bottom_view_rect: dict | None = None
    side_view_rect: dict | None = None
    # Raw `$INSUNITS` header value from the source DXF (None for legacy
    # rows uploaded before this column existed; re-preprocess to populate).
    # 0 = unitless, 1 = inch, 2 = foot, 4 = mm, 5 = cm, 6 = m, …
    insunits: int | None = None
    # Multiplier the preprocessor applied to bring this file's coordinates
    # into mm. `rescaled_coord = original_coord * applied_scale`. `1.0`
    # means "no rescale". See `app.dxf.detect_scale_factor`.
    applied_scale: float = 1.0
    # Operator-chosen unit interpretation from the viewer's unit picker.
    # When set, the preprocessor uses `app.dxf.UNIT_TO_SCALE[value]` as
    # the multiplier and skips the detector. `None` means detector has
    # authority. The viewer's "set by you" badge keys off this field.
    user_unit_override: str | None = None

    def to_dict(self) -> dict:
        # When the preprocessor applied a rescale, the persisted bbox is
        # already in mm — so feed the heuristic the *pre-rescale* diagonal
        # (current diagonal / applied_scale) so the warning kind stays
        # stable regardless of auto-rescale.
        pre_bbox = self.bbox
        if pre_bbox is not None and self.applied_scale != 1.0 and self.applied_scale > 0:
            inv = 1.0 / self.applied_scale
            pre_bbox = tuple(c * inv for c in self.bbox)
        kind, detail = compute_unit_scale_warning(self.insunits, pre_bbox)
        if self.applied_scale != 1.0:
            detail = _format_rescale_detail(
                self.insunits, pre_bbox, self.applied_scale,
            )
        return {
            "id": self.id,
            "name": self.name,
            "size": self.size,
            "uploaded_at": self.uploaded_at,
            "status": self.status,
            "library_id": self.library_id,
            "product_id": self.product_id,
            "dxf_role": self.dxf_role,
            "dxf_view": self.dxf_view,
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
            "top_view_rect": dict(self.top_view_rect) if self.top_view_rect else None,
            "bottom_view_rect": dict(self.bottom_view_rect) if self.bottom_view_rect else None,
            "side_view_rect": dict(self.side_view_rect) if self.side_view_rect else None,
            "insunits": self.insunits,
            "applied_scale": self.applied_scale,
            "unit_scale_warning": kind,
            "unit_scale_warning_detail": detail if (kind or self.applied_scale != 1.0) else None,
            "applied_scale_label": (
                format_applied_scale_label(self.applied_scale, self.insunits)
                if self.applied_scale != 1.0 else None
            ),
            "user_unit_override": self.user_unit_override,
        }


# Unit-scale heuristic. Returns (kind, detail) — kind is None when the
# file looks fine. Pure function for testability and so the dashboard
# can render directly off the dict without re-implementing the rule.
# Thresholds (drawing units):
#   ≤ 100         — small enough that "unitless" is academic
#   100..1000     — plausible packaging range
#   > 1000        — suspect for a packaging file
def compute_unit_scale_warning(
    insunits: int | None,
    bbox: tuple[float, float, float, float] | None,
) -> tuple[str | None, str]:
    if not bbox:
        return None, ""
    xmin, ymin, xmax, ymax = bbox
    dx = float(xmax) - float(xmin)
    dy = float(ymax) - float(ymin)
    if dx <= 0 and dy <= 0:
        return None, ""
    import math as _math
    diagonal = _math.hypot(max(dx, 0.0), max(dy, 0.0))
    iu = "None" if insunits is None else str(insunits)
    base = f"INSUNITS={iu}, diagonal={diagonal:.1f}"
    declared = insunits in (4, 5, 6)  # mm / cm / m
    if diagonal > 1000:
        return "suspect_scale", f"{base} — bbox is huge for a packaging design, likely a 1000×-style scale issue"
    if insunits == 0:
        if diagonal > 100:
            return "suspect_scale", f"{base} — declared unitless and bbox is large; treat as suspect"
        return "unitless", f"{base} — DXF is declared unitless ($INSUNITS=0)"
    # diagonal ≤ 1000, declared unit (or unknown) → no warning.
    return None, ""


# Map INSUNITS → short human label, used only for the rescale pill text.
_INSUNITS_LABELS = {
    0: "unitless",
    1: "inch",
    2: "foot",
    4: "mm",
    5: "cm",
    6: "m",
}


def format_applied_scale_label(applied_scale: float, insunits: int | None) -> str:
    """Short pill text for the dashboard, e.g. `"÷1000"`, `"×25.4 (inch)"`.

    Powers of 10 render with the `÷` / `×` shorthand; non-power factors
    (currently only 25.4 from inch) render with `×` and the source unit
    in parentheses."""
    if applied_scale == 1.0:
        return ""
    # Power-of-10 case: render as ÷N (when M < 1) or ×N (when M > 1) to
    # match the user's mental model of "the file was N× too big / small."
    import math as _math
    log10 = _math.log10(applied_scale)
    if _math.isclose(log10, round(log10), abs_tol=1e-9):
        n = 10 ** int(round(abs(log10)))
        if applied_scale < 1.0:
            return f"÷{n}"
        return f"×{n}"
    # Non-power factor (declared inch → ×25.4). Append the source unit for
    # discoverability so the user sees why we did it.
    label = _INSUNITS_LABELS.get(insunits or -1)
    suffix = f" ({label})" if label else ""
    return f"×{applied_scale:g}{suffix}"


def _format_rescale_detail(
    insunits: int | None,
    pre_bbox: tuple[float, float, float, float] | None,
    applied_scale: float,
) -> str:
    """Full detail text shown in the dashboard pill's `title`."""
    iu_label = _INSUNITS_LABELS.get(insunits or -1, "unknown")
    iu_token = "None" if insunits is None else f"{insunits} ({iu_label})"
    pill = format_applied_scale_label(applied_scale, insunits)
    if pre_bbox:
        import math as _math
        dx = float(pre_bbox[2]) - float(pre_bbox[0])
        dy = float(pre_bbox[3]) - float(pre_bbox[1])
        diag = _math.hypot(max(dx, 0.0), max(dy, 0.0))
        return (
            f"INSUNITS={iu_token}, pre-rescale diagonal={diag:.1f} "
            f"→ auto-rescaled {pill} (mm)"
        )
    return f"INSUNITS={iu_token} → auto-rescaled {pill} (mm)"


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
            if "dxf_view" not in cols:
                self.conn.execute("ALTER TABLE files ADD COLUMN dxf_view TEXT")
                # Backfill: every existing product-bound row was implicitly
                # 'multi' under the old single-file-per-role model.
                self.conn.execute(
                    "UPDATE files SET dxf_view = 'multi' "
                    "WHERE product_id IS NOT NULL AND dxf_role IS NOT NULL "
                    "AND dxf_view IS NULL"
                )
            if "match_saved" not in cols:
                self.conn.execute(
                    "ALTER TABLE files ADD COLUMN match_saved INTEGER NOT NULL DEFAULT 0"
                )
            if "selected_layers" not in cols:
                self.conn.execute("ALTER TABLE files ADD COLUMN selected_layers TEXT")
            # Side-region columns: rename frontside_rect → top_view_rect and
            # bottomside_rect → bottom_view_rect (SQLite ≥ 3.25 supports
            # RENAME COLUMN; bundled Python sqlite3 is well past that). Then
            # add the new side_view_rect column. Each branch is idempotent:
            # already-migrated DBs and fresh DBs hit the no-op path.
            if "frontside_rect" in cols and "top_view_rect" not in cols:
                self.conn.execute(
                    "ALTER TABLE files RENAME COLUMN frontside_rect TO top_view_rect"
                )
            elif "top_view_rect" not in cols:
                self.conn.execute("ALTER TABLE files ADD COLUMN top_view_rect TEXT")
            if "bottomside_rect" in cols and "bottom_view_rect" not in cols:
                self.conn.execute(
                    "ALTER TABLE files RENAME COLUMN bottomside_rect TO bottom_view_rect"
                )
            elif "bottom_view_rect" not in cols:
                self.conn.execute("ALTER TABLE files ADD COLUMN bottom_view_rect TEXT")
            if "side_view_rect" not in cols:
                self.conn.execute("ALTER TABLE files ADD COLUMN side_view_rect TEXT")
            if "insunits" not in cols:
                self.conn.execute("ALTER TABLE files ADD COLUMN insunits INTEGER")
            if "applied_scale" not in cols:
                # Legacy rows pre-date the auto-rescale feature; treat them
                # as untouched (factor = 1.0) until they are re-preprocessed.
                self.conn.execute(
                    "ALTER TABLE files ADD COLUMN applied_scale REAL NOT NULL DEFAULT 1.0"
                )
            if "user_unit_override" not in cols:
                # Legacy rows default to NULL (no override → detector
                # authority). The column is unconstrained at the DB level;
                # the API layer validates the enum.
                self.conn.execute(
                    "ALTER TABLE files ADD COLUMN user_unit_override TEXT"
                )
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_files_product ON files(product_id)"
            )
            # A (product, role) can hold multiple DXFs. View-coverage
            # uniqueness (each top/bottom/side region sourced by at most
            # one file) is enforced at write time by `app.product_views`
            # using the file rect columns. The DB-level unique constraints
            # from earlier iterations are obsolete and dropped here.
            self.conn.execute("DROP INDEX IF EXISTS idx_files_product_role")
            self.conn.execute("DROP INDEX IF EXISTS idx_files_product_role_view")
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_files_product_role "
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
        dxf_view: str | None = None,
        initial_status: str = PREPROCESSING,
    ) -> FileRecord:
        rec = FileRecord(
            id=file_id, name=name, size=size,
            uploaded_at=time.time(), status=initial_status,
            library_id=library_id,
            product_id=product_id, dxf_role=dxf_role, dxf_view=dxf_view,
        )
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO files "
                "(id, name, size, uploaded_at, status, library_id, product_id, "
                "dxf_role, dxf_view, match_saved) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
                (rec.id, rec.name, rec.size, rec.uploaded_at, rec.status,
                 rec.library_id, rec.product_id, rec.dxf_role, rec.dxf_view),
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
        top_view_rect: dict | None,
        bottom_view_rect: dict | None,
        side_view_rect: dict | None,
    ) -> None:
        top = _json.dumps(top_view_rect) if top_view_rect else None
        bottom = _json.dumps(bottom_view_rect) if bottom_view_rect else None
        side = _json.dumps(side_view_rect) if side_view_rect else None
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE files SET top_view_rect = ?, bottom_view_rect = ?, "
                "side_view_rect = ? WHERE id = ?",
                (top, bottom, side, file_id),
            )

    def clear_side_regions(self, file_id: str) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE files SET top_view_rect = NULL, bottom_view_rect = NULL, "
                "side_view_rect = NULL WHERE id = ?",
                (file_id,),
            )

    def set_user_unit_override(self, file_id: str, unit: str | None) -> None:
        """Persist or clear the operator's unit-override choice. The
        caller is responsible for validating `unit` against the
        five-string allowlist; this method accepts whatever it is given
        (or `None` to clear)."""
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE files SET user_unit_override = ? WHERE id = ?",
                (unit, file_id),
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
        insunits: int | None = None,
        applied_scale: float = 1.0,
    ) -> bool:
        """Update preprocess outputs on the file row. Returns True iff the
        `applied_scale` actually changed (caller invalidates Match JSON in
        that case — see `app/jobs.py` and the auto-rescale spec)."""
        with self.lock, self.conn:
            prior_row = self.conn.execute(
                "SELECT applied_scale FROM files WHERE id = ?", (file_id,)
            ).fetchone()
            prior = float(prior_row["applied_scale"]) if prior_row else 1.0
            self.conn.execute(
                "UPDATE files SET status = ?, parsed_at = ?, primitive_count = ?, "
                "bbox_xmin = ?, bbox_ymin = ?, bbox_xmax = ?, bbox_ymax = ?, "
                "background = ?, insunits = ?, applied_scale = ?, error = NULL "
                "WHERE id = ?",
                (READY, time.time(), primitive_count,
                 bbox[0], bbox[1], bbox[2], bbox[3], background, insunits,
                 float(applied_scale), file_id),
            )
        return prior != float(applied_scale)

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

    top_view_rect = _decode_rect(_get("top_view_rect"))
    bottom_view_rect = _decode_rect(_get("bottom_view_rect"))
    side_view_rect = _decode_rect(_get("side_view_rect"))
    return FileRecord(
        id=row["id"],
        name=row["name"],
        size=row["size"],
        uploaded_at=row["uploaded_at"],
        status=row["status"],
        library_id=library_id or "default",
        product_id=_get("product_id"),
        dxf_role=_get("dxf_role"),
        dxf_view=_get("dxf_view"),
        match_saved=bool(_get("match_saved", 0)),
        error=row["error"],
        parsed_at=row["parsed_at"],
        primitive_count=row["primitive_count"],
        bbox=bbox,
        background=row["background"],
        selected_layers=selected_layers,
        top_view_rect=top_view_rect,
        bottom_view_rect=bottom_view_rect,
        side_view_rect=side_view_rect,
        insunits=_get("insunits"),
        applied_scale=float(_get("applied_scale", 1.0) or 1.0),
        user_unit_override=_get("user_unit_override"),
    )


# Module-level singleton.
FILE_STORE = FileStore()
