"""Developer-mode parameter overrides.

Process-wide, in-memory overrides for a curated allow-list of tunables in
`app.matching` and `app.dxf`. Mutates module attributes in place
(`setattr(module, name, value)`); the consuming code in those modules
resolves the constants by bare-name lookup at call time, so the next
match / preprocess call picks up the new value with no restart.

Single-user dev tool. NOT thread-safe with respect to in-flight match /
preprocess jobs — changing a value mid-job yields undefined behaviour.

On restart, every tunable returns to its compiled default. There is no
persistence layer by design (see OpenSpec change
`expose-dev-parameter-overrides`).
"""

from __future__ import annotations

from dataclasses import dataclass
from types import ModuleType
from typing import Any

from app import dxf as _dxf
from app import matching as _matching


@dataclass(frozen=True)
class _Spec:
    module: ModuleType
    name: str
    py_type: type
    min: float | int
    max: float | int
    description: str


# Allow-list. Adding a new tunable requires (a) the constant exists on the
# named module and (b) an entry here; the endpoint refuses anything else.
# `min`/`max` are inclusive; for ints both bounds are integers.
#
# `CURVE_FLATTENING_DISTANCE` is a documentation-only alias kept on
# `app.dxf` for legacy external readers — it is no longer consumed inside
# the preprocessing pipeline (only `BASE_TOLERANCE` is). It stays in the
# allow-list so the modal surfaces both names side by side and the
# rename is discoverable, but overriding it has no downstream effect.
# Override `BASE_TOLERANCE` instead.
_ALLOW_LIST: tuple[_Spec, ...] = (
    # matching.py — alignment and pre-filter thresholds
    _Spec(_matching, "SCALE_MIN", float, 0.5, 1.0,
          "Lower bound of accepted scale ratio (template / candidate). 0.9999 = essentially no scale slack."),
    _Spec(_matching, "SCALE_MAX", float, 1.0, 2.0,
          "Upper bound of accepted scale ratio. 1.0001 = essentially no scale slack."),
    _Spec(_matching, "TOLERANCE_ABS", float, 1e-6, 1.0,
          "Chamfer distance cap (drawing units) for a candidate to count as a match."),
    _Spec(_matching, "VERTEX_COUNT_RATIO", float, 0.0, 1.0,
          "Vertex-count band for the cheap pre-filter (currently unused — kept for parity with older builds)."),
    _Spec(_matching, "PATH_LENGTH_RATIO", float, 0.0, 1.0,
          "Path-length band for the pre-filter, ±this fraction of the template's length."),
    _Spec(_matching, "RADIUS_RATIO", float, 0.0, 1.0,
          "Max-radius-from-centroid band for the pre-filter."),
    _Spec(_matching, "SIGMA_RATIO_TOL", float, 0.0, 1.0,
          "Absolute Δ on σ₂/σ₁ ∈ [0,1] — principal-axis aspect tolerance."),
    _Spec(_matching, "RESAMPLE_N", int, 8, 1024,
          "Canonical point count every non-circle template + candidate is resampled to."),
    _Spec(_matching, "BRUTE_FORCE_CUTOFF", int, 1, 10_000,
          "Cloud size above which Chamfer uses cKDTree; at or below, brute-force numpy."),
    # dxf.py — preprocessing thresholds
    _Spec(_dxf, "BASE_TOLERANCE", float, 1e-6, 10.0,
          "Floor for curve flattening (drawing units). Tighter ⇒ more vertices ⇒ slower."),
    _Spec(_dxf, "CURVE_FLATTENING_DISTANCE", float, 1e-6, 10.0,
          "Legacy alias of BASE_TOLERANCE — no downstream effect. Override BASE_TOLERANCE instead."),
    _Spec(_dxf, "CIRCLE_MIN_VERTS", int, 3, 256,
          "Min vertex count for a closed flattened sub-path with curves to promote to a circle primitive."),
    _Spec(_dxf, "CIRCLE_RADIAL_TOL", float, 1e-9, 1.0,
          "Max (rmax − rmin) / rmean for a closed sub-path to be treated as a circle."),
    _Spec(_dxf, "MAX_PRIMS_PER_THUMB", int, 10, 10_000,
          "Per-layer SVG thumbnail cap. Higher ⇒ richer thumbnails, larger SVGs."),
    _Spec(_dxf, "MAX_VERTICES_PER_POLYLINE", int, 4, 1024,
          "Polyline-decimation cap for layer thumbnails."),
)

