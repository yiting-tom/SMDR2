"""DXF parsing and flattening.

Responsibilities:
1. `flatten_for_render(path)`: turn a DXF into JSON-serialisable drawing
   primitives (line / polyline / filled_polygon / point). ezdxf's Frontend
   handles OCS, INSERT expansion, bulge, text-to-paths, etc. Curves are
   flattened to polylines for trivial canvas rendering.
2. `group_primitives_by_layer(prims)` / `render_layer_svg(...)`: emit
   per-layer SVG thumbnails for the layer-discovery phase.
3. `filter_primitives(prims, layers)`: drop everything not on the user's
   chosen layer subset.
"""

from __future__ import annotations

import logging
import math
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import ezdxf
import ezdxf.bbox
import ezdxf.recover
import numpy as np
from ezdxf.addons.drawing import Frontend, RenderContext
from ezdxf.addons.drawing.backend import BackendInterface
from ezdxf.addons.drawing.config import Configuration, TextPolicy
from ezdxf.addons.drawing.properties import BackendProperties
from ezdxf.math import Vec2
from ezdxf.npshapes import NumpyPath2d, NumpyPoints2d


logger = logging.getLogger(__name__)


# Floor for curve flattening (drawing units). Used directly for normal-scale
# DXFs; relaxed proportionally for files with abnormally large bboxes so the
# vertex count stays bounded across pathological unit scales (e.g. INSUNITS=0
# files mis-interpreted as 1000× their intended unit).
BASE_TOLERANCE = 0.01
# Tolerance auto-scales with bbox diagonal: tol = max(BASE, D × SCALE_FACTOR).
# At 1e-5, even at fit-zoom one chord deviation is ~67× tighter than a screen
# pixel — well below anything a user can perceive.
SCALE_FACTOR = 1e-5
# Legacy alias kept for external imports / downstream readers.
CURVE_FLATTENING_DISTANCE = BASE_TOLERANCE

# Circle-detection thresholds. `CIRCLE_MIN_VERTS` (curves case) and
# `CIRCLE_RADIAL_TOL` are kept in lockstep with the client-side
# `detectCircle` in app/static/measure_core.js so server emit and client
# OSNAP/QUA snap agree on what counts as a circle for sub-paths the
# server did NOT pre-convert. `CIRCLE_MIN_VERTS_NOCURVE` is server-only:
# it gates promotion of pure-line LWPOLYLINE / POLYLINE entities whose
# vertices happen to lie on a circle (typical for BGA balls authored as
# N-gons in some packaging DXFs). The higher floor protects deliberate
# low-N polygon pads (N ∈ {3, 4, 6, 8, 12}) whose perfectly regular
# radial layout would otherwise sail through `CIRCLE_RADIAL_TOL`.
CIRCLE_MIN_VERTS = 8
CIRCLE_MIN_VERTS_NOCURVE = 11
CIRCLE_RADIAL_TOL = 0.002

# DXF entity types that should be rendered but NOT participate in selection,
# chain-grouping, or matching. Their primitives get a `"decorative": true`
# flag at flatten time; everything downstream filters on that. HATCH is
# stripped before flatten (see `flatten_for_render`), so it never reaches
# this filter.
DECORATIVE_DXFTYPES = frozenset({"TEXT", "MTEXT", "DIMENSION"})


@dataclass
class RenderOutput:
    primitives: list[dict[str, Any]]
    bbox: tuple[float, float, float, float] | None  # (xmin, ymin, xmax, ymax)
    background: str  # "#RRGGBB"
    # Tolerance actually used to flatten curves for this file. Surfaced for
    # tests + future dashboard diagnostics ("why does this file have so few /
    # so many vertices?"). Default keeps callers that construct it manually
    # working.
    flatten_tolerance: float = BASE_TOLERANCE
    # Raw `$INSUNITS` header value from the source DXF (0 = unitless,
    # 1 = inch, 2 = foot, 4 = mm, 5 = cm, 6 = m, …). Persisted to the file
    # record and fed into the dashboard's unit-scale-warning heuristic.
    # None when the header is missing or unparseable.
    insunits: int | None = None
    # Multiplier applied to every coordinate in `primitives` + `bbox` so the
    # downstream consumers can stay in mm. `rescaled_coord = original_coord
    # * applied_scale`. `1.0` means "no rescale". See `detect_scale_factor`
    # for the trigger rules.
    applied_scale: float = 1.0
    # When the source DXF could not be opened in strict mode and ezdxf's
    # recover path was used instead, this dict summarises what the audit
    # patched. `None` means the file parsed cleanly via strict — there
    # is no notion of "recovered with zero fixes". Shape:
    # {"strict_error": "<ExcCls>: <msg>", "n_fixed": int,
    #  "n_unrecoverable": int, "audit_messages": ["<msg>", …]}.
    recover_notes: dict[str, Any] | None = None
    # Which DXF "tab" these primitives were rendered from: "Model" for
    # modelspace (the historical default), or a paper-space layout name
    # (e.g. "Layout1") when the file's geometry lives in a layout tab. Set
    # by `flatten_for_render` after layout resolution; surfaced so the
    # dashboard/viewer can show a "loaded from <tab>" badge.
    source_layout: str | None = None
    # True when `source_layout` is a paper-space layout rather than
    # modelspace. Lets callers branch on "did we fall back to paper space?"
    # without re-opening the doc.
    source_is_paperspace: bool = False


