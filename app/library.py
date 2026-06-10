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
import logging
import math
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


logger = logging.getLogger(__name__)


# Canonical class list. Auto-seeded into every newly-created library.
# Order is also the display / toolbar / fold order — keep deliberate.
DEFAULT_CLASSES: list[str] = [
    "Substrate",
    "Pin-1",
    "Lid",
    "RingOuter",
    "RingInner",
    "DieArea",
    "DAM1",
    "DAM2",
    "FiducialCircle",
    "FiducialCross",
    "FiducialSquare",
    "SMD-2T",
    "C4Ball",
    "BGABall",
    "Protrusion",
    "2DBarcode",
    "SMD-3T",
    "SMD-8T",
    "SMD-14T",
]


# Class IDs that are no longer seeded. The migration drops both their class
# row and any templates filed under them, so legacy DBs converge to the new
# default list without manual cleanup.
# "DAM" was split into DAM1 / DAM2 on 2026-06-09 — drop the old single class
# (it carried no templates) so legacy libraries converge to the new pair.
DEPRECATED_CLASSES: frozenset[str] = frozenset({"FiducialMark", "Side", "DAM"})


# Code-declared default match config for built-in classes whose outline is a
# large rigid loop best matched by perimeter + aspect (signature mode) rather
# than PCA-chamfer. A big sharp-cornered boundary's chamfer score is sensitive
# to the stored winding / start vertex of the loop (see
# app.matching._canonical_start); signature matching keys only on size + aspect
# and sidesteps that entirely. `bbox_ratio` is the signature size tolerance.
# Classes absent here default to ('chamfer', None). Applied at class-seed time
# (add_class) and via a boot migration that only converts rows still in the
# pristine chamfer/NULL state — any explicit signature configuration set in the
# UI (a different bbox_ratio) is preserved across reboots.
# Pin-1 is not a large loop but a small orientation mark; it gets signature
# mode too (matched on size + aspect, not chamfer) with a more generous
# bbox_ratio so minor per-instance mark variation still matches.
# DieArea is the die-area boundary loop — same large-rigid-outline rationale;
# the 0.0005 bbox_ratio leaves headroom for minor per-instance size variation.
# DAM1 / DAM2 are the encapsulation dam rings — same large-outline rationale.
CLASS_DEFAULT_MATCH_CONFIG: dict[str, tuple[str, float | None]] = {
    "Substrate": ("signature", 0.0001),
    "RingOuter": ("signature", 0.0001),
    "RingInner": ("signature", 0.0001),
    "Pin-1":     ("signature", 0.0005),
    "DieArea":   ("signature", 0.0005),
    "DAM1":      ("signature", 0.0005),
    "DAM2":      ("signature", 0.0005),
}


# Display ID → match-JSON snake_case key. Display labels stay in their
# canonical form (BGABall, Pin-1, …); only the persisted JSON key uses the
# snake_case identifier downstream consumers (rule checker, exports) expect.
CLASS_JSON_KEY: dict[str, str] = {
    "Substrate":      "substrate",
    "Pin-1":          "pin_1",
    "Lid":            "lid",
    "RingOuter":      "ring_outer",
    "RingInner":      "ring_inner",
    "DieArea":        "die_area",
    "DAM1":           "dam1",
    "DAM2":           "dam2",
    "FiducialCircle": "fiducial_circle",
    "FiducialCross":  "fiducial_cross",
    "FiducialSquare": "fiducial_square",
    "SMD-2T":         "smd_2t",
    "C4Ball":         "c4_ball",
    "BGABall":        "bga_ball",
    "Protrusion":     "protrusion",
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
    "lid_outer":     "RingOuter",
    "lid_inner":     "RingInner",
    "bga_ball":      "BGABall",
    "pin_mark":      "Pin-1",
    "2d_barcode":    "2DBarcode",
    # LidOuter / LidInner were renamed to RingOuter / RingInner on 2026-06-09.
    # Rewrite existing class rows AND their templates in place (the rename
    # pass touches both tables), so saved Ring templates survive the rename.
    "LidOuter":      "RingOuter",
    "LidInner":      "RingInner",
}


