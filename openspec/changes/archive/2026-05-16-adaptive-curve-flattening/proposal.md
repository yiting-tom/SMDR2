## Why

DXF files in the wild routinely arrive with `$INSUNITS = 0` (unitless)
or with an authoring-time unit-scale bug that inflates every coordinate
by 1000× (the designer worked in mm but the file is being interpreted
as m downstream, or vice versa). ezdxf's curve flattening uses a fixed
`CURVE_FLATTENING_DISTANCE = 0.01` in raw drawing units, so when a
file's natural scale jumps 1000×, a curve that previously needed
~7 chord segments needs ~`π · √(1000)` ≈ 32× more — and a 1 M-entity
file produces ~1.2 GB of parsed JSON.

Chrome / V8 caps a single `String` at ~512 MiB, so the viewer's
`response.json()` call throws `RangeError: Invalid string length`
before any rendering can even begin. The file is functionally
un-openable, even though geometrically it would be straightforward
once parsed.

The fix is purely geometric: scale the flattening tolerance by the
file's bounding-box diagonal so chord count stays bounded regardless
of the file's nominal unit. Normal-scale DXFs (diagonal ~ 1 m for
packaging) are unaffected; pathological-scale DXFs auto-relax their
tolerance and become loadable.

## What Changes

- **Backend** (`app/dxf.py:flatten_for_render`): pre-scan the modelspace
  (cheap header / extents read via `ezdxf.bbox.extents`) to estimate
  the bbox diagonal `D`, then derive a per-file
  `flatten_tolerance = max(BASE_TOLERANCE, D * SCALE_FACTOR)`
  (`BASE_TOLERANCE = 0.01`, `SCALE_FACTOR = 1e-5`). Thread the
  per-file value through `_flatten_path` and any other call site that
  currently hard-codes `CURVE_FLATTENING_DISTANCE`.
- **Backend** (`app/dxf.py`): keep `CURVE_FLATTENING_DISTANCE = 0.01`
  as the BASE_TOLERANCE constant for backward compatibility, but
  rename / reframe the requirement so call sites consult the
  per-file value instead of the module constant where applicable.
- **Telemetry**: log `bbox diagonal` and the chosen `flatten_tolerance`
  whenever it differs from the base — surfaces immediately when a
  user uploads a unit-scale-busted file.

No frontend changes. No persisted-format changes (the resulting
primitives are the same shape, just with fewer vertices per curve on
oversized files).

## Capabilities

### New Capabilities
<!-- None — refines an existing capability. -->

### Modified Capabilities

- `dxf-pipeline`: the `Server-side DXF flatten` requirement currently
  hard-codes a "default 0.01 drawing units" tolerance. It SHALL be
  reframed as **bbox-scaled**: tolerance derives from the file's
  bounding-box diagonal so vertex count stays bounded across pathological
  unit scales.

## Impact

- **Code**: `app/dxf.py` (`flatten_for_render`, `_flatten_path`,
  `JSONBackend.draw_path` if it calls the helper). The
  `_detect_circle_subpath` helper added in `optimize-bga-render` is
  scale-invariant (uses *relative* radial variance) and needs no
  change.
- **Tests**: `tests/test_dxf.py` gets a fixture that builds an arc /
  spline at two nominal scales (1× and 1000×) and asserts the
  primitive count is comparable. A second test asserts the base
  case (small DXF) is byte-identical to before, modulo the new
  per-file tolerance code path.
- **Persisted artifacts**: existing `data/parsed/*.json` files are
  unaffected — re-preprocess to pick up the new tolerance.
- **No impact** on: matching engine (handle-based), rule check,
  library DB, viewer renderer, OpenSpec specs other than
  `dxf-pipeline`.
- **Interaction with `optimize-bga-render`**: orthogonal and additive.
  CIRCLE entities never go through chord flattening once that change
  lands; this change targets the remaining curve types
  (ELLIPSE, SPLINE, partial ARC, etc.) and pathological-scale files.