# Maps the operator-facing unit-override strings (persisted in
# `files.user_unit_override`) to the multiplier that brings coordinates
# in that unit into mm. When an override is set, `_maybe_rescale` skips
# `detect_scale_factor` and uses this directly.
UNIT_TO_SCALE: dict[str, float] = {
    "mm": 1.0,
    "cm": 10.0,
    "m": 1000.0,
    "inch": 25.4,
    "μm": 0.001,
}

# Reverse map used by the viewer to pre-select the picker option for a
# file whose `applied_scale` is one of the known unit multipliers.
SCALE_TO_UNIT: dict[float, str] = {v: k for k, v in UNIT_TO_SCALE.items()}


def detect_scale_factor(insunits: int | None, bbox_diagonal: float) -> float:
    """Always returns `1.0` — automatic unit detection is disabled.

    Every uploaded DXF is now taken to be authored in mm as-is; the
    preprocessor never auto-rescales. Only an explicit operator choice in
    the viewer's unit picker (`files.user_unit_override`) rescales a file,
    and that path bypasses this function entirely (`_maybe_rescale` reads
    `UNIT_TO_SCALE` directly). The `insunits` / `bbox_diagonal` arguments
    are retained for signature stability with existing callers.

    Previously this inspected `$INSUNITS` and the bbox diagonal to guess a
    power-of-10 (or inch) correction. That heuristic was removed on
    2026-06-09: it caused false rescales on legitimate files, and the
    operator's manual picker is the authoritative override when a file
    really is in non-mm units."""
    return 1.0


def _scale_primitive_coords(prim: dict[str, Any], factor: float) -> None:
    """Multiply every coordinate inside a primitive dict by `factor` in
    place. Handles every primitive shape emitted by `JSONBackend`."""
    t = prim.get("type")
    if t == "point":
        x, y = prim["pos"]
        prim["pos"] = [x * factor, y * factor]
    elif t == "line":
        sx, sy = prim["start"]
        ex, ey = prim["end"]
        prim["start"] = [sx * factor, sy * factor]
        prim["end"] = [ex * factor, ey * factor]
    elif t == "polyline":
        prim["points"] = [[x * factor, y * factor] for x, y in prim["points"]]
    elif t == "circle":
        cx, cy = prim["center"]
        prim["center"] = [cx * factor, cy * factor]
        prim["r"] = prim["r"] * factor
    elif t == "filled_polygon":
        prim["rings"] = [
            [[x * factor, y * factor] for x, y in ring]
            for ring in prim["rings"]
        ]


def _maybe_rescale(
    render: RenderOutput,
    user_unit_override: str | None = None,
) -> tuple[RenderOutput, float]:
    """Apply a scale multiplier and, when non-trivial, scale every
    primitive coordinate and the bbox in place. Returns the (possibly
    mutated) render and the applied factor.

    When `user_unit_override` is a recognised unit string, the
    detector is skipped entirely and the override's multiplier is
    used. An unrecognised override string falls through to the
    detector path (treated as no override) — the API layer rejects
    bad inputs before they reach here, so this is a defensive
    fallback only."""
    if render.bbox is None:
        return render, 1.0
    xmin, ymin, xmax, ymax = render.bbox
    dx = float(xmax) - float(xmin)
    dy = float(ymax) - float(ymin)
    diagonal = math.hypot(max(dx, 0.0), max(dy, 0.0))
    if user_unit_override in UNIT_TO_SCALE:
        factor = UNIT_TO_SCALE[user_unit_override]
        logger.info(
            "user-unit-override: unit=%s, pre-diagonal=%.3g, factor=%.6g (detector skipped)",
            user_unit_override, diagonal, factor,
        )
    else:
        factor = detect_scale_factor(render.insunits, diagonal)
    if factor == 1.0:
        return render, 1.0
    for prim in render.primitives:
        _scale_primitive_coords(prim, factor)
    render.bbox = (xmin * factor, ymin * factor, xmax * factor, ymax * factor)
    render.applied_scale = factor
    if user_unit_override not in UNIT_TO_SCALE:
        logger.info(
            "auto-rescale: insunits=%s, pre-diagonal=%.3g, factor=%.6g → post-diagonal=%.3g",
            render.insunits, diagonal, factor, diagonal * factor,
        )
    return render, factor


def choose_flatten_tolerance(diagonal: float) -> float:
    """Pick a curve-flattening tolerance from a file's bbox diagonal.

    `max(BASE_TOLERANCE, diagonal * SCALE_FACTOR)`. Defensive on
    negative / NaN inputs — returns `BASE_TOLERANCE`."""
    if not math.isfinite(diagonal) or diagonal <= 0:
        return BASE_TOLERANCE
    return max(BASE_TOLERANCE, diagonal * SCALE_FACTOR)


def _modelspace_diagonal(doc) -> float | None:
    """Cheap bbox-diagonal probe. Prefers the DXF header's `$EXTMIN` /
    `$EXTMAX` (free — already in memory after readfile) and falls back to
    `ezdxf.bbox.extents(fast=True)` only when the header values are
    missing or degenerate. Returns None when no estimate can be made.

    The header path costs microseconds; the fallback path costs seconds
    on 100 k-entity files, so the header shortcut matters."""
    try:
        emin = doc.header.get("$EXTMIN")
        emax = doc.header.get("$EXTMAX")
    except Exception:
        emin = emax = None
    if emin is not None and emax is not None:
        dx = float(emax[0]) - float(emin[0])
        dy = float(emax[1]) - float(emin[1])
        if math.isfinite(dx) and math.isfinite(dy) and dx > 0 and dy > 0:
            return math.hypot(dx, dy)
    # Fall back to the entity-sweep extents probe. Slower but works on
    # files whose header lacks usable extents.
    try:
        ext = ezdxf.bbox.extents(doc.modelspace(), fast=True)
    except Exception:
        return None
    if not ext.has_data:
        return None
    size = ext.size
    return math.hypot(float(size.x), float(size.y))