# Per-class view constraints. Most IC-packaging classes are physically
# restricted to a subset of views (C4 bumps face the chip's top side; BGA
# balls face the package bottom). The match-JSON serialiser and the viewer's
# Scan All overlay both consult is_allowed_view() below to drop instances that
# violate this rule.
#
# These constraints also *disambiguate* same-geometry classes by physical
# view instead of by neighbour density: BGABall (bottom-only) and
# FiducialCircle (top-only) are mutually exclusive, so a circle is classified
# by which view it sits in — this replaced the old density-based
# class-arbitration subsystem for that pair (since removed).
#
# Absent key = class is unconstrained (all views including "unassigned"
# permitted). Present key = strict mode: only the listed views are
# allowed, and the "unassigned" position (no view rect covers the
# instance) is never allowed.
#
# JS canvas.js MUST keep an in-sync mirror of this constant between its
# CLASS_VIEW_CONSTRAINTS_BEGIN / _END sentinel comments. The drift-guard
# test in tests/test_canvas_constants.py enforces consistency.
# CLASS_VIEW_CONSTRAINTS_BEGIN
CLASS_VIEW_CONSTRAINTS: dict[str, frozenset[str]] = {
    "C4Ball":         frozenset({"top_view"}),
    "BGABall":        frozenset({"bottom_view"}),
    "FiducialCircle": frozenset({"top_view"}),
    "FiducialCross":  frozenset({"top_view", "bottom_view"}),
    "FiducialSquare": frozenset({"top_view", "bottom_view"}),
    "SMD-2T":         frozenset({"top_view", "bottom_view"}),
    "SMD-3T":         frozenset({"top_view", "bottom_view"}),
    "SMD-8T":         frozenset({"top_view", "bottom_view"}),
    "SMD-14T":        frozenset({"top_view", "bottom_view"}),
}
# CLASS_VIEW_CONSTRAINTS_END


def is_allowed_view(class_name: str, view: str | None) -> bool:
    """True when a (class_name, view) pair is permitted under
    CLASS_VIEW_CONSTRAINTS. Unconstrained classes (key absent) admit
    every view including None. Constrained classes admit only views in
    their allow-set and never None (strict mode: an unassigned match
    of a constrained class is physically impossible)."""
    allowed = CLASS_VIEW_CONSTRAINTS.get(class_name)
    if allowed is None:
        return True
    return view is not None and view in allowed


# Two-tier storage scope (PRODUCT_SCOPED_CLASSES) was removed 2026-06-10:
# every library belongs 1:1 to a version (app.versions), so all templates
# are version-scoped by construction and no class-based scope routing or
# dual-scope merge exists anymore.


# ---- Per-class toolbar category -----------------------------------------
# Functional grouping for the viewer's class toolbar. Display ID → category
# key; CLASS_CATEGORY_ORDER gives each key a label in render order. JS canvas.js
# MUST keep an in-sync mirror of BOTH between their CLASS_CATEGORY_BEGIN/_END and
# CLASS_CATEGORY_ORDER_BEGIN/_END sentinels — the drift-guard test in
# tests/test_canvas_constants.py enforces it. A class absent from CLASS_CATEGORY
# is treated as uncategorised (the toolbar groups it under a trailing "Other").
# CLASS_CATEGORY_BEGIN
CLASS_CATEGORY: dict[str, str] = {
    "Substrate":      "structure",
    "DieArea":        "structure",
    "DAM1":           "structure",
    "DAM2":           "structure",
    "Lid":            "structure",
    "RingOuter":      "structure",
    "RingInner":      "structure",
    "Protrusion":     "structure",
    "C4Ball":         "balls",
    "BGABall":        "balls",
    "SMD-2T":         "smd",
    "SMD-3T":         "smd",
    "SMD-8T":         "smd",
    "SMD-14T":        "smd",
    "FiducialCircle": "marks",
    "FiducialCross":  "marks",
    "FiducialSquare": "marks",
    "Pin-1":          "marks",
    "2DBarcode":      "marks",
}
# CLASS_CATEGORY_END

