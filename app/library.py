"""Template libraries, persisted to SQLite.

A *template* is a geometric pattern (one or more entities' point clouds)
labelled with an object class. Templates live inside a *library* — a named
collection of classes + templates. Multiple libraries can coexist; each
uploaded DXF is bound to exactly one library.

Persistence:
- One SQLite file (`data/library.sqlite`) backs everything.
- `libraries(id PK, name, created_at)`
- `classes(library_id, name, rank, created_at)` PK = (library_id, name)
- `templates(id PK, library_id, class_name, …)`
- A `Library` instance is a per-library in-memory cache over the same store.
- `LibraryRegistry` owns the SQLite store and a dict of Library caches.
"""

from __future__ import annotations

import json
import math
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


# Canonical class list. Auto-seeded into every newly-created library.
# Order is also the display / toolbar / fold order — keep deliberate.
DEFAULT_CLASSES: list[str] = [
    "Substrate",
    "Pin-1",
    "Lid",
    "LidOuter",
    "LidInner",
    "DieArea",
    "FiducialCircle",
    "FiducialCross",
    "SMD-2T",
    "BGABall",
    "2DBarcode",
    "SMD-3T",
    "SMD-8T",
    "SMD-14T",
]


# Class IDs that are no longer seeded. The migration drops both their class
# row and any templates filed under them, so legacy DBs converge to the new
# default list without manual cleanup.
DEPRECATED_CLASSES: frozenset[str] = frozenset({"FiducialMark", "Side"})


# Display ID → match-JSON snake_case key. Display labels stay in their
# canonical form (BGABall, Pin-1, …); only the persisted JSON key uses the
# snake_case identifier downstream consumers (rule checker, exports) expect.
CLASS_JSON_KEY: dict[str, str] = {
    "Substrate":      "substrate",
    "Pin-1":          "pin_1",
    "Lid":            "lid",
    "LidOuter":       "lid_outer",
    "LidInner":       "lid_inner",
    "DieArea":        "die_area",
    "FiducialCircle": "fiducial_circle",
    "FiducialCross":  "fiducial_cross",
    "SMD-2T":         "smd_2t",
    "BGABall":        "bga_ball",
    "2DBarcode":      "2d_barcode",
    "SMD-3T":         "smd_3t",
    "SMD-8T":         "smd_8t",
    "SMD-14T":        "smd_14t",
}


# Legacy snake_case class names → new canonical IDs. Applied as a one-shot
# rename pass on every Store boot. Idempotent: after the first run all the
# WHERE clauses match zero rows, so subsequent boots are no-ops.
LEGACY_CLASS_RENAME: dict[str, str] = {
    "smd":           "SMD-2T",
    "substrate":     "Substrate",
    "die_area":      "DieArea",
    "lid_outer":     "LidOuter",
    "lid_inner":     "LidInner",
    "bga_ball":      "BGABall",
    "pin_mark":      "Pin-1",
    "2d_barcode":    "2DBarcode",
}


DEFAULT_LIBRARY_ID = "default"
DEFAULT_LIBRARY_NAME = "Default"


Point = tuple[float, float]


@dataclass
class Template:
    """A single template pattern stored as raw point sets per source entity."""

    id: str
    class_name: str
    entity_point_sets: list[list[Point]]
    centroid: tuple[float, float]
    bbox: tuple[float, float, float, float]
    # Per-entity primitive kind at commit time (e.g., "circle", "polyline").
    # Same length as entity_point_sets. None entries mean mixed-kind handle
    # or legacy row (template committed before the column was added).
    entity_kinds: list[str | None] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.entity_kinds is None:
            self.entity_kinds = [None] * len(self.entity_point_sets)
        elif len(self.entity_kinds) != len(self.entity_point_sets):
            raise ValueError(
                "entity_kinds length must equal entity_point_sets length"
            )

    @classmethod
    def from_entities(
        cls,
        class_name: str,
        entity_point_sets: list[list[Point]],
        entity_kinds: list[str | None] | None = None,
    ) -> "Template":
        if not entity_point_sets or all(len(e) == 0 for e in entity_point_sets):
            raise ValueError("template must have at least one point")
        all_pts = [p for ent in entity_point_sets for p in ent]
        xs = [p[0] for p in all_pts]; ys = [p[1] for p in all_pts]
        bbox = (min(xs), min(ys), max(xs), max(ys))
        cx = sum(xs) / len(xs); cy = sum(ys) / len(ys)
        return cls(
            id=str(uuid.uuid4()),
            class_name=class_name,
            entity_point_sets=entity_point_sets,
            centroid=(cx, cy),
            bbox=bbox,
            entity_kinds=(
                list(entity_kinds) if entity_kinds is not None
                else [None] * len(entity_point_sets)
            ),
        )