_BY_NAME: dict[str, _Spec] = {s.name: s for s in _ALLOW_LIST}

# Compiled defaults snapshot, taken at import time so reset reverts even
# after the module attributes have been mutated.
DEFAULTS: dict[str, Any] = {s.name: getattr(s.module, s.name) for s in _ALLOW_LIST}


class ValidationError(Exception):
    """Raised by `apply` when one or more keys fail validation. Carries a
    `{key: reason}` map so the HTTP layer can return a per-field 400."""

    def __init__(self, errors: dict[str, str]):
        super().__init__(f"invalid overrides: {errors}")
        self.errors = errors


def _module_label(module: ModuleType) -> str:
    return module.__name__.rsplit(".", 1)[-1]


def _entry_state(spec: _Spec) -> dict[str, Any]:
    return {
        "name": spec.name,
        "module": _module_label(spec.module),
        "default": DEFAULTS[spec.name],
        "current": getattr(spec.module, spec.name),
        "type": "int" if spec.py_type is int else "float",
        "min": spec.min,
        "max": spec.max,
        "description": spec.description,
    }


def read_state() -> list[dict[str, Any]]:
    """Snapshot of every allow-listed tunable for the GET endpoint."""
    return [_entry_state(s) for s in _ALLOW_LIST]


def _coerce(spec: _Spec, raw: Any) -> Any:
    """Coerce + range-check a single value. Returns the coerced value or
    raises ValueError with a human-readable reason."""
    if isinstance(raw, bool):
        # `bool` is a subclass of int in Python; refuse it explicitly so
        # `{"RESAMPLE_N": true}` is not silently accepted as 1.
        raise ValueError("expected number, got bool")
    if spec.py_type is int:
        if isinstance(raw, int):
            value: int | float = raw
        elif isinstance(raw, float) and raw.is_integer():
            value = int(raw)
        else:
            raise ValueError("expected int")
    else:
        if isinstance(raw, (int, float)):
            value = float(raw)
        else:
            raise ValueError("expected number")
    if value < spec.min or value > spec.max:
        raise ValueError(f"out of range [{spec.min}, {spec.max}]")
    return value


def apply(overrides: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate every entry in `overrides`, then mutate module attributes
    in one pass. On any validation failure the call raises
    `ValidationError` and no module attribute is touched.
    """
    if not overrides:
        raise ValidationError({"_root": "no overrides supplied"})
    errors: dict[str, str] = {}
    coerced: dict[str, Any] = {}
    for key, raw in overrides.items():
        spec = _BY_NAME.get(key)
        if spec is None:
            errors[key] = "not in allow-list"
            continue
        try:
            coerced[key] = _coerce(spec, raw)
        except ValueError as exc:
            errors[key] = str(exc)
    if errors:
        raise ValidationError(errors)
    for key, value in coerced.items():
        spec = _BY_NAME[key]
        setattr(spec.module, spec.name, value)
    return read_state()


def reset() -> list[dict[str, Any]]:
    """Re-assign every allow-listed attribute to its compiled default."""
    for spec in _ALLOW_LIST:
        setattr(spec.module, spec.name, DEFAULTS[spec.name])
    return read_state()


def snapshot_non_default() -> dict[str, Any]:
    """Return only the entries currently diverging from compiled defaults.

    Used to ferry the active overrides into worker processes (which
    re-import modules fresh and would otherwise see only the compiled
    defaults). Empty dict means "nothing to apply".
    """
    out: dict[str, Any] = {}
    for spec in _ALLOW_LIST:
        cur = getattr(spec.module, spec.name)
        if cur != DEFAULTS[spec.name]:
            out[spec.name] = cur
    return out


def apply_snapshot(snapshot: dict[str, Any]) -> None:
    """Re-apply a `snapshot_non_default()` payload without re-validating.

    Trust-based: only call with a dict you produced yourself. Skips
    allow-list lookups silently for unknown keys so a snapshot taken on
    a server with a richer allow-list still works on a stripped-down one.
    """
    for name, value in snapshot.items():
        spec = _BY_NAME.get(name)
        if spec is None:
            continue
        setattr(spec.module, spec.name, value)