# Entity types that occupy a layout's entity list but never emit a render
# primitive. AutoCAD writes a VIEWPORT into essentially every paper-space
# layout tab (the sheet's window into model space), so counting raw
# `len(layout)` would treat a pure framing/title-block tab as if it held
# drawable geometry — wrongly tripping the layout picker and skewing the
# tolerance diagonal. The "does this tab have content?" heuristics count
# only entities NOT in this set.
NON_RENDERED_DXFTYPES = frozenset({"VIEWPORT"})


def _renderable_entity_count(layout) -> int:
    """Count a layout's entities excluding `NON_RENDERED_DXFTYPES`. A cheap
    proxy for "drawable content" — does not flatten, so it over-counts
    entities that happen to render empty (e.g. TEXT under TextPolicy.IGNORE);
    callers that need an exact answer flatten and check the primitive count."""
    n = 0
    for e in layout:
        try:
            if e.dxftype() in NON_RENDERED_DXFTYPES:
                continue
        except Exception:
            pass
        n += 1
    return n


def _layout_name(layout: Any) -> str:
    """Best-effort tab name for an ezdxf layout / modelspace object.
    Modelspace reports `name == "Model"`; paper-space layouts report their
    tab name (e.g. "Layout1")."""
    return str(getattr(layout, "name", "") or "Model")


def _resolve_layout(doc, layout_name: str | None) -> tuple[Any, str, bool]:
    """Choose which DXF tab to render. Returns
    `(layout_obj, resolved_name, is_paperspace)`.

    - When `layout_name` names an existing tab, that tab is rendered.
    - Otherwise auto-resolve: modelspace when it holds ANY entities — the
      historical default, so normal files (geometry in modelspace) behave
      exactly as before — else the paper-space layout with the most
      entities. This last branch is what makes DXFs exported from DWGs
      whose geometry lives in a layout tab (rather than modelspace) render
      at all.
    - When nothing qualifies (truly empty file), falls back to the empty
      modelspace, identical to the pre-feature behaviour.

    An unknown `layout_name` logs a warning and falls through to
    auto-resolution rather than raising — a stale persisted choice should
    degrade gracefully, not break the pipeline."""
    msp = doc.modelspace()
    if layout_name:
        try:
            lay = doc.layouts.get(layout_name)
        except Exception:
            logger.warning(
                "requested layout %r not found; auto-resolving instead",
                layout_name,
            )
            lay = None
        if lay is not None:
            return lay, _layout_name(lay), bool(getattr(lay, "is_any_paperspace", False))
    # Modelspace fast-path: `len() > 0` is O(1) and modelspace never holds
    # the paper-space VIEWPORT entities, so the raw length is a faithful
    # "has content?" test here without paying the per-entity scan.
    if len(msp) > 0:
        return msp, _layout_name(msp), False
    # Paper-space fallback: rank by RENDERABLE entity count so a tab padded
    # with viewports (or that is viewport-only) never out-ranks a tab with
    # real geometry. Layouts are sheet-sized, so the per-entity scan is cheap.
    best: tuple[Any, str] | None = None
    best_count = 0
    for name in doc.layout_names_in_taborder():
        try:
            lay = doc.layouts.get(name)
        except Exception:
            continue
        if not getattr(lay, "is_any_paperspace", False):
            continue
        count = _renderable_entity_count(lay)
        if count > best_count:
            best_count = count
            best = (lay, name)
    if best is not None:
        return best[0], best[1], True
    return msp, _layout_name(msp), False


def _enumerate_layouts_doc(doc) -> list[dict[str, Any]]:
    """Inventory of a doc's tabs (modelspace + every paper-space layout) in
    tab order: `[{"name", "entity_count", "is_paperspace"}, ...]`. The
    `entity_count` excludes `NON_RENDERED_DXFTYPES` (VIEWPORTs) so a
    viewport-only framing tab reads as zero content; it is a cheap proxy for
    drawable geometry used to rank/flag candidate tabs, never the exact
    rendered primitive count."""
    out: list[dict[str, Any]] = []
    for name in doc.layout_names_in_taborder():
        try:
            lay = doc.layouts.get(name)
        except Exception:
            continue
        out.append({
            "name": name,
            # Renderable count (excludes VIEWPORTs) so a viewport-only
            # framing tab reads as empty and never trips the picker gate.
            "entity_count": _renderable_entity_count(lay),
            "is_paperspace": bool(getattr(lay, "is_any_paperspace", False)),
        })
    return out


def enumerate_layouts(
    dxf_path: str | Path, *, file_id: str | None = None,
) -> list[dict[str, Any]]:
    """Open a DXF and return its tab inventory (see `_enumerate_layouts_doc`).
    Used by the discovery phase to decide whether a layout picker is needed
    and to populate it."""
    doc, _ = _open_dxf(dxf_path, file_id=file_id)
    return _enumerate_layouts_doc(doc)