# ---- SQLite store ---------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS libraries (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS classes (
    library_id  TEXT NOT NULL,
    name        TEXT NOT NULL,
    rank        INTEGER NOT NULL,
    created_at  REAL NOT NULL,
    tolerance   REAL,
    PRIMARY KEY (library_id, name),
    FOREIGN KEY (library_id) REFERENCES libraries(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS templates (
    id                 TEXT PRIMARY KEY,
    library_id         TEXT NOT NULL,
    class_name         TEXT NOT NULL,
    entity_point_sets  TEXT NOT NULL,
    centroid_x         REAL NOT NULL,
    centroid_y         REAL NOT NULL,
    bbox_xmin          REAL NOT NULL,
    bbox_ymin          REAL NOT NULL,
    bbox_xmax          REAL NOT NULL,
    bbox_ymax          REAL NOT NULL,
    created_at         REAL NOT NULL,
    entity_kinds       TEXT,
    FOREIGN KEY (library_id) REFERENCES libraries(id) ON DELETE CASCADE
);
"""


class Store:
    """Thin SQLite wrapper. Single connection, RLock-protected writes."""

    def __init__(self, path: Path | str) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.lock = threading.RLock()
        with self.lock, self.conn:
            self.conn.executescript(SCHEMA)
            self._migrate()
            # Index created after migration so library_id column is guaranteed.
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_templates_lib_class ON templates(library_id, class_name)"
            )
            # Always ensure the default library exists.
            self.conn.execute(
                "INSERT OR IGNORE INTO libraries (id, name, created_at) VALUES (?, ?, ?)",
                (DEFAULT_LIBRARY_ID, DEFAULT_LIBRARY_NAME, time.time()),
            )

    # ---- migration from pre-multi-library schema -------------------------
    def _migrate(self) -> None:
        """Bring a pre-multi-library DB up to date.

        Detects whether `classes`/`templates` have the `library_id` column;
        if not, adds it and re-tags all rows with the default library.
        """
        def has_col(table: str, col: str) -> bool:
            rows = self.conn.execute(f"PRAGMA table_info({table})").fetchall()
            return any(r["name"] == col for r in rows)

        # templates.library_id — simple column add (ALTER doesn't take params).
        if not has_col("templates", "library_id"):
            self.conn.execute(
                f"ALTER TABLE templates ADD COLUMN library_id TEXT NOT NULL "
                f"DEFAULT '{DEFAULT_LIBRARY_ID}'"
            )

        # templates.entity_kinds — JSON-encoded parallel list of primitive
        # types (or None) captured at commit time. NULL on legacy rows; the
        # loader parses NULL as [None] * len(entity_point_sets) so existing
        # libraries keep working without the circle fast path.
        if not has_col("templates", "entity_kinds"):
            self.conn.execute(
                "ALTER TABLE templates ADD COLUMN entity_kinds TEXT"
            )

        # Pre-multi-library schema had templates.FOREIGN KEY(class_name)
        # → classes(name). After we changed classes' PK to (library_id, name)
        # that FK is unresolvable ("foreign key mismatch"). Rebuild templates
        # without that old FK.
        fk_list = self.conn.execute("PRAGMA foreign_key_list(templates)").fetchall()
        if any(fk["table"] == "classes" for fk in fk_list):
            self.conn.executescript("""
                CREATE TABLE templates__new (
                    id                 TEXT PRIMARY KEY,
                    library_id         TEXT NOT NULL,
                    class_name         TEXT NOT NULL,
                    entity_point_sets  TEXT NOT NULL,
                    centroid_x         REAL NOT NULL,
                    centroid_y         REAL NOT NULL,
                    bbox_xmin          REAL NOT NULL,
                    bbox_ymin          REAL NOT NULL,
                    bbox_xmax          REAL NOT NULL,
                    bbox_ymax          REAL NOT NULL,
                    created_at         REAL NOT NULL,
                    FOREIGN KEY (library_id) REFERENCES libraries(id) ON DELETE CASCADE
                );
                INSERT INTO templates__new
                    SELECT id, library_id, class_name, entity_point_sets,
                           centroid_x, centroid_y,
                           bbox_xmin, bbox_ymin, bbox_xmax, bbox_ymax, created_at
                    FROM templates;
                DROP TABLE templates;
                ALTER TABLE templates__new RENAME TO templates;
            """)

        # classes needs PK change (was: name; now: (library_id, name)). Rebuild.
        cls_cols = [r["name"] for r in self.conn.execute("PRAGMA table_info(classes)")]
        if cls_cols and "library_id" not in cls_cols:
            self.conn.executescript(f"""
                CREATE TABLE classes__new (
                    library_id  TEXT NOT NULL,
                    name        TEXT NOT NULL,
                    rank        INTEGER NOT NULL,
                    created_at  REAL NOT NULL,
                    tolerance   REAL,
                    PRIMARY KEY (library_id, name)
                );
                INSERT INTO classes__new (library_id, name, rank, created_at)
                    SELECT '{DEFAULT_LIBRARY_ID}', name, rank, created_at FROM classes;
                DROP TABLE classes;
                ALTER TABLE classes__new RENAME TO classes;
            """)

        # classes.tolerance — per-class chamfer override (NULL = use the
        # global TOLERANCE_ABS default in app/matching.py). Added late so
        # existing libraries get NULL on every row, preserving pre-change
        # matching behaviour.
        if not has_col("classes", "tolerance"):
            self.conn.execute(
                "ALTER TABLE classes ADD COLUMN tolerance REAL"
            )

        # Legacy snake_case class names → new canonical IDs. Rewrite both the
        # `classes` and `templates` tables in place. UPDATE OR IGNORE skips
        # rows that would collide with an already-existing (library_id, NEW)
        # row, and the trailing DELETE cleans up any such leftovers. Naturally
        # idempotent — once renamed, no rows match the old name.
        for old, new in LEGACY_CLASS_RENAME.items():
            self.conn.execute(
                "UPDATE templates SET class_name = ? WHERE class_name = ?",
                (new, old),
            )
            self.conn.execute(
                "UPDATE OR IGNORE classes SET name = ? WHERE name = ?",
                (new, old),
            )
            self.conn.execute(
                "DELETE FROM classes WHERE name = ?",
                (old,),
            )

        # Drop deprecated classes (and any templates filed under them) so
        # legacy DBs converge to the new DEFAULT_CLASSES set on boot.
        for dead in DEPRECATED_CLASSES:
            self.conn.execute("DELETE FROM templates WHERE class_name = ?", (dead,))
            self.conn.execute("DELETE FROM classes WHERE name = ?", (dead,))

        # Make sure every existing library carries the full DEFAULT_CLASSES set
        # before re-ranking, so newly-added defaults (e.g. FiducialCircle /
        # FiducialCross) slot into their canonical position rather than the
        # tail. INSERT OR IGNORE is a no-op for classes that already exist.
        lib_ids = [r["id"] for r in self.conn.execute("SELECT id FROM libraries")]
        now = time.time()
        for lib_id in lib_ids:
            for c in DEFAULT_CLASSES:
                self.conn.execute(
                    "INSERT OR IGNORE INTO classes (library_id, name, rank, created_at) "
                    "VALUES (?, ?, 0, ?)",
                    (lib_id, c, now),
                )

        # Re-rank classes per library so the toolbar order tracks the current
        # DEFAULT_CLASSES list. Anything not in DEFAULT_CLASSES (custom classes
        # added by the user) is pushed to the end, preserving relative order.
        rank_priority = {n: i for i, n in enumerate(DEFAULT_CLASSES)}
        rows = self.conn.execute(
            "SELECT library_id, name, rank, created_at FROM classes"
        ).fetchall()
        per_lib: dict[str, list[sqlite3.Row]] = {}
        for r in rows:
            per_lib.setdefault(r["library_id"], []).append(r)
        for lib_id, lib_rows in per_lib.items():
            lib_rows.sort(key=lambda r: (
                rank_priority.get(r["name"], len(DEFAULT_CLASSES)),
                r["rank"],
                r["created_at"],
            ))
            for new_rank, r in enumerate(lib_rows):
                self.conn.execute(
                    "UPDATE classes SET rank = ? WHERE library_id = ? AND name = ?",
                    (new_rank, lib_id, r["name"]),
                )

    # ---- library CRUD ----------------------------------------------------
    def create_library(self, library_id: str, name: str) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO libraries (id, name, created_at) VALUES (?, ?, ?)",
                (library_id, name, time.time()),
            )

    def delete_library(self, library_id: str) -> None:
        with self.lock, self.conn:
            self.conn.execute("DELETE FROM libraries WHERE id = ?", (library_id,))

    def list_libraries(self) -> list[sqlite3.Row]:
        with self.lock:
            return list(self.conn.execute(
                "SELECT id, name, created_at FROM libraries ORDER BY created_at ASC"
            ))

    def get_library(self, library_id: str) -> sqlite3.Row | None:
        with self.lock:
            return self.conn.execute(
                "SELECT id, name, created_at FROM libraries WHERE id = ?",
                (library_id,),
            ).fetchone()

    # ---- class / template writes (library-scoped) ------------------------
    def upsert_class(self, library_id: str, name: str) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO classes (library_id, name, rank, created_at) "
                "VALUES (?, ?, COALESCE((SELECT MAX(rank) + 1 FROM classes WHERE library_id = ?), 0), ?)",
                (library_id, name, library_id, time.time()),
            )

    def update_class_tolerance(
        self, library_id: str, name: str, tolerance: float | None
    ) -> bool:
        """Set or clear the per-class chamfer tolerance. Returns True when
        the (library_id, name) row exists."""
        with self.lock, self.conn:
            cur = self.conn.execute(
                "UPDATE classes SET tolerance = ? WHERE library_id = ? AND name = ?",
                (tolerance, library_id, name),
            )
            return cur.rowcount > 0

    def insert_template(self, library_id: str, t: Template) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO templates "
                "(id, library_id, class_name, entity_point_sets, centroid_x, centroid_y, "
                " bbox_xmin, bbox_ymin, bbox_xmax, bbox_ymax, created_at, entity_kinds) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    t.id, library_id, t.class_name,
                    json.dumps(t.entity_point_sets, separators=(",", ":")),
                    t.centroid[0], t.centroid[1],
                    t.bbox[0], t.bbox[1], t.bbox[2], t.bbox[3],
                    time.time(),
                    json.dumps(t.entity_kinds, separators=(",", ":")),
                ),
            )

    def delete_template(self, template_id: str) -> bool:
        with self.lock, self.conn:
            cur = self.conn.execute("DELETE FROM templates WHERE id = ?", (template_id,))
            return cur.rowcount > 0

    def update_template_class(self, template_id: str, new_class: str) -> bool:
        with self.lock, self.conn:
            cur = self.conn.execute(
                "UPDATE templates SET class_name = ? WHERE id = ?",
                (new_class, template_id),
            )
            return cur.rowcount > 0

    def load_library(
        self, library_id: str
    ) -> tuple[list[str], dict[str, float | None], dict[str, list[Template]]]:
        with self.lock:
            class_rows = self.conn.execute(
                "SELECT name, tolerance FROM classes WHERE library_id = ? "
                "ORDER BY rank ASC, created_at ASC",
                (library_id,),
            ).fetchall()
            classes = [r["name"] for r in class_rows]
            tolerances: dict[str, float | None] = {
                r["name"]: r["tolerance"] for r in class_rows
            }
            templates: dict[str, list[Template]] = {c: [] for c in classes}

            tmpl_rows = self.conn.execute(
                "SELECT id, class_name, entity_point_sets, centroid_x, centroid_y, "
                "bbox_xmin, bbox_ymin, bbox_xmax, bbox_ymax, entity_kinds "
                "FROM templates WHERE library_id = ? ORDER BY created_at ASC",
                (library_id,),
            ).fetchall()
            for r in tmpl_rows:
                raw_sets = json.loads(r["entity_point_sets"])
                point_sets: list[list[Point]] = [
                    [(float(p[0]), float(p[1])) for p in ent] for ent in raw_sets
                ]
                # Legacy rows store NULL → reconstruct a parallel [None, ...]
                # list so downstream code can index it uniformly.
                raw_kinds = r["entity_kinds"]
                if raw_kinds is None:
                    entity_kinds: list[str | None] = [None] * len(point_sets)
                else:
                    entity_kinds = list(json.loads(raw_kinds))
                t = Template(
                    id=r["id"],
                    class_name=r["class_name"],
                    entity_point_sets=point_sets,
                    centroid=(r["centroid_x"], r["centroid_y"]),
                    bbox=(r["bbox_xmin"], r["bbox_ymin"], r["bbox_xmax"], r["bbox_ymax"]),
                    entity_kinds=entity_kinds,
                )
                templates.setdefault(r["class_name"], []).append(t)
            return classes, tolerances, templates


class Library:
    """In-memory cache + write-through SQLite persistence, scoped to one library_id."""

    def __init__(
        self,
        library_id: str,
        store: Store,
        defaults: Iterable[str] = DEFAULT_CLASSES,
    ) -> None:
        self.library_id = library_id
        self.store = store
        classes, tolerances, templates = store.load_library(library_id)
        self._classes: list[str] = classes
        self._tolerances: dict[str, float | None] = tolerances
        self._templates: dict[str, list[Template]] = templates
        for c in defaults:
            if c not in self._templates:
                self.add_class(c)

    @property
    def classes(self) -> list[str]:
        return list(self._classes)

    def add_class(self, name: str) -> None:
        if name in self._templates:
            return
        self._classes.append(name)
        self._templates[name] = []
        self._tolerances[name] = None
        self.store.upsert_class(self.library_id, name)

    def tolerance_of(self, name: str) -> float | None:
        """Per-class chamfer override (drawing units) or `None` to use
        the global `app.matching.TOLERANCE_ABS` default."""
        return self._tolerances.get(name)

    def set_tolerance(self, name: str, value: float | None) -> bool:
        """Persist `value` as the class's chamfer override. Returns False
        when the class doesn't exist in this library."""
        if name not in self._templates:
            return False
        self._tolerances[name] = value
        self.store.update_class_tolerance(self.library_id, name, value)
        return True

    def add_template(self, template: Template) -> None:
        if template.class_name not in self._templates:
            self.add_class(template.class_name)
        self._templates[template.class_name].append(template)
        self.store.insert_template(self.library_id, template)

    def templates_of(self, class_name: str) -> list[Template]:
        return list(self._templates.get(class_name, []))

    def count(self, class_name: str) -> int:
        return len(self._templates.get(class_name, []))

    def summary(self) -> list[dict]:
        return [
            {"name": c, "count": self.count(c), "tolerance": self._tolerances.get(c)}
            for c in self._classes
        ]

    def find_template(self, template_id: str) -> Template | None:
        for templates in self._templates.values():
            for t in templates:
                if t.id == template_id:
                    return t
        return None

    def delete_template(self, template_id: str) -> bool:
        for templates in self._templates.values():
            for i, t in enumerate(templates):
                if t.id == template_id:
                    templates.pop(i)
                    self.store.delete_template(template_id)
                    return True
        return False

    def move_template(self, template_id: str, new_class: str) -> bool:
        found: Template | None = None
        for templates in self._templates.values():
            for i, t in enumerate(templates):
                if t.id == template_id:
                    found = templates.pop(i)
                    break
            if found is not None:
                break
        if found is None:
            return False
        if new_class not in self._templates:
            self.add_class(new_class)
        found.class_name = new_class
        self._templates[new_class].append(found)
        self.store.update_template_class(template_id, new_class)
        return True

    def all_templates(self) -> list[tuple[str, int, Template]]:
        out: list[tuple[str, int, Template]] = []
        for cls in self._classes:
            for i, t in enumerate(self._templates.get(cls, [])):
                out.append((cls, i, t))
        return out


class LibraryRegistry:
    """Manages multiple Library instances over a single shared Store."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self._libs: dict[str, Library] = {}
        self._lock = threading.RLock()

    def get(self, library_id: str) -> Library:
        with self._lock:
            lib = self._libs.get(library_id)
            if lib is None:
                # Ensure the library row exists before constructing a cache.
                if self.store.get_library(library_id) is None:
                    raise KeyError(f"library {library_id!r} not found")
                lib = Library(library_id, self.store)
                self._libs[library_id] = lib
            return lib

    def create(self, name: str) -> Library:
        library_id = str(uuid.uuid4())[:12]
        self.store.create_library(library_id, name)
        return self.get(library_id)

    def delete(self, library_id: str) -> None:
        if library_id == DEFAULT_LIBRARY_ID:
            raise ValueError("cannot delete the default library")
        with self._lock:
            self.store.delete_library(library_id)
            self._libs.pop(library_id, None)

    def list_summaries(self) -> list[dict]:
        rows = self.store.list_libraries()
        out = []
        for r in rows:
            lib = self.get(r["id"])
            n_templates = sum(lib.count(c) for c in lib.classes)
            out.append({
                "id": r["id"],
                "name": r["name"],
                "created_at": r["created_at"],
                "template_count": n_templates,
                "class_count": len(lib.classes),
            })
        return out

    def exists(self, library_id: str) -> bool:
        return self.store.get_library(library_id) is not None


# ---- Module-level singleton, hydrated from disk at import time ----------
from app.storage import DB_PATH  # noqa: E402

_STORE = Store(DB_PATH)
LIBRARIES = LibraryRegistry(_STORE)
# Ensure default library has its classes seeded.
LIBRARIES.get(DEFAULT_LIBRARY_ID)


# ---- Entity index helpers (unchanged) ------------------------------------
def build_handle_index(primitives: list[dict]) -> dict[str, list[int]]:
    """Group primitive indices by source DXF handle.

    Primitives flagged `decorative` (TEXT / MTEXT / DIMENSION / HATCH) are
    excluded — they're rendered for context but must not be selectable or
    match-able.
    """
    idx: dict[str, list[int]] = {}
    for i, p in enumerate(primitives):
        if p.get("decorative"):
            continue
        h = p.get("handle")
        if not h:
            continue
        idx.setdefault(h, []).append(i)
    return idx


def collect_entity_kinds(
    primitives: list[dict],
    handle_index: dict[str, list[int]],
    handle: str,
) -> str | None:
    """Return the shared primitive `type` for a handle, or None if its
    primitives have more than one type (mixed-kind handle) or the handle has
    no primitives. Used to tag EntityShape.kind / Template.entity_kinds so
    the matcher can dispatch primitive-specific fast paths."""
    types: set[str] = set()
    for pi in handle_index.get(handle, []):
        types.add(primitives[pi]["type"])
        if len(types) > 1:
            return None
    if not types:
        return None
    return next(iter(types))


def collect_entity_points(primitives: list[dict], handle_index: dict[str, list[int]], handle: str) -> list[Point]:
    pts: list[Point] = []
    for pi in handle_index.get(handle, []):
        p = primitives[pi]
        t = p["type"]
        if t == "line":
            pts.append(tuple(p["start"]))
            pts.append(tuple(p["end"]))
        elif t == "polyline":
            for v in p["points"]:
                pts.append(tuple(v))
        elif t == "filled_polygon":
            for ring in p["rings"]:
                for v in ring:
                    pts.append(tuple(v))
        elif t == "point":
            pts.append(tuple(p["pos"]))
        elif t == "circle":
            # Synthesize a deterministic, evenly-spaced point cloud so the
            # matcher sees something equivalent to the pre-change flattened
            # polyline. Density tracks CURVE_FLATTENING_DISTANCE so existing
            # fingerprints stay stable; the 64-cap protects giant fiducials.
            cx, cy = p["center"]
            r = float(p["r"])
            n = max(8, min(64, round(2.0 * math.pi * r / 0.01)))
            for i in range(n):
                a = 2.0 * math.pi * i / n
                pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts
