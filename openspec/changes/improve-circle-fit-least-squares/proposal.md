## Why

`_detect_circle_subpath` (`app/dxf.py:372`) currently estimates the
circle centre with the arithmetic mean of vertex positions. That is the
geometric centroid of the *vertex set*, not of the underlying circle.
When the vertices are unevenly spaced along the perimeter (a polyline
sampled densely on one side and sparsely on the other — common when a
CAD operator manually places extra vertices on a curved corner of an
otherwise N-gon outline), the centroid drifts toward the dense side.
The drifted centre then inflates `rmin / rmax / rmean`, which either:

- wrongly rejects a real circle as out-of-tolerance (false negative), or
- promotes a polyline to a `circle` primitive whose `center` is offset
  from the real geometric centre — propagating the offset into the
  viewer's rendered position and the matcher's radius-bucket key.

A least-squares circle fit produces an unbiased centre regardless of
vertex spacing.

## What Changes

- `_detect_circle_subpath` SHALL replace the centroid-as-centre with a
  Kåsa algebraic least-squares fit:
  - Minimise Σᵢ (xᵢ² + yᵢ² + D·xᵢ + E·yᵢ + F)² over D, E, F via the
    closed-form 3×3 normal-equation solution.
  - Centre `(cx, cy) = (-D/2, -E/2)`.
  - Radius `r = √((D² + E²)/4 − F)` (used as the seed; the radial-variance
    test still recomputes per-vertex distances).
- When the 3×3 normal-equation matrix is singular (collinear vertices,
  degenerate input, |determinant| below a small epsilon), the function
  SHALL fall back to the centroid-based estimate so degenerate sub-paths
  produce the same answer as today.
- The radial-variance predicate stays unchanged: after the centre is
  resolved (LS or fallback), recompute `r_i = hypot(xᵢ − cx, yᵢ − cy)`
  for each vertex and apply `(rmax − rmin) / rmean ≤ CIRCLE_RADIAL_TOL = 0.02`.
- The two min-verts thresholds (`CIRCLE_MIN_VERTS = 8`,
  `CIRCLE_MIN_VERTS_NOCURVE = 11`) and the radial tolerance stay the
  same. No new public constants. No new public functions.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `dxf-pipeline`: the circle-detection predicate's centre-estimation
  step is upgraded to LS. The rest of the contract (input shape,
  output primitive shape, thresholds) is unchanged.

## Impact

- `app/dxf.py`: replace the `sx / sy / cx / cy` block in
  `_detect_circle_subpath` with a Kåsa fit; add a small private helper
  if the math reads cleaner as a separate function.
- `tests/test_dxf.py`: add a test that constructs an unevenly-sampled
  near-circle (e.g., 60 vertices densely placed on one arc, 6 on the
  rest) — under the current centroid estimate the centre is visibly
  pulled toward the dense arc; under LS the centre matches the
  geometric centre within `1e-6 × r`.
- `openspec/specs/dxf-pipeline/spec.md`: update the "Server-side DXF
  flatten" requirement language to specify Kåsa LS for the centre
  estimate while keeping the radial-variance threshold as the accept
  predicate.
- No DB / API / UI / matcher changes. Existing tests for uniformly-spaced
  circles and N-gon polylines SHALL continue to pass — LS produces the
  same centre as the centroid for uniformly-spaced vertices on a circle.