def _layout_diagonal(doc, layout) -> float | None:
    """Bbox-diagonal probe for the tab being rendered. Modelspace reuses the
    cheap header `$EXTMIN/$EXTMAX` shortcut (`_modelspace_diagonal`); a
    paper-space layout's geometry lives in its own coordinate frame, for
    which the header extents do not apply, so we sweep that layout's entity
    extents directly via `ezdxf.bbox.extents(fast=True)`."""
    if not getattr(layout, "is_any_paperspace", False):
        return _modelspace_diagonal(doc)
    try:
        # Exclude the VIEWPORT frame: its paper-space placement rectangle
        # (e.g. an A3 sheet) would otherwise dominate the diagonal and
        # coarsen the flatten tolerance for the actual (often sub-mm) geometry.
        drawable = [
            e for e in layout if e.dxftype() not in NON_RENDERED_DXFTYPES
        ]
        ext = ezdxf.bbox.extents(drawable, fast=True)
    except Exception:
        return None
    if not ext.has_data:
        return None
    size = ext.size
    return math.hypot(float(size.x), float(size.y))


class JSONBackend(BackendInterface):
    """Collects drawing operations as JSON-serialisable primitive dicts."""

    def __init__(self, flatten_tolerance: float = BASE_TOLERANCE) -> None:
        self.primitives: list[dict[str, Any]] = []
        self.background: str = "#ffffff"
        self.flatten_tolerance = flatten_tolerance
        self._xmin = float("inf")
        self._ymin = float("inf")
        self._xmax = float("-inf")
        self._ymax = float("-inf")
        # Set true while inside the enter_entity → exit_entity wrapper of a
        # decorative DXF entity. Each appended primitive inherits this flag.
        self._decorative: bool = False

    # ---- lifecycle ---------------------------------------------------------
    def configure(self, config: Configuration) -> None:
        pass

    def clear(self) -> None:
        self.primitives.clear()

    def finalize(self) -> None:
        pass

    def enter_entity(self, entity, properties) -> None:  # noqa: ARG002
        try:
            self._decorative = entity.dxftype() in DECORATIVE_DXFTYPES
        except Exception:
            self._decorative = False

    def exit_entity(self, entity) -> None:  # noqa: ARG002
        self._decorative = False

    def _append(self, prim: dict[str, Any]) -> None:
        if self._decorative:
            prim["decorative"] = True
        self.primitives.append(prim)

    def set_background(self, color: str) -> None:
        self.background = _normalize_color(color)

    # ---- draw operations ---------------------------------------------------
    def draw_point(self, pos: Vec2, properties: BackendProperties) -> None:
        x, y = float(pos.x), float(pos.y)
        self._track_point(x, y)
        self._append(
            {"type": "point", "pos": [x, y], **_props(properties)}
        )

    def draw_line(self, start: Vec2, end: Vec2, properties: BackendProperties) -> None:
        sx, sy, ex, ey = float(start.x), float(start.y), float(end.x), float(end.y)
        self._track_point(sx, sy)
        self._track_point(ex, ey)
        self._append(
            {
                "type": "line",
                "start": [sx, sy],
                "end": [ex, ey],
                **_props(properties),
            }
        )

    def draw_solid_lines(
        self,
        lines: Iterable[tuple[Vec2, Vec2]],
        properties: BackendProperties,
    ) -> None:
        common = _props(properties)
        for start, end in lines:
            sx, sy, ex, ey = float(start.x), float(start.y), float(end.x), float(end.y)
            self._track_point(sx, sy)
            self._track_point(ex, ey)
            self._append(
                {"type": "line", "start": [sx, sy], "end": [ex, ey], **common}
            )

    def draw_path(self, path: NumpyPath2d, properties: BackendProperties) -> None:
        for sub in path.sub_paths():
            points = _flatten_path(sub, self.flatten_tolerance)
            if len(points) < 2:
                continue
            # Closed sub-paths whose flattened vertices describe a circle
            # within tolerance are emitted as a `circle` primitive instead
            # of a many-vertex closed polyline — saves ~30× on memory /
            # bandwidth for BGA-ball-heavy packaging DXFs and lets the
            # canvas draw via ctx.arc + sub-pixel LOD batching. The
            # vertex-count floor is dual: `CIRCLE_MIN_VERTS` (8) when
            # ezdxf flattened a real curve (`has_curves`), and the
            # higher `CIRCLE_MIN_VERTS_NOCURVE` (11) when the sub-path
            # is pure line segments (typical for BGA balls authored as
            # LWPOLYLINE N-gons). The higher floor for the no-curves
            # case protects deliberate low-N polygon pads (N ∈
            # {3, 4, 6, 8}) whose perfectly regular radial layout
            # would otherwise sail through the radial test.
            if bool(sub.is_closed):
                has_curves = bool(getattr(sub, "has_curves", False))
                min_verts = CIRCLE_MIN_VERTS if has_curves else CIRCLE_MIN_VERTS_NOCURVE
                circle = _detect_circle_subpath(points, min_verts)
                if circle is not None:
                    cx, cy = circle["center"]
                    r = circle["r"]
                    self._track_point(cx - r, cy - r)
                    self._track_point(cx + r, cy + r)
                    self._append({"type": "circle", **circle, **_props(properties)})
                    continue
            self._track_points(points)
            self._append(
                {
                    "type": "polyline",
                    "points": points,
                    "closed": bool(sub.is_closed),
                    **_props(properties),
                }
            )

    def draw_filled_paths(
        self,
        paths: Iterable[NumpyPath2d],
        properties: BackendProperties,
    ) -> None:
        common = _props(properties)
        paths_list = list(paths)
        # Fast path: a single closed sub-path that detects as a circle
        # (e.g., HATCH bounded by a CIRCLE, or a HATCH bounded by an
        # LWPOLYLINE N-gon approximating a circle) collapses to a
        # `circle` primitive so the canvas renderer can use ctx.arc +
        # sub-pixel dot batching instead of filling an N-vertex
        # polygon. Same dual-threshold rule as draw_path:
        # `CIRCLE_MIN_VERTS` (8) for `has_curves` sub-paths,
        # `CIRCLE_MIN_VERTS_NOCURVE` (11) for pure-line sub-paths, so
        # a filled N-gon SMD pad keeps its corners.
        if len(paths_list) == 1:
            subs = list(paths_list[0].sub_paths())
            if len(subs) == 1:
                sub = subs[0]
                if bool(sub.is_closed):
                    pts = _flatten_path(sub, self.flatten_tolerance)
                    if len(pts) >= 3:
                        has_curves = bool(getattr(sub, "has_curves", False))
                        min_verts = (
                            CIRCLE_MIN_VERTS if has_curves else CIRCLE_MIN_VERTS_NOCURVE
                        )
                        circle = _detect_circle_subpath(pts, min_verts)
                        if circle is not None:
                            cx, cy = circle["center"]
                            r = circle["r"]
                            self._track_point(cx - r, cy - r)
                            self._track_point(cx + r, cy + r)
                            self._append(
                                {"type": "circle", "filled": True, **circle, **common}
                            )
                            return
        rings: list[list[list[float]]] = []
        for path in paths_list:
            for sub in path.sub_paths():
                pts = _flatten_path(sub, self.flatten_tolerance)
                if len(pts) >= 3:
                    self._track_points(pts)
                    rings.append(pts)
        if rings:
            self._append(
                {"type": "filled_polygon", "rings": rings, **common}
            )

    def draw_filled_polygon(
        self,
        points: NumpyPoints2d,
        properties: BackendProperties,
    ) -> None:
        pts = [[float(v.x), float(v.y)] for v in points.vertices()]
        if len(pts) < 3:
            return
        self._track_points(pts)
        self._append(
            {"type": "filled_polygon", "rings": [pts], **_props(properties)}
        )

    def draw_image(self, image_data, properties) -> None:  # noqa: ARG002
        # Raster images skipped for MVP.
        pass

    # ---- bbox tracking -----------------------------------------------------
    def _track_point(self, x: float, y: float) -> None:
        if x < self._xmin:
            self._xmin = x
        if y < self._ymin:
            self._ymin = y
        if x > self._xmax:
            self._xmax = x
        if y > self._ymax:
            self._ymax = y

    def _track_points(self, points: list[list[float]]) -> None:
        for x, y in points:
            self._track_point(x, y)

    @property
    def bbox(self) -> tuple[float, float, float, float] | None:
        if self._xmin == float("inf"):
            return None
        return (self._xmin, self._ymin, self._xmax, self._ymax)


def _open_dxf(
    dxf_path: str | Path, *, file_id: str | None = None,
) -> tuple[Any, dict[str, Any] | None]:
    """Open a DXF using strict mode first, falling back to ezdxf's
    recover mode on parser errors. Returns `(doc, recover_notes)`
    where `recover_notes` is `None` for strict-OK files and a small
    summary dict for files that took the recover path.

    Non-parser exceptions (FileNotFoundError, PermissionError, OS
    errors) propagate unchanged — recover cannot fix those and we'd
    just confuse the operator with a misleading audit message. When
    both strict and recover raise, this raises `RuntimeError` whose
    message includes both exception classes and messages so the
    worker's error handler can persist a combined diagnostic."""
    path_str = str(dxf_path)
    try:
        return ezdxf.readfile(path_str), None
    except ezdxf.DXFError as strict_exc:
        try:
            doc, auditor = ezdxf.recover.readfile(path_str)
        except ezdxf.DXFError as recover_exc:
            raise RuntimeError(
                f"strict: {type(strict_exc).__name__}: {strict_exc} "
                f"| recover: {type(recover_exc).__name__}: {recover_exc}"
            ) from recover_exc
        fixes = list(auditor.fixes) if auditor is not None else []
        errors = list(auditor.errors) if auditor is not None else []
        audit_messages = [str(f) for f in fixes[:5]]
        recover_notes = {
            "strict_error": f"{type(strict_exc).__name__}: {strict_exc}",
            "n_fixed": len(fixes),
            "n_unrecoverable": len(errors),
            "audit_messages": audit_messages,
        }
        logger.warning(
            "dxf parse: strict failed → recover ok "
            "(file_id=%s, path=%s, strict=%s: %s, fixed=%d, "
            "unrecoverable=%d, sample=%r)",
            file_id, path_str, type(strict_exc).__name__,
            str(strict_exc)[:200],
            recover_notes["n_fixed"], recover_notes["n_unrecoverable"],
            audit_messages,
        )
        return doc, recover_notes


