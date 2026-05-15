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
DEFAULT_CLASSES: list[str] = [
    "smd",
    "substrate",
    "die_area",
    "lid_outer",
    "lid_inner",
    "bga_ball",
    "pin_mark",
    "fiducial_mark",
    "2d_barcode",
]


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

    @classmethod
    def from_entities(cls, class_name: str, entity_point_sets: list[list[Point]]) -> "Template":
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
                    PRIMARY KEY (library_id, name)
                );
                INSERT INTO classes__new (library_id, name, rank, created_at)
                    SELECT '{DEFAULT_LIBRARY_ID}', name, rank, created_at FROM classes;
                DROP TABLE classes;
                ALTER TABLE classes__new RENAME TO classes;
            """)

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

    def insert_template(self, library_id: str, t: Template) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO templates "
                "(id, library_id, class_name, entity_point_sets, centroid_x, centroid_y, "
                " bbox_xmin, bbox_ymin, bbox_xmax, bbox_ymax, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    t.id, library_id, t.class_name,
                    json.dumps(t.entity_point_sets, separators=(",", ":")),
                    t.centroid[0], t.centroid[1],
                    t.bbox[0], t.bbox[1], t.bbox[2], t.bbox[3],
                    time.time(),
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

    def load_library(self, library_id: str) -> tuple[list[str], dict[str, list[Template]]]:
        with self.lock:
            class_rows = self.conn.execute(
                "SELECT name FROM classes WHERE library_id = ? "
                "ORDER BY rank ASC, created_at ASC",
                (library_id,),
            ).fetchall()
            classes = [r["name"] for r in class_rows]
            templates: dict[str, list[Template]] = {c: [] for c in classes}

            tmpl_rows = self.conn.execute(
                "SELECT id, class_name, entity_point_sets, centroid_x, centroid_y, "
                "bbox_xmin, bbox_ymin, bbox_xmax, bbox_ymax "
                "FROM templates WHERE library_id = ? ORDER BY created_at ASC",
                (library_id,),
            ).fetchall()
            for r in tmpl_rows:
                raw_sets = json.loads(r["entity_point_sets"])
                point_sets: list[list[Point]] = [
                    [(float(p[0]), float(p[1])) for p in ent] for ent in raw_sets
                ]
                t = Template(
                    id=r["id"],
                    class_name=r["class_name"],
                    entity_point_sets=point_sets,
                    centroid=(r["centroid_x"], r["centroid_y"]),
                    bbox=(r["bbox_xmin"], r["bbox_ymin"], r["bbox_xmax"], r["bbox_ymax"]),
                )
                templates.setdefault(r["class_name"], []).append(t)
            return classes, templates


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
        classes, templates = store.load_library(library_id)
        self._classes: list[str] = classes
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
        self.store.upsert_class(self.library_id, name)

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
        return [{"name": c, "count": self.count(c)} for c in self._classes]

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
