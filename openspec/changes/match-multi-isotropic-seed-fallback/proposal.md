## Why

`_match_multi` (`app/matching.py:978-`) recovers each candidate's
rigid transform by computing `_pca_axes` on the candidate seed entity
and trying four sign variants `(±1, ±1)` to cover mirror / 180° rotation
ambiguity. This works when the seed's shape is *anisotropic*
(`σ₂/σ₁` clearly less than 1) — PCA's principal axes have a stable
orientation determined by the geometry, and the four sign variants
exhaustively cover the rotation ambiguity.

It breaks when the seed is *isotropic* (`σ₂/σ₁ ≈ 1`): for a circular
mark, an axisymmetric cross, a square pad with rotational symmetry,
PCA's principal axes have no canonical direction — they point in
whichever direction `µm`-scale numerical noise happens to push the
eigen decomposition. Two copy-pasted instances of the *same* shape
produce PCA axes pointing in random, unrelated directions. The four
sign variants only cover 90°/mirror flips, not arbitrary rotation, so
the `expected = local_pos @ scaled_axes + cand.centroid` prediction
overshoots `CENTROID_NOISE_TOL = 1e-3 mm`, the KDTree
`query_ball_point` finds nothing, and the candidate is rejected.

User-reported symptom: a row of copy-paste SMDs where every SMD has
the same rounded-square corner mark on each side. Selecting the two
marks of one SMD as a 2-entity template and running scan-all matches
*some* of the row's SMDs and misses others — pattern irregular,
purely driven by the random PCA orientations. Adding the SMD itself
to the template (3 entities) finds all SMDs, because the extra
verification step happens to mask the random-axis prediction error.

## What Changes

- **`app/matching.py`** — `_match_multi` SHALL detect isotropic seeds
  (defined as `σ₂/σ₁ > 0.95`) and use a **2-point alignment**
  fallback for candidate enumeration:
  - For each candidate seed handle, iterate over the first
    other-template-entity's fingerprint bucket
  - For each `(cand_seed, cand_other_first)` pair where the
    drawing-side distance matches the template-side distance within
    `CENTROID_NOISE_TOL`, recover the rigid transform from the line
    between the two centroids (`atan2` rotation + translation), no
    PCA involved
  - For 3+ entity templates, also try the mirrored variant of that
    rotation (one bit of mirror ambiguity remains; 2 candidate
    transforms vs the current 4 sign variants — cheaper, not more
    expensive)
  - For each remaining other template entity, predict its world
    centroid under that rigid transform and verify via the
    existing KDTree + fingerprint check
- **Non-isotropic seed path is untouched** — the PCA-based code
  remains the default; the new path activates only when the cheap
  `σ₂/σ₁` check trips.
- **Tests** —
  - New regression test: 2-entity template made of two isotropic
    shapes (squares), planted as a row of ≥5 copies. Assert every
    copy matches. This locks in the fix.
  - New unit test: `_sigma_ratio` returns the expected range for
    canonical shapes (circle ≈ 1.0, square ≈ 1.0, narrow rectangle
    < 0.5, thin line ≈ 0).
- **Existing tests** — non-isotropic property tests (`test_match_multi_symmetry_property`
  random patterns, three-rect-SMD tests, close-packed-neighbour
  tests, frame-select-stacked-duplicates) do not enter the new path
  and remain green.

## Capabilities

### New Capabilities

_None._ This is a defect fix in an existing capability.

### Modified Capabilities

- `pattern-matching`: the `Multi-entity template matching
  (pose-based)` requirement gains an additional clause for the
  isotropic-seed fallback. Behaviour for anisotropic seeds is
  preserved verbatim.

## Impact

- **Code**: `app/matching.py` — a new helper for the isotropic
  fallback path inside `_match_multi`; bug-free dispatch at the top
  of the candidate-enumeration block. No new public APIs.
- **APIs**: none. `find_matches`, `find_matches_from_pointsets`,
  scan-all, save-match all behave the same on the surface — they
  just find more matches when the template is isotropic.
- **Tests**: 2 new tests (1 regression + 1 unit). Existing matching
  test suite (~20 tests) stays green.
- **Perf**: the isotropic fallback iterates the first other-entity's
  bucket for every candidate seed. For typical SMD scans this is the
  same order as the current PCA path's per-candidate verification —
  no measurable regression. The recently-merged scan-all perf guard
  (`test_scan_all_perf_does_not_regress`) protects against a real
  blowup.
- **Operational**: the user's row-of-SMD case (and any other 2-entity
  isotropic-template scan-all) goes from "partial recall" to "full
  recall". No data migration; no behaviour change for non-isotropic
  templates.