def flatten_for_render(
    dxf_path: str | Path,
    user_unit_override: str | None = None,
    *,
    file_id: str | None = None,
    layout_name: str | None = None,
) -> RenderOutput:
    """Parse a DXF file and return drawing primitives + bbox.

    When `user_unit_override` is set, the rescale step uses that
    unit's multiplier and skips `detect_scale_factor`. Callers reach
    this path when the operator has used the viewer's unit picker;
    standard uploads pass `None` and fall through to the detector.

    `layout_name` selects which DXF "tab" to render. `None` (the default)
    auto-resolves: modelspace when it holds any entities — unchanged
    behaviour for normal files — otherwise the richest paper-space layout,
    so DXFs whose geometry lives in a layout tab (exported from DWGs
    organised per-view) render instead of coming back empty. A non-None
    value renders that specific tab; an unknown name degrades to
    auto-resolution. See `_resolve_layout`.

    `file_id` is plumbed through to the strict/recover log line —
    optional because the test suite + ad-hoc callers don't have one."""
    doc, recover_notes = _open_dxf(dxf_path, file_id=file_id)
    target, source_layout, source_is_paperspace = _resolve_layout(doc, layout_name)
    if source_is_paperspace:
        logger.info(
            "flatten: rendering paper-space layout %r (modelspace empty or "
            "layout explicitly requested)", source_layout,
        )
    # HATCH is pure decorative noise in packaging DXFs (solder-mask fills,
    # copper pours) and its boundary edges otherwise reach the backend as
    # polylines that don't promote to circle primitives — costing render
    # budget and producing jagged N-gon outlines at zoom-in. Strip before
    # `Frontend.draw_layout` so no HATCH-sourced primitive is ever emitted.
    # Stripped from the resolved tab (modelspace or paper-space layout).
    for hatch in list(target.query("HATCH")):
        target.delete_entity(hatch)
    diagonal = _layout_diagonal(doc, target)
    tol = choose_flatten_tolerance(diagonal) if diagonal is not None else BASE_TOLERANCE
    if tol != BASE_TOLERANCE:
        logger.info(
            "flatten: diagonal=%.3g → tol=%.4g (base=%.4g)",
            diagonal, tol, BASE_TOLERANCE,
        )
    ctx = RenderContext(doc)
    backend = JSONBackend(flatten_tolerance=tol)
    # SMDR2's pattern matching never consumes TEXT/MTEXT geometry — the
    # template library is pure shape (lines, circles, polygons), so the
    # rendered glyphs would just add noise. We disable text rendering
    # entirely, which also bypasses font loading; this dodges fontTools
    # assertions / warnings on broken system fonts
    # (e.g. fontTools/ttLib/tables/_g_l_y_f.py "At least two cubic
    # off-curve points required" crashes seen on CJK fonts with
    # malformed cubic-Bézier glyph data).
    Frontend(
        ctx, backend,
        config=Configuration(text_policy=TextPolicy.IGNORE),
    ).draw_layout(target, finalize=True)
    render = RenderOutput(
        primitives=backend.primitives,
        bbox=backend.bbox,
        background=backend.background,
        flatten_tolerance=tol,
        insunits=_read_insunits(doc),
        recover_notes=recover_notes,
        source_layout=source_layout,
        source_is_paperspace=source_is_paperspace,
    )
    render, _ = _maybe_rescale(render, user_unit_override=user_unit_override)
    return render


def _read_insunits(doc) -> int | None:
    """Pull `$INSUNITS` from the DXF header. Returns None when missing or
    unparseable so callers can downstream-default without try/except."""
    try:
        raw = doc.header.get("$INSUNITS")
    except Exception:
        return None
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


# ---- helpers ---------------------------------------------------------------
def _flatten_path(sub: NumpyPath2d, tolerance: float = BASE_TOLERANCE) -> list[list[float]]:
    return [[float(v.x), float(v.y)] for v in sub.flattening(tolerance)]


