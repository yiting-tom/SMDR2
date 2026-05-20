## Context

`_detect_circle_subpath(points, min_verts)` is called once per closed
sub-path that survives the min-verts gate, from two places:
`JSONBackend.draw_path` (stroked closed paths — CIRCLE, ARC 360°,
closed LWPOLYLINE / POLYLINE) and `JSONBackend.draw_filled_paths` (the
HATCH-bounded case, which is now mostly dead code after HATCH stripping
in commit `a64703b`). It returns either `None` (not a circle within
tolerance) or `{"center": [cx, cy], "r": float}`.

Today it computes `(cx, cy) = (Σx/n, Σy/n)` then accepts the sub-path
when `(rmax − rmin) / rmean ≤ CIRCLE_RADIAL_TOL = 0.02`.

The arithmetic mean of vertices is the circle's geometric centre *only*
when the vertices are uniformly spaced on the perimeter. The
high-vertex-count regime this function runs in (N ≥ 8 with curves, N ≥ 11
pure-line) usually IS uniformly spaced — ezdxf's `flattening` produces
arclength-uniform samples and operator-authored N-gons are by definition
regular polygons. But two regimes break the assumption:

1. ezdxf flattens curves with adaptive subdivision near
   high-curvature regions, and for arcs combined with line segments in
   the same sub-path the line portions get fewer samples than the curve
   portions.
2. Operators sometimes add extra vertices to one side of an otherwise
   regular polygon (a snap-to-feature edit on one corner) — leaving
   that side dense and the rest sparse.

In both cases the centroid drifts toward the dense region. LS fitting
removes the bias.

## Goals / Non-Goals

**Goals:**
- Estimate the circle centre without bias from vertex spacing.
- Closed-form computation (no iteration) — the function is in a hot
  path called per-sub-path during DXF flatten.
- Graceful degeneracy: collinear vertices or numeric singularities
  fall back to the existing centroid estimate so behaviour matches
  today's for the degenerate cases.
- No new tunables; same thresholds, same call sites, same return shape.

**Non-Goals:**
- Not changing the radial-variance acceptance criterion. Whether a
  sub-path *is* a circle is still the same predicate; only the centre
  estimate it uses is upgraded.
- Not implementing Pratt / Taubin (geometric LS) — they fix the small
  bias toward smaller radii that algebraic LS shows on noisy inputs.
  Our inputs are near-perfect (vertices either lie on a CAD-authored
  circle or are tessellated by ezdxf) and the radial-variance threshold
  is `0.02`, four to five orders of magnitude looser than any algebraic
  LS bias on realistic substrate / pad / ball geometry.
- Not generalising to ellipse fitting. Ellipse detection is out of
  scope; we only collapse circular sub-paths.

## Decisions

**Use Kåsa algebraic LS** (over: Pratt, Taubin, geometric iterative).

- Closed form, three linear normal equations, one 3×3 solve. Cheaper
  than the existing per-vertex Python loop in raw cost terms and
  cleanly vectorisable with numpy. Already a numpy-available codebase.
- Algebraic LS bias is irrelevant at our vertex-count and radial-tolerance
  regime (see Non-Goals).

**Translate to local frame before solving**.

- Discovered during shake-out (commit `b97de10`): packaging DXFs sit
  at large absolute coordinates (10⁴–10⁵ mm) and the raw-coordinate
  Kåsa solve has matrix condition number ~10²¹ — the radius came back
  ~14× too big on a 100 km × 0.3 mm ball. Solving in the cloud's local
  frame (translate by vertex-centroid before, add the offset back
  after) reduces the spread of matrix entries to the cloud's diameter
  and keeps conditioning benign. Mathematically equivalent (circle
  fitting is translation-invariant), numerically critical.

**Two safety bounds in addition to the existing radial-variance test**.

- `rmean > max_extent` (cloud-bbox larger dimension): bounds the LS
  result against the data itself. A genuine closed circle has
  `rmean ≈ extent / 2`; if rmean exceeds the bbox the LS solve fit a
  near-line / shallow-arc as a giant circle. The existing radial
  variance test wouldn't catch this because the points DO sit on that
  giant circle (within 2 % of its huge radius). Rejecting here keeps
  the sub-path as a polyline / filled_polygon.
- `min_extent / max_extent < 0.9` (bbox not square): catches a
  surprising rectangular-lid failure mode. A 30×20 mm rectangle drawn
  with 12 vertices (4 corners + 8 edge midpoints) has 4 corners at one
  fixed distance from centroid and 8 midpoints at another fixed
  distance — and for certain aspect ratios these two values pool to a
  variance under 2 %. The detector then emits a `circle` primitive
  whose radius is the corner-to-centroid distance, visibly oversized
  next to the rectangular lid. A true circle has a square bbox; the
  aspect gate rejects rectangles. 10 % slack absorbs the
  discretisation of a finite-N inscribed polygon (a 12-gon's bbox
  isn't perfectly square depending on rotation).

**Singular-matrix fallback returns centroid** (over: raise / skip).

- A singular 3×3 means the vertices are collinear (or numerically
  near-collinear). Today's centroid-based predicate would also reject
  these as non-circular at the radial-variance test, since collinear
  points have huge (rmax − rmin) / rmean. Falling back to centroid
  preserves the exact-same behaviour for these inputs — no behaviour
  change for degenerate sub-paths.

**Threshold for "singular"**: `|det(M)| < 1e-12 × M_scale²`, where
`M_scale` is the median absolute value of the matrix's non-diagonal
entries (a translation-invariant scale proxy). Scaled because the
matrix entries are products of vertex coordinates and can be huge for
large-scale DXFs (modelspace diagonals up to 10⁵). An absolute epsilon
would either trigger spuriously at small scale or never trigger at
large scale.

**Implementation footprint**: inline the Kåsa fit inside
`_detect_circle_subpath` rather than expose a separate module-level
helper. The function stays under ~50 lines and the math is more legible
when colocated with the radial-variance test that consumes it.

**Use numpy `linalg.solve` not a hand-rolled 3×3 inverse**: numpy is
already a hot dependency in this file's neighbourhood (the matcher
loads it eagerly). `linalg.solve` raises `numpy.linalg.LinAlgError` on
singular matrices, which catches the fallback case cleanly without
manually checking determinants. Cheap singular test (compare to
fallback). Caller catches `LinAlgError` and applies the centroid
fallback.

## Risks / Trade-offs

- [LS centre drifts slightly *away* from a hand-targeted centre that an
  operator wanted to preserve] → Mitigation: today's centroid IS the
  hand-targeted centre only when vertices are uniformly placed; if the
  operator wanted a specific centre they'd have authored a CIRCLE
  entity, not a polyline. For a polyline, LS centre is the right
  estimate.
- [Algebraic LS bias on noisy inputs underestimates radius slightly]
  → Mitigation: irrelevant at 2% radial-variance tolerance; the bias
  on a near-perfect 11-gon is ~5e-5 × r — six orders of magnitude
  below the test threshold.
- [Performance regression on huge files with many sub-paths]
  → Mitigation: a 3-element numpy solve per sub-path is comparable to
  the existing per-vertex Python loop. Net cost is at worst flat.
- [Centroid fallback hides numerical issues] → Mitigation: the
  radial-variance test downstream catches false-positives. A degenerate
  matrix that survives to LS but should have been rejected upstream
  still gets rejected at the variance test.
