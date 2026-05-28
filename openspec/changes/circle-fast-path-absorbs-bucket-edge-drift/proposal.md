## Why

`9d66024` ("Matching: analytical CIRCLE fast path in `build_entity_shapes`")
made circle drawing-shapes use an analytical radius (`from_circle(h,
cx, cy, r) → radius = float(r)`) instead of recomputing it from
synthesised points. The library-stored template path is unchanged:
templates persist as `entity_point_sets`, reload through
`EntityShape.from_points(stored_pts, kind="circle")`, and that
constructor RECOMPUTES `radius = max(|pts - centroid|)` numerically.

For most DXFs the two radii bucket identically under
`_radius_bucket_key(r) = round(r * 10^4)`. But when a circle's
`r·10^4` lands at a `.5` fence-post (banker's rounding boundary),
ULP-scale FP drift between the analytical r and the
recomputed-from-points r can push them into **adjacent integer
buckets**. `_match_single_circle` then misses the entire bucket and
returns `0` matches. Bisect (user-confirmed): `0c03d61` GOOD,
`9d66024` BAD.

Symptom on the affected user-supplied DXF: `BGABall` + `C4Ball` +
`FiducialCircle` all disappear from scan-all + Save Match JSON,
while the handle-based `/match` endpoint still finds them (both
template and candidate route through `from_circle`'s analytical r,
same bucket). The handle path was not regressed; only the
stored-template path is.

## What Changes

- **`app/matching.py` `_match_single_circle(template, drawing, skip)`**:
  Look up a 3-neighbour window `(key-1, key, key+1)` instead of just
  `key`. Each handle lives in exactly one bucket, so concatenation
  needs no dedup. The rest of the function (MatchResult construction
  with `score=0.0, scale=1.0`, skip filter, no-near-miss invariant)
  is unchanged.
- **`tests/test_matching_circle_fast_path.py`**: add one regression
  test (`test_circle_fast_path_absorbs_bucket_edge_drift`) that
  constructs a template whose recomputed `from_points` radius lands
  on the FAR side of a `.5` fence-post from a drawing circle's
  analytical r; pre-fix the bucket lookup misses, post-fix it finds.

## Capabilities

### New Capabilities

_None._

### Modified Capabilities

- `pattern-matching`: adds one ADDED requirement scoped to the
  single-CIRCLE radius-bucket fast path's bucket-edge behaviour.
  No existing requirement is modified or removed. (The parent
  fast-path requirement is still in-flight in
  `add-circle-scan-fast-path` and not yet merged into the live
  spec; the new requirement here is independent and additive, so
  either archive order works.)

## Impact

- **Code**: `app/matching.py` only — the `_match_single_circle`
  function body, ≈4 lines changed (replace single `.get(key, [])`
  with the 3-key loop).
- **APIs**: none. Endpoint shapes (`/api/files/<id>/scan-all`,
  Save Match JSON, `/api/files/<id>/match`) are unchanged.
- **Bucket grid**: still `CIRCLE_RADIUS_KEY_DIGITS = 4`
  (`10^-4 mm = 0.1 µm`); ±1 lookup widens the accepted radius window
  to ~`±1.5e-4 mm` (`±0.15 µm`). Real packaging classes differ by
  ≥ 1 µm radius (≥ 10 buckets), so ±1 cannot reach across a real
  design boundary; the only thing it absorbs is intra-class FP drift
  at the rounding fence-post.
- **Tests**: existing `tests/test_matching_circle_fast_path.py` (7
  tests) stays green — they all use radii either > 1 bucket apart
  (rejection cases) or with FP noise well below the bucket boundary
  (acceptance cases). One new regression test added.
- **Dependencies**: none.
- **Operational / migration**: none. No data on disk, no API moves.
  The first deploy that ships the new `matching.py` takes effect on
  the next scan-all / save-match invocation; cached `rule_check.json`
  and `match.json` files are unaffected.
- **Performance**: O(1) → 3 × O(1) dict lookups in the fast path;
  negligible vs the rest of the matching pipeline.
- **Relationship to other in-flight changes**:
  - `add-circle-scan-fast-path` introduced the fast path itself
    (still pending archive). The requirement it adds —
    "Single-CIRCLE template fast path via radius bucket" — defines
    `key = round(radius * 10**CIRCLE_RADIUS_KEY_DIGITS)` and "return
    every bucketed handle". This change extends behaviour with a
    boundary-drift clause on a separate requirement, so the two are
    composable regardless of archive order.
  - `improve-circle-fit-least-squares` (still pending archive)
    changes how polylines get promoted to CIRCLE primitives in DXF
    parsing. Orthogonal to this change — the fix lives downstream of
    parsing, on the matcher's lookup side.