def _detect_circle_subpath(
    points: list[list[float]], min_verts: int = CIRCLE_MIN_VERTS
) -> dict[str, Any] | None:
    """Return `{"center": [cx, cy], "r": float}` when `points` describe a
    circle within tolerance, else None. The radial predicate matches the
    client-side `detectCircle`. Caller is expected to gate on
    `sub.is_closed` and to pick `min_verts` per the curves vs no-curves
    rule: `CIRCLE_MIN_VERTS` (8) when ezdxf flattened a real curve
    sub-path, `CIRCLE_MIN_VERTS_NOCURVE` (11) when the sub-path is pure
    line segments — the higher floor keeps deliberate low-N polygon
    pads from being eaten by the radial test.

    Centre is estimated by Kåsa algebraic least-squares fit
    (`min Σ (xᵢ² + yᵢ² + D·xᵢ + E·yᵢ + F)²`, closed form): this is
    spacing-invariant where the vertex centroid is not. The centroid
    drifts toward dense regions of the perimeter when vertices are
    unevenly spaced; LS does not. On singular / collinear inputs the
    function falls back to the centroid so degenerate sub-paths produce
    the same result as before this change (the radial-variance test
    rejects them downstream anyway)."""
    if len(points) < min_verts:
        return None
    first, last = points[0], points[-1]
    n = len(points) - 1 if first[0] == last[0] and first[1] == last[1] else len(points)
    if n < min_verts:
        return None
    arr = np.asarray(points[:n], dtype=np.float64)
    # Translate to the vertex centroid before the LS solve. This is
    # numerically essential for packaging DXFs where balls / pads sit at
    # world coordinates ~10⁵ mm: without it, the Kåsa normal-equation
    # matrix is dominated by Σx² ≈ n·cx0² and the condition number
    # explodes (~10²¹ on a 100 km × 0.3 mm test case), producing a
    # garbage centre and a radius ~10× too big. After translation the
    # matrix's spread is the cloud's own diameter, so conditioning stays
    # benign. Translation is a similarity invariant for circle fitting:
    # solving in local coordinates and adding the offset back gives the
    # same centre.
    cx0 = float(arr[:, 0].mean())
    cy0 = float(arr[:, 1].mean())
    xs = arr[:, 0] - cx0
    ys = arr[:, 1] - cy0
    # Kåsa LS in centroid-local frame:
    #   minimise Σ (D·x + E·y + F + (x² + y²))²
    # Normal equations: [Σx² Σxy Σx; Σxy Σy² Σy; Σx Σy n] · [D, E, F]ᵀ
    #                 = -[Σx(x²+y²); Σy(x²+y²); Σ(x²+y²)]
    # In the local frame Σx = Σy = 0, so the matrix block-diagonalises;
    # `linalg.solve` still handles it generically.
    x2y2 = xs * xs + ys * ys
    M = np.array([
        [(xs * xs).sum(), (xs * ys).sum(), xs.sum()],
        [(xs * ys).sum(), (ys * ys).sum(), ys.sum()],
        [xs.sum(),         ys.sum(),         float(n)],
    ])
    b = -np.array([(xs * x2y2).sum(), (ys * x2y2).sum(), x2y2.sum()])
    try:
        D, E, _F = np.linalg.solve(M, b)
        cx = cx0 - D / 2.0
        cy = cy0 - E / 2.0
    except np.linalg.LinAlgError:
        # Singular system (collinear vertices, degenerate input). Fall
        # back to the centroid — the radial-variance test downstream
        # will reject these anyway, behaviour is identical to pre-LS.
        cx = cx0
        cy = cy0
    dx = arr[:, 0] - cx
    dy = arr[:, 1] - cy
    r = np.hypot(dx, dy)
    rmin = float(r.min())
    rmax = float(r.max())
    rmean = float(r.mean())
    if rmean < 1e-9:
        return None
    extent_x = float(arr[:, 0].max() - arr[:, 0].min())
    extent_y = float(arr[:, 1].max() - arr[:, 1].min())
    max_extent = max(extent_x, extent_y)
    min_extent = min(extent_x, extent_y)
    # Safety bound on the LS-fit radius. A genuine closed circular sub-
    # path's mean radius is ≈ half its bbox extent, so `rmean` should
    # never exceed the larger bbox dimension. When it does, the LS solve
    # has fit a near-line/arc as a giant circle (the bigger-than-data
    # output the user reported). Bail out so the sub-path stays a
    # polyline / filled_polygon and isn't promoted to a wildly oversized
    # `circle` primitive.
    if rmean > max_extent:
        return None
    # Bbox-aspect gate: a true closed circle's bbox is square. A
    # rectangular outline (lid, package frame) with N ≥ 11 vertices
    # whose verts happen to be roughly equidistant from the centroid
    # passes the radial-variance test (all corners + symmetric edge
    # midpoints share a common distance) but its bbox is far from
    # square. Catching the aspect mismatch here prevents lid-shaped
    # rectangles from being promoted to inscribed circles. 10 % slack
    # absorbs the discretisation of a finite-N inscribed polygon.
    if max_extent > 0 and min_extent / max_extent < 0.9:
        return None
    if (rmax - rmin) / rmean > CIRCLE_RADIAL_TOL:
        return None
    return {"center": [float(cx), float(cy)], "r": rmean}


def _props(p: BackendProperties) -> dict[str, Any]:
    """Extract render-relevant properties as a small dict."""
    return {
        "color": _normalize_color(p.color),
        "layer": p.layer,
        "lineweight": float(p.lineweight),
        "handle": p.handle,
    }


def _normalize_color(color: str) -> str:
    """ezdxf gives '#RRGGBBAA' sometimes; canvas expects '#RRGGBB'."""
    if isinstance(color, str) and color.startswith("#") and len(color) == 9:
        return color[:7]
    return color


# ---- Layer discovery / filtering ----------------------------------------

# Filename-unsafe characters (Windows + POSIX union). We URL-encode anything
# outside [A-Za-z0-9._-] to keep the on-disk filename portable while letting
# the manifest carry the original layer name verbatim.
def sanitize_layer_name(name: str) -> str:
    """Turn a DXF layer name into a filesystem-safe filename stem."""
    safe = urllib.parse.quote(name, safe="")
    # Defensive: a totally-empty layer name (rare) gets a placeholder so
    # we never produce a zero-length filename.
    return safe or "_unnamed"


def group_primitives_by_layer(
    primitives: list[dict[str, Any]],
) -> dict[str, list[int]]:
    """Return `{layer_name: [primitive_index, ...]}` covering every prim.
    Primitives without an explicit `layer` are bucketed under `"0"` (the
    AutoCAD default)."""
    by_layer: dict[str, list[int]] = {}
    for i, p in enumerate(primitives):
        name = str(p.get("layer") or "0")
        by_layer.setdefault(name, []).append(i)
    return by_layer


def filter_primitives(
    primitives: list[dict[str, Any]],
    layers: Iterable[str],
) -> list[dict[str, Any]]:
    """Drop primitives whose `layer` is not in `layers`. Decorative
    primitives are filtered alongside on the same rule."""
    keep = {str(l) for l in layers}
    return [p for p in primitives if str(p.get("layer") or "0") in keep]