# CLASS_CATEGORY_ORDER_BEGIN
CLASS_CATEGORY_ORDER: list[tuple[str, str]] = [
    ("structure", "Structure"),
    ("balls", "Balls & Bumps"),
    ("smd", "SMD Pads"),
    ("marks", "Fiducials & Marks"),
]
# CLASS_CATEGORY_ORDER_END


# Invariants: every default class is categorised, and every category used is a
# declared order key — so a newly-added default class can't be left ungrouped.
_uncategorised = set(DEFAULT_CLASSES) - set(CLASS_CATEGORY)
if _uncategorised:
    raise ValueError(
        f"DEFAULT_CLASSES not in CLASS_CATEGORY: {sorted(_uncategorised)!r}"
    )
_order_keys = {k for k, _ in CLASS_CATEGORY_ORDER}
_unknown_category = set(CLASS_CATEGORY.values()) - _order_keys
if _unknown_category:
    raise ValueError(
        f"CLASS_CATEGORY uses keys absent from CLASS_CATEGORY_ORDER: "
        f"{sorted(_unknown_category)!r}"
    )
del _uncategorised, _order_keys, _unknown_category


# ---- Template dedup signature -------------------------------------------
# Coordinates are bucketed at 0.1 µm (10^-4 mm). Parallel to
# _radius_bucket_key's grid in app/matching.py — same FP-noise tolerance,
# same physical safety margin (real packaging classes differ by ≥ 1 µm =
# 10 buckets, so dedup never collapses genuinely distinct shapes).
TEMPLATE_DEDUP_BUCKET = 10**4


def template_signature(
    entity_point_sets: list[list[tuple[float, float]]],
) -> tuple:
    """Canonical, hashable dedup key for a template's entity point sets.

    Invariants:
    - translation YES (centroid-subtracted)
    - entity-order YES (outer sort)
    - vertex-order YES (inner sort)

    Non-invariants:
    - rotation NO  (90° copy gets a different key)
    - scale NO     (2× copy gets a different key)
    - reflection NO

    Bucket precision is `TEMPLATE_DEDUP_BUCKET` (0.1 µm), parallel to
    `_radius_bucket_key` in `app/matching.py`. Pure function — same
    input always returns the same tuple.
    """
    all_pts = [p for ent in entity_point_sets for p in ent]
    if not all_pts:
        return ()
    gx = sum(p[0] for p in all_pts) / len(all_pts)
    gy = sum(p[1] for p in all_pts) / len(all_pts)
    entity_keys: list[tuple[tuple[int, int], ...]] = []
    for pts in entity_point_sets:
        bucketed = tuple(sorted(
            (
                round((p[0] - gx) * TEMPLATE_DEDUP_BUCKET),
                round((p[1] - gy) * TEMPLATE_DEDUP_BUCKET),
            )
            for p in pts
        ))
        entity_keys.append(bucketed)
    return tuple(sorted(entity_keys))


def _template_signature_cached(t: "Template") -> tuple:
    sig = getattr(t, "_signature", None)
    if sig is not None:
        return sig
    sig = template_signature(t.entity_point_sets)
    object.__setattr__(t, "_signature", sig)
    return sig


