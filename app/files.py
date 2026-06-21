"""DXF file metadata store, persisted alongside the template library.

Versioned model (2026-06-10): the `files` table is pure content-addressed
storage — one row per unique DXF bytes (id = content hash), shared by any
number of version bindings with zero byte duplication. Everything
lifecycle- or interpretation-related lives on the `version_files`
junction: a binding of one file into one version's role, carrying the
processing status, the layer selection, view rects, unit override, and
parse outputs — because two versions sharing the same bytes may select
different layers and therefore parse differently.

    discovering_layers → awaiting_layers → preprocessing → ready_to_match
                                                       → error

The actual DXF bytes and the parsed JSON live on disk under
data/uploads/{file_id}.dxf (shared) and data/parsed/{version_id}/{file_id}.json
(per binding) respectively (see `app.storage`).
"""

from __future__ import annotations

import json as _json
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from app import db
from app.dbschema import ensure_versioned_schema
from app.storage import DB_PATH

logger = logging.getLogger(__name__)

# Lifecycle states. The visible-to-user pipeline is:
#   discovering_layers → awaiting_layers → preprocessing → ready_to_match
#                                                     → checking_rules → report
# (error short-circuits from anywhere.)
DISCOVERING_LAYERS = "discovering_layers"
# Phase 0 gate: the file's geometry lives in paper-space layouts and more
# than one layout has content, so the operator must pick which AutoCAD tab
# to load before layer discovery can proceed. Single-layout / model-space
# files never enter this state (auto-resolution handles them). See
# `app.jobs._discover_layers_worker`.
AWAITING_LAYOUT = "awaiting_layout"
AWAITING_LAYERS = "awaiting_layers"
PREPROCESSING = "preprocessing"
READY = "ready_to_match"
CHECKING = "checking_rules"
REPORT = "report"
ERROR = "error"

ALL_STATUSES = (
    DISCOVERING_LAYERS,
    AWAITING_LAYOUT,
    AWAITING_LAYERS,
    PREPROCESSING,
    READY,
    CHECKING,
    REPORT,
    ERROR,
)


FILES_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    size              INTEGER NOT NULL,
    uploaded_at       REAL NOT NULL,
    -- Raw `$INSUNITS` header value from the source DXF; content property,
    -- populated on first parse of the bytes under any version.
    insunits          INTEGER,
    -- Summary of ezdxf's recover audit, populated only when strict mode
    -- rejected the file and recover saved it. Content-deterministic.
    dxf_recover_notes TEXT
);

CREATE TABLE IF NOT EXISTS version_files (
    version_id      TEXT NOT NULL,
    role            TEXT NOT NULL,
    file_id         TEXT NOT NULL REFERENCES files(id),
    -- 'multi' (default): per-view geometry derives from in-DXF region
    -- rects. 'top' / 'bottom' / 'side': the whole file is that view.
    dxf_view        TEXT,
    status          TEXT NOT NULL,
    error           TEXT,
    match_saved     INTEGER NOT NULL DEFAULT 0,
    selected_layers TEXT,
    top_view_rect    TEXT,
    bottom_view_rect TEXT,
    side_view_rect   TEXT,
    -- Multiplier the preprocessor applied for THIS binding (unit override
    -- is per version, so the scale is too).
    applied_scale   REAL NOT NULL DEFAULT 1.0,
    user_unit_override TEXT,
    chosen_layout   TEXT,
    parsed_at       REAL,
    primitive_count INTEGER,
    bbox_xmin       REAL,
    bbox_ymin       REAL,
    bbox_xmax       REAL,
    bbox_ymax       REAL,
    background      TEXT,
    created_at      REAL NOT NULL,
    PRIMARY KEY (version_id, file_id)
);