# Caps tuned to keep per-layer SVGs around 100 KB for thumbnails. Dense
# layers (e.g., a 24k-segment pad grid) get evenly subsampled — the user
# only needs to recognise the layer, not measure it.
MAX_PRIMS_PER_THUMB = 600
MAX_VERTICES_PER_POLYLINE = 24


def render_layer_svg(
    primitives: list[dict[str, Any]],
    layer_indices: list[int],
    bbox: tuple[float, float, float, float] | None,
    *,
    skip_decorative: bool = True,
    max_prims: int | None = None,
    background: str = "#212830",
) -> str:
    """Render a compact SVG preview of one layer's primitives.

    All thumbnails for a given file share the same `viewBox` (the file-wide
    bbox) so the user can compare layers in a consistent frame. The SVG
    embeds the file's background color as a backdrop and draws each
    primitive in its own color — matching what the user sees in the
    canvas viewer. Dense layers are evenly subsampled.
    """
    if max_prims is None:
        max_prims = MAX_PRIMS_PER_THUMB
    if bbox is None:
        # Degenerate file: emit an empty 1×1 viewport so consumers always
        # get a parseable SVG.
        bbox = (0.0, 0.0, 1.0, 1.0)
    xmin, ymin, xmax, ymax = bbox
    width = max(xmax - xmin, 1e-9)
    height = max(ymax - ymin, 1e-9)
    # Stroke width tuned to the file bbox so the preview reads at any zoom
    # without becoming a single fat blob on tiny layers or a hair-thin
    # outline on huge ones. 0.25% of the diagonal feels right empirically.
    stroke_w = max(width, height) * 0.0025

    # Subsample evenly if there are too many primitives. Keeps the visual
    # distribution recognisable while bounding SVG size.
    visible = [
        i for i in layer_indices
        if not (skip_decorative and primitives[i].get("decorative"))
    ]
    if len(visible) > max_prims:
        stride = len(visible) / max_prims
        visible = [visible[int(k * stride)] for k in range(max_prims)]

    bg = _safe_color(background)
    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="{_fmt(xmin)} {_fmt(ymin)} {_fmt(width)} {_fmt(height)}" '
        f'preserveAspectRatio="xMidYMid meet">'
    )
    # Solid backdrop in the file's own background color so each thumbnail
    # reads the same way the canvas viewer would render the layer.
    parts.append(
        f'<rect x="{_fmt(xmin)}" y="{_fmt(ymin)}" '
        f'width="{_fmt(width)}" height="{_fmt(height)}" fill="{bg}"/>'
    )
    # SVG y-axis points down; DXF world y-axis points up. Mirror around the
    # bbox vertical center so the thumbnail matches the canvas viewer.
    parts.append(
        f'<g transform="translate(0,{_fmt(ymin + ymax)}) scale(1,-1)" '
        f'fill="none" stroke-width="{_fmt(stroke_w)}" stroke-linecap="round" '
        f'stroke-linejoin="round">'
    )
    for idx in visible:
        parts.append(_prim_to_svg(primitives[idx]))
    parts.append("</g></svg>")
    return "".join(parts)


def _decimate_pts(pts: list[list[float]], cap: int) -> list[list[float]]:
    if len(pts) <= cap:
        return pts
    stride = len(pts) / cap
    out = [pts[int(k * stride)] for k in range(cap)]
    # Always keep the last vertex so closed shapes stay closed-looking.
    if out[-1] is not pts[-1]:
        out.append(pts[-1])
    return out


def _prim_to_svg(p: dict[str, Any]) -> str:
    color = _safe_color(p.get("color"))
    t = p.get("type")
    if t == "line":
        sx, sy = p["start"]
        ex, ey = p["end"]
        return (
            f'<line x1="{_fmt(sx)}" y1="{_fmt(sy)}" '
            f'x2="{_fmt(ex)}" y2="{_fmt(ey)}" stroke="{color}"/>'
        )
    if t == "polyline":
        decimated = _decimate_pts(p["points"], MAX_VERTICES_PER_POLYLINE)
        pts = " ".join(f"{_fmt(x)},{_fmt(y)}" for x, y in decimated)
        tag = "polygon" if p.get("closed") else "polyline"
        return f'<{tag} points="{pts}" stroke="{color}"/>'
    if t == "filled_polygon":
        # Multiple rings: render each as its own polygon so even-odd rules
        # aren't required for a thumbnail.
        out: list[str] = []
        for ring in p.get("rings", []):
            decimated = _decimate_pts(ring, MAX_VERTICES_PER_POLYLINE)
            pts = " ".join(f"{_fmt(x)},{_fmt(y)}" for x, y in decimated)
            out.append(
                f'<polygon points="{pts}" stroke="{color}" '
                f'fill="{color}" fill-opacity="0.5"/>'
            )
        return "".join(out)
    if t == "point":
        x, y = p["pos"]
        return f'<circle cx="{_fmt(x)}" cy="{_fmt(y)}" r="0" stroke="{color}"/>'
    return ""


def _fmt(v: float) -> str:
    """Compact float repr for SVG attributes (drops trailing zeros)."""
    if v == int(v):
        return str(int(v))
    return f"{v:.4f}".rstrip("0").rstrip(".")


def _safe_color(c: Any) -> str:
    if isinstance(c, str) and c.startswith("#") and len(c) in (7, 9):
        return c[:7]
    return "#000000"