# The shared "default" library was removed 2026-06-10: libraries are
# created exclusively by version creation (app.versions) and belong 1:1
# to a version.

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
    library_id     TEXT NOT NULL,
    name           TEXT NOT NULL,
    rank           INTEGER NOT NULL,
    created_at     REAL NOT NULL,
    match_strategy TEXT NOT NULL DEFAULT 'chamfer',
    bbox_ratio     REAL,
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
        from app.dbschema import ensure_versioned_schema
        ensure_versioned_schema(path)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.lock = threading.RLock()
        with self.lock, self.conn:
            self.conn.executescript(SCHEMA)
            self._migrate()
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_templates_lib_class ON templates(library_id, class_name)"
            )

    # ---- per-boot idempotent maintenance ----------------------------------
    def _migrate(self) -> None:
        """Idempotent per-boot upkeep on a versioned-schema DB.

        Pre-versioning databases never reach here — the dbschema guard
        rebuilds them from scratch (decision C9: no data preserved). What
        remains is rename/purge/seed/re-rank maintenance that must track
        code-level class-list changes across boots.
        """
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

        # Apply code-declared default match config for built-in large-outline
        # classes (Substrate / RingOuter / RingInner -> signature). Only convert
        # rows still in the pristine chamfer/NULL state; an explicit signature
        # config set in the UI (any bbox_ratio) is preserved. Idempotent: once
        # a row is signature the WHERE no longer matches it.
        for cls_name, (strat, ratio) in CLASS_DEFAULT_MATCH_CONFIG.items():
            self.conn.execute(
                "UPDATE classes SET match_strategy = ?, bbox_ratio = ? "
                "WHERE name = ? AND match_strategy = 'chamfer' "
                "AND bbox_ratio IS NULL",
                (strat, ratio, cls_name),
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

    def update_class_strategy(
        self,
        library_id: str,
        name: str,
        strategy: str,
        bbox_ratio: float | None,
    ) -> bool:
        """Atomically set (match_strategy, bbox_ratio) for a class.
        Returns True when the (library_id, name) row exists."""
        with self.lock, self.conn:
            cur = self.conn.execute(
                "UPDATE classes SET match_strategy = ?, bbox_ratio = ? "
                "WHERE library_id = ? AND name = ?",
                (strategy, bbox_ratio, library_id, name),
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
        self,
        library_id: str,
    ) -> tuple[list[str], dict[str, dict], dict[str, list[Template]]]:
        """Return (classes, configs, templates_by_class) for a library.

        A library belongs 1:1 to a version, so this is the version's
        complete template view — no scope merging exists anymore.
        """
        with self.lock:
            class_rows = self.conn.execute(
                "SELECT name, match_strategy, bbox_ratio FROM classes WHERE library_id = ? "
                "ORDER BY rank ASC, created_at ASC",
                (library_id,),
            ).fetchall()
            classes = [r["name"] for r in class_rows]
            configs: dict[str, dict] = {
                r["name"]: {
                    "match_strategy": r["match_strategy"] or "chamfer",
                    "bbox_ratio": r["bbox_ratio"],
                }
                for r in class_rows
            }
            templates: dict[str, list[Template]] = {c: [] for c in classes}

            tmpl_rows = self.conn.execute(
                "SELECT id, class_name, entity_point_sets, centroid_x, centroid_y, "
                "bbox_xmin, bbox_ymin, bbox_xmax, bbox_ymax, entity_kinds "
                "FROM templates "
                "WHERE library_id = ? "
                "ORDER BY created_at ASC",
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
            return classes, configs, templates


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
        classes, configs, templates = store.load_library(library_id)
        self._classes: list[str] = classes
        self._configs: dict[str, dict] = configs
        self._templates: dict[str, list[Template]] = templates
        for c in defaults:
            if c not in self._templates:
                self.add_class(c)
        self._warn_on_duplicate_signatures()

    def _warn_on_duplicate_signatures(self) -> None:
        """Surface pre-dedup duplicate rows at startup.

        Groups loaded templates by (class_name, canonical signature) and
        logs one WARNING per group with count > 1. The duplicates stay
        in place — the dedup invariant in `add_template_for_file` applies
        only to new commits. The operator can clean up via the existing
        delete-template UI if desired.
        """
        for class_name, templates in self._templates.items():
            if len(templates) < 2:
                continue
            sig_counts: dict[tuple, int] = {}
            for t in templates:
                sig = _template_signature_cached(t)
                sig_counts[sig] = sig_counts.get(sig, 0) + 1
            for sig, n in sig_counts.items():
                if n > 1:
                    logger.warning(
                        "library %s: class %s has %d templates with "
                        "identical canonical signature — pre-dedup data, "
                        "scan-all will iterate redundantly",
                        self.library_id, class_name, n,
                    )

    @property
    def classes(self) -> list[str]:
        return list(self._classes)

    def add_class(self, name: str) -> None:
        if name in self._templates:
            return
        self._classes.append(name)
        self._templates[name] = []
        strategy, bbox_ratio = CLASS_DEFAULT_MATCH_CONFIG.get(
            name, ("chamfer", None)
        )
        self._configs[name] = {
            "match_strategy": strategy, "bbox_ratio": bbox_ratio,
        }
        self.store.upsert_class(self.library_id, name)
        # upsert_class inserts with the DB default (chamfer / NULL); persist the
        # code-declared default when it differs so the DB row matches _configs.
        if strategy != "chamfer" or bbox_ratio is not None:
            self.store.update_class_strategy(
                self.library_id, name, strategy, bbox_ratio
            )

    def strategy_of(self, name: str) -> tuple[str, float | None]:
        """Per-class (match_strategy, bbox_ratio). Falls back to
        ('chamfer', None) when the class is unknown (so callers can
        always assume a safe default and don't need a None-guard)."""
        cfg = self._configs.get(name)
        if cfg is None:
            return ("chamfer", None)
        return (cfg.get("match_strategy") or "chamfer", cfg.get("bbox_ratio"))

    def set_strategy(
        self,
        name: str,
        strategy: str,
        bbox_ratio: float | None,
    ) -> bool:
        """Persist a class's matching strategy. Returns False when the
        class doesn't exist."""
        if name not in self._templates:
            return False
        self._configs[name] = {
            "match_strategy": strategy,
            "bbox_ratio": bbox_ratio,
        }
        self.store.update_class_strategy(
            self.library_id, name, strategy, bbox_ratio
        )
        return True

    def add_template(self, template: Template) -> tuple[Template, bool]:
        """Alias of add_template_for_file — kept for fixtures and any
        caller with no file context. Returns (template, already_existed)."""
        return self.add_template_for_file(template)

    def add_template_for_file(self, template: Template) -> tuple[Template, bool]:
        """Persist a template into this (version's) library.

        Deduplicates by canonical signature within the
        (library_id, class_name) scope — the in-memory cache holds the
        library's full template set, so it is canonical. Returns
        (existing_template, True) on hit (no append, no store insert);
        otherwise appends + inserts and returns (template, False)."""
        if template.class_name not in self._templates:
            self.add_class(template.class_name)
        new_sig = template_signature(template.entity_point_sets)
        for existing in self._templates.get(template.class_name, []):
            if _template_signature_cached(existing) == new_sig:
                return existing, True

        object.__setattr__(template, "_signature", new_sig)
        self._templates[template.class_name].append(template)
        self.store.insert_template(self.library_id, template)
        return template, False

    def templates_of(self, class_name: str) -> list[Template]:
        return list(self._templates.get(class_name, []))

    def count(self, class_name: str) -> int:
        return len(self._templates.get(class_name, []))

    def summary(self) -> list[dict]:
        out = []
        for c in self._classes:
            cfg = self._configs.get(c, {})
            out.append({
                "name": c,
                "count": self.count(c),
                "match_strategy": cfg.get("match_strategy") or "chamfer",
                "bbox_ratio": cfg.get("bbox_ratio"),
            })
        return out

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
        with self._lock:
            self.store.delete_library(library_id)
            self._libs.pop(library_id, None)

    def evict(self, library_id: str) -> None:
        """Drop the in-memory cache for a library whose rows were written
        or removed by another connection (version clone / product cascade)."""
        with self._lock:
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