CREATE INDEX IF NOT EXISTS idx_vf_version ON version_files(version_id);
CREATE INDEX IF NOT EXISTS idx_vf_file ON version_files(file_id);
CREATE INDEX IF NOT EXISTS idx_vf_version_role ON version_files(version_id, role);
CREATE INDEX IF NOT EXISTS idx_files_uploaded_at ON files(uploaded_at);
"""


@dataclass
class FileRecord:
    """One binding of a content-addressed file into a version's role —
    the unit every viewer/job/API call operates on. `id` is the content
    hash (shared across versions); `version_id` scopes the state."""
    id: str
    version_id: str
    name: str
    size: int
    uploaded_at: float
    status: str
    dxf_role: str | None = None
    dxf_view: str | None = None
    match_saved: bool = False
    error: str | None = None
    parsed_at: float | None = None
    primitive_count: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    background: str | None = None
    # User-chosen layer subset, persisted between phase 1 and phase 2 and
    # reused on re-preprocess. None = "all layers". Per binding.
    selected_layers: list[str] | None = None
    # Per-binding top/bottom/side view rectangles, world coords,
    # axis-aligned, normalised so x0<=x1, y0<=y1.
    top_view_rect: dict | None = None
    bottom_view_rect: dict | None = None
    side_view_rect: dict | None = None
    insunits: int | None = None
    applied_scale: float = 1.0
    user_unit_override: str | None = None
    dxf_recover_notes: dict | None = None
    chosen_layout: str | None = None
    bound_at: float | None = None

    def to_dict(self) -> dict:
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
            "version_id": self.version_id,
            "name": self.name,
            "size": self.size,
            "uploaded_at": self.uploaded_at,
            "status": self.status,
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
            "unit_label": unit_display(self.insunits, self.user_unit_override)[0],
            "unit_is_mm": unit_display(self.insunits, self.user_unit_override)[1],
            "unit_scale_warning": kind,
            "unit_scale_warning_detail": detail if (kind or self.applied_scale != 1.0) else None,
            "applied_scale_label": (
                format_applied_scale_label(self.applied_scale, self.insunits)
                if self.applied_scale != 1.0 else None
            ),
            "user_unit_override": self.user_unit_override,
            "dxf_recover_notes": self.dxf_recover_notes,
            "chosen_layout": self.chosen_layout,
        }


# Unit-scale warning — DISABLED. Every file is treated as mm as-authored
# (see `app.dxf.detect_scale_factor`), so there is nothing to warn about;
# this always returns `(None, "")`. Kept as a pure function with its old
# signature so `to_dict` and the dashboard need no changes. Removed the
# diagonal/INSUNITS heuristic on 2026-06-09 along with auto-rescale.
def compute_unit_scale_warning(
    insunits: int | None,
    bbox: tuple[float, float, float, float] | None,
) -> tuple[str | None, str]:
    return None, ""


# Map INSUNITS → short human label. Used for the rescale pill text and the
# dashboard's per-file unit badge.
_INSUNITS_LABELS = {
    0: "unitless",
    1: "inch",
    2: "foot",
    4: "mm",
    5: "cm",
    6: "m",
}


def unit_display(
    insunits: int | None, user_unit_override: str | None,
) -> tuple[str, bool]:
    """Resolve the unit label shown on the dashboard, plus whether it is mm.

    An operator unit-override is authoritative (it is the source unit the
    human declared and that we rescaled from). Otherwise the label comes
    from the source DXF's `$INSUNITS`. `None`/unmapped values render as
    `"未指定"` (unspecified) since we cannot prove they are mm.

    The bool is `True` only for an exact mm match — the dashboard raises a
    "not mm" warning when it is `False` and the file was not rescaled."""
    if user_unit_override:
        label = user_unit_override
    elif insunits is None:
        label = "未指定"
    else:
        label = _INSUNITS_LABELS.get(insunits, "未指定")
    return label, label == "mm"


def format_applied_scale_label(applied_scale: float, insunits: int | None) -> str:
    """Short pill text for the dashboard, e.g. `"÷1000"`, `"×25.4 (inch)"`.

    Powers of 10 render with the `÷` / `×` shorthand; non-power factors
    (currently only 25.4 from inch) render with `×` and the source unit
    in parentheses."""
    if applied_scale == 1.0:
        return ""
    import math as _math
    log10 = _math.log10(applied_scale)
    if _math.isclose(log10, round(log10), abs_tol=1e-9):
        n = 10 ** int(round(abs(log10)))
        if applied_scale < 1.0:
            return f"÷{n}"
        return f"×{n}"
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


_BINDING_COLS = (
    "vf.version_id, vf.role, vf.file_id, vf.dxf_view, vf.status, vf.error, "
    "vf.match_saved, vf.selected_layers, vf.top_view_rect, vf.bottom_view_rect, "
    "vf.side_view_rect, vf.applied_scale, vf.user_unit_override, "
    "vf.chosen_layout, vf.parsed_at, vf.primitive_count, "
    "vf.bbox_xmin, vf.bbox_ymin, vf.bbox_xmax, vf.bbox_ymax, vf.background, "
    "vf.created_at AS bound_at, "
    "f.name, f.size, f.uploaded_at, f.insunits, f.dxf_recover_notes"
)

_BINDING_SELECT = (
    f"SELECT {_BINDING_COLS} FROM version_files vf "
    "JOIN files f ON f.id = vf.file_id "
)


class FileStore:
    """Thread-safe SQLite-backed file content + version-binding metadata."""

    def __init__(self, path: Path | str = DB_PATH) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        ensure_versioned_schema(path)
        self.conn = db.connect(path)
        self.lock = threading.RLock()
        with self.lock, self.conn:
            self.conn.executescript(FILES_SCHEMA)

    # ---- content rows ------------------------------------------------------
    def register_content(self, file_id: str, name: str, size: int) -> None:
        """Idempotent: content rows are keyed by hash, so a re-upload of
        identical bytes is a no-op (first uploader's filename wins)."""
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO files (id, name, size, uploaded_at) "
                "VALUES (?, ?, ?, ?)",
                (file_id, name, size, time.time()),
            )

    def content_exists(self, file_id: str) -> bool:
        with self.lock:
            row = self.conn.execute(
                "SELECT 1 FROM files WHERE id = ?", (file_id,)
            ).fetchone()
        return row is not None

    def set_insunits(self, file_id: str, insunits: int | None) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE files SET insunits = ? WHERE id = ?",
                (insunits, file_id),
            )

    def set_dxf_recover_notes(self, file_id: str, notes: dict | None) -> None:
        """Persist the recover-fallback audit summary on the content row.
        Pass `None` to clear (file parsed cleanly via strict)."""
        raw = _json.dumps(notes) if notes is not None else None
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE files SET dxf_recover_notes = ? WHERE id = ?",
                (raw, file_id),
            )

    def binding_count(self, file_id: str) -> int:
        """How many versions reference this content row (for safe-delete
        decisions on the uploads/ blob)."""
        with self.lock:
            row = self.conn.execute(
                "SELECT COUNT(*) AS n FROM version_files WHERE file_id = ?",
                (file_id,),
            ).fetchone()
        return int(row["n"])

    # ---- bindings ----------------------------------------------------------
    def bind(
        self,
        version_id: str,
        role: str,
        file_id: str,
        *,
        dxf_view: str | None = None,
        initial_status: str = PREPROCESSING,
    ) -> FileRecord:
        """Create (or replace) this version's binding of the file."""
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO version_files "
                "(version_id, role, file_id, dxf_view, status, match_saved, created_at) "
                "VALUES (?, ?, ?, ?, ?, 0, ?)",
                (version_id, role, file_id, dxf_view, initial_status, time.time()),
            )
        rec = self.get(version_id, file_id)
        assert rec is not None
        return rec

    def unbind(self, version_id: str, file_id: str) -> bool:
        with self.lock, self.conn:
            cur = self.conn.execute(
                "DELETE FROM version_files WHERE version_id = ? AND file_id = ?",
                (version_id, file_id),
            )
            return cur.rowcount > 0

    def unbind_role(self, version_id: str, role: str) -> int:
        with self.lock, self.conn:
            cur = self.conn.execute(
                "DELETE FROM version_files WHERE version_id = ? AND role = ?",
                (version_id, role),
            )
            return cur.rowcount

    # ---- binding state writes ----------------------------------------------
    def update_selected_layers(
        self, version_id: str, file_id: str, layers: list[str],
    ) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE version_files SET selected_layers = ? "
                "WHERE version_id = ? AND file_id = ?",
                (_json.dumps(list(layers)), version_id, file_id),
            )

    def clear_selected_layers(self, version_id: str, file_id: str) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE version_files SET selected_layers = NULL "
                "WHERE version_id = ? AND file_id = ?",
                (version_id, file_id),
            )

    def update_side_regions(
        self,
        version_id: str,
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
                "UPDATE version_files SET top_view_rect = ?, bottom_view_rect = ?, "
                "side_view_rect = ? WHERE version_id = ? AND file_id = ?",
                (top, bottom, side, version_id, file_id),
            )

    def clear_side_regions(self, version_id: str, file_id: str) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE version_files SET top_view_rect = NULL, "
                "bottom_view_rect = NULL, side_view_rect = NULL "
                "WHERE version_id = ? AND file_id = ?",
                (version_id, file_id),
            )

    def set_user_unit_override(
        self, version_id: str, file_id: str, unit: str | None,
    ) -> None:
        """Persist or clear the operator's unit-override choice for this
        binding. The caller validates `unit` against the allowlist."""
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE version_files SET user_unit_override = ? "
                "WHERE version_id = ? AND file_id = ?",
                (unit, version_id, file_id),
            )

    def set_chosen_layout(
        self, version_id: str, file_id: str, layout: str | None,
    ) -> None:
        """Persist or clear which AutoCAD tab this binding renders from.
        `None` clears back to the modelspace default."""
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE version_files SET chosen_layout = ? "
                "WHERE version_id = ? AND file_id = ?",
                (layout, version_id, file_id),
            )

    def set_match_saved(
        self, version_id: str, file_id: str, value: bool = True,
    ) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE version_files SET match_saved = ? "
                "WHERE version_id = ? AND file_id = ?",
                (1 if value else 0, version_id, file_id),
            )

    def update_status(
        self, version_id: str, file_id: str, status: str, error: str | None = None,
    ) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE version_files SET status = ?, error = ? "
                "WHERE version_id = ? AND file_id = ?",
                (status, error, version_id, file_id),
            )

    def update_parsed(
        self,
        version_id: str,
        file_id: str,
        primitive_count: int,
        bbox: tuple[float, float, float, float],
        background: str,
        insunits: int | None = None,
        applied_scale: float = 1.0,
    ) -> bool:
        """Update preprocess outputs on the binding (insunits lands on the
        content row). Returns True iff `applied_scale` actually changed
        (caller invalidates Match JSON in that case)."""
        with self.lock, self.conn:
            prior_row = self.conn.execute(
                "SELECT applied_scale FROM version_files "
                "WHERE version_id = ? AND file_id = ?",
                (version_id, file_id),
            ).fetchone()
            prior = float(prior_row["applied_scale"]) if prior_row else 1.0
            self.conn.execute(
                "UPDATE version_files SET status = ?, parsed_at = ?, "
                "primitive_count = ?, "
                "bbox_xmin = ?, bbox_ymin = ?, bbox_xmax = ?, bbox_ymax = ?, "
                "background = ?, applied_scale = ?, error = NULL "
                "WHERE version_id = ? AND file_id = ?",
                (READY, time.time(), primitive_count,
                 bbox[0], bbox[1], bbox[2], bbox[3], background,
                 float(applied_scale), version_id, file_id),
            )
            self.conn.execute(
                "UPDATE files SET insunits = ? WHERE id = ?",
                (insunits, file_id),
            )
        return prior != float(applied_scale)

    # ---- reads ------------------------------------------------------------
    def get(self, version_id: str, file_id: str) -> FileRecord | None:
        with self.lock:
            row = self.conn.execute(
                _BINDING_SELECT + "WHERE vf.version_id = ? AND vf.file_id = ?",
                (version_id, file_id),
            ).fetchone()
        return _row_to_record(row) if row else None

    def list_by_version(self, version_id: str) -> list[FileRecord]:
        with self.lock:
            rows = self.conn.execute(
                _BINDING_SELECT + "WHERE vf.version_id = ? ORDER BY vf.role",
                (version_id,),
            ).fetchall()
        return [_row_to_record(r) for r in rows]

    def list_bindings_of_file(self, file_id: str) -> list[FileRecord]:
        with self.lock:
            rows = self.conn.execute(
                _BINDING_SELECT + "WHERE vf.file_id = ? ORDER BY vf.created_at",
                (file_id,),
            ).fetchall()
        return [_row_to_record(r) for r in rows]

    def list_all(self) -> list[FileRecord]:
        """Every binding (not every content row) — the operational unit."""
        with self.lock:
            rows = self.conn.execute(
                _BINDING_SELECT + "ORDER BY f.uploaded_at DESC"
            ).fetchall()
        return [_row_to_record(r) for r in rows]


