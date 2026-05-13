"""DXF parsing and flattening.

Two responsibilities:
1. `flatten_for_render(path)`: turn a DXF into JSON-serialisable drawing
   primitives (line / polyline / filled_polygon / point). ezdxf's Frontend
   handles OCS, INSERT expansion, bulge, text-to-paths, etc. Curves are
   flattened to polylines for trivial canvas rendering.
2. (Later) `extract_entities(path)`: structured entity export for the
   matching engine — preserves CIRCLE/ARC/LINE primitives.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import ezdxf
from ezdxf.addons.drawing import Frontend, RenderContext
from ezdxf.addons.drawing.backend import BackendInterface
from ezdxf.addons.drawing.config import Configuration
from ezdxf.addons.drawing.properties import BackendProperties
from ezdxf.math import Vec2
from ezdxf.npshapes import NumpyPath2d, NumpyPoints2d


# Max deviation (drawing units) when flattening curves to polylines.
# Smaller = more vertices, smoother arcs. 0.01 is fine for typical mm-scale CAD.
CURVE_FLATTENING_DISTANCE = 0.01


@dataclass
class RenderOutput:
    primitives: list[dict[str, Any]]
    bbox: tuple[float, float, float, float] | None  # (xmin, ymin, xmax, ymax)
    background: str  # "#RRGGBB"


class JSONBackend(BackendInterface):
    """Collects drawing operations as JSON-serialisable primitive dicts."""

    def __init__(self) -> None:
        self.primitives: list[dict[str, Any]] = []
        self.background: str = "#ffffff"
        self._xmin = float("inf")
        self._ymin = float("inf")
        self._xmax = float("-inf")
        self._ymax = float("-inf")

    # ---- lifecycle (no-ops for our use) ------------------------------------
    def configure(self, config: Configuration) -> None:
        pass

    def clear(self) -> None:
        self.primitives.clear()

    def finalize(self) -> None:
        pass

    def enter_entity(self, entity, properties) -> None:  # noqa: ARG002
        pass

    def exit_entity(self, entity) -> None:  # noqa: ARG002
        pass

    def set_background(self, color: str) -> None:
        self.background = _normalize_color(color)

    # ---- draw operations ---------------------------------------------------
    def draw_point(self, pos: Vec2, properties: BackendProperties) -> None:
        x, y = float(pos.x), float(pos.y)
        self._track_point(x, y)
        self.primitives.append(
            {"type": "point", "pos": [x, y], **_props(properties)}
        )

    def draw_line(self, start: Vec2, end: Vec2, properties: BackendProperties) -> None:
        sx, sy, ex, ey = float(start.x), float(start.y), float(end.x), float(end.y)
        self._track_point(sx, sy)
        self._track_point(ex, ey)
        self.primitives.append(
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
            self.primitives.append(
                {"type": "line", "start": [sx, sy], "end": [ex, ey], **common}
            )

    def draw_path(self, path: NumpyPath2d, properties: BackendProperties) -> None:
        for sub in path.sub_paths():
            points = _flatten_path(sub)
            if len(points) < 2:
                continue
            self._track_points(points)
            self.primitives.append(
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
        rings: list[list[list[float]]] = []
        for path in paths:
            for sub in path.sub_paths():
                pts = _flatten_path(sub)
                if len(pts) >= 3:
                    self._track_points(pts)
                    rings.append(pts)
        if rings:
            self.primitives.append(
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
        self.primitives.append(
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


def flatten_for_render(dxf_path: str | Path) -> RenderOutput:
    """Parse a DXF file and return drawing primitives + bbox."""
    doc = ezdxf.readfile(str(dxf_path))
    ctx = RenderContext(doc)
    backend = JSONBackend()
    Frontend(ctx, backend).draw_layout(doc.modelspace(), finalize=True)
    return RenderOutput(
        primitives=backend.primitives,
        bbox=backend.bbox,
        background=backend.background,
    )


# ---- helpers ---------------------------------------------------------------
def _flatten_path(sub: NumpyPath2d) -> list[list[float]]:
    return [[float(v.x), float(v.y)] for v in sub.flattening(CURVE_FLATTENING_DISTANCE)]


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