def _row_to_record(row: db.Row) -> FileRecord:
    bbox = None
    if row["bbox_xmin"] is not None:
        bbox = (row["bbox_xmin"], row["bbox_ymin"], row["bbox_xmax"], row["bbox_ymax"])

    def _get(col, default=None):
        try:
            return row[col]
        except (IndexError, KeyError):
            return default

    raw_layers = _get("selected_layers")
    selected_layers: list[str] | None = None
    if raw_layers:
        try:
            parsed = _json.loads(raw_layers)
            if isinstance(parsed, list):
                selected_layers = [str(x) for x in parsed]
        except (ValueError, TypeError):
            # DB JSON should never be malformed; if it is, "all layers" is a
            # silent, hard-to-diagnose behaviour change — flag it.
            logger.warning(
                "corrupt JSON in files.selected_layers for file=%s; "
                "treating as null (all layers)", _get("file_id"),
            )
            selected_layers = None

    def _decode_rect(raw: object) -> dict | None:
        if not raw:
            return None
        try:
            parsed = _json.loads(raw)
        except (ValueError, TypeError):
            logger.warning("corrupt JSON in a files side-region rect for "
                           "file=%s; treating as null", _get("file_id"))
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

    raw_notes = _get("dxf_recover_notes")
    dxf_recover_notes: dict | None = None
    if raw_notes:
        try:
            parsed_notes = _json.loads(raw_notes)
            if isinstance(parsed_notes, dict):
                dxf_recover_notes = parsed_notes
        except (ValueError, TypeError):
            logger.warning("corrupt JSON in files.dxf_recover_notes for "
                           "file=%s; treating as null", _get("file_id"))
            dxf_recover_notes = None

    return FileRecord(
        id=row["file_id"],
        version_id=row["version_id"],
        name=row["name"],
        size=row["size"],
        uploaded_at=row["uploaded_at"],
        status=row["status"],
        dxf_role=_get("role"),
        dxf_view=_get("dxf_view"),
        match_saved=bool(_get("match_saved", 0)),
        error=row["error"],
        parsed_at=row["parsed_at"],
        primitive_count=row["primitive_count"],
        bbox=bbox,
        background=row["background"],
        selected_layers=selected_layers,
        top_view_rect=_decode_rect(_get("top_view_rect")),
        bottom_view_rect=_decode_rect(_get("bottom_view_rect")),
        side_view_rect=_decode_rect(_get("side_view_rect")),
        insunits=_get("insunits"),
        applied_scale=float(_get("applied_scale", 1.0) or 1.0),
        user_unit_override=_get("user_unit_override"),
        dxf_recover_notes=dxf_recover_notes,
        chosen_layout=_get("chosen_layout"),
        bound_at=_get("bound_at"),
    )


# Module-level singleton.
FILE_STORE = FileStore()
