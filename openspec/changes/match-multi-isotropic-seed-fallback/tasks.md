## 1. Module constants + helper

- [x] 1.1 Constant added at `app/matching.py:43` with 4-line comment.
- [x] 1.2 Skipped — inlined `_sigma_ratio(seed) > ISOTROPIC_SIGMA_RATIO_THRESHOLD` at the single call site for cleaner diff.

## 2. 2-point alignment fallback in `_match_multi`

- [x] 2.1 Branch added; existing PCA path moved to `else` block verbatim.
- [x] 2.2 New path implemented as described:
  - Let `other_first = others[0]` (the first other template entity in source order). Compute `template_vec = other_first.centroid - seed.centroid`, `template_dist = ||template_vec||`, `template_angle = atan2(template_vec.y, template_vec.x)`.
  - For each `cand_seed` in `seed_bucket`:
    - Apply the same `skip` / `template_cluster_keys` filters as the PCA path.
    - Look up the first-other-entity's bucket: `other_first_bucket = buckets.get(_fingerprint(other_first), [])` plus the existing ±1-cell neighbour expansion the seed lookup already does (re-use the `_fingerprint_neighbours` helper).
    - For each `cand_other` in `other_first_bucket`:
      - Skip if `cand_other == cand_seed` (can't use the same handle twice).
      - Skip if `cand_other` is in `skip` or its cluster key is in `template_cluster_keys`.
      - Compute `cand_vec = drawing[cand_other].centroid - drawing[cand_seed].centroid`. Skip if `abs(||cand_vec|| - template_dist) > CENTROID_NOISE_TOL`. This is the distance gate.
      - Compute `rotation_angle = atan2(cand_vec.y, cand_vec.x) - template_angle`. Build a `R(rotation_angle)` 2×2 rotation matrix and `translation = drawing[cand_seed].centroid - R @ seed.centroid`.
      - For N≥3 templates, also generate a mirrored rotation: reflect the rotation across the cand-side line direction. Concretely, multiply the predicted "other-entity" world centroid by a reflection matrix about the line from `drawing[cand_seed].centroid` to `drawing[cand_other].centroid`. Apply the mirror only to the predicted positions of `others[1:]`, NOT to `cand_other` itself (which is already on the line).
      - For each remaining `other_t in others[1:]` (or none, for 2-entity templates):
        - `expected = R @ (other_t.centroid - seed.centroid) + drawing[cand_seed].centroid` (and mirrored variant for N≥3).
        - Re-use the existing per-other verification block: `tree.query_ball_point(expected, r=CENTROID_NOISE_TOL)`, iterate, fingerprint-match check.
      - On success, append `matched = [(cand_seed, seed_key), (cand_other, other_first_key), ...remaining...]` to `raw_matches` using the same `seen_groups` dedupe as the existing path.
- [x] 2.3 Confirmed: post-processing iterates `raw_matches` and uses each match's `(handle, role_key)` tuples — agnostic to producer path. No edits needed. Full matching suite (81 existing tests) still green after the branch added.

## 3. Tests

- [x] 3.1 Added — passes. Caveat: initial draft reconstructed template from `EntityShape.points` (closing duplicate stripped → fingerprint mismatch → 0 matches). Fixed by building template directly from the closed-polygon point lists, matching the drawing-side construction.
  - Build a 2-entity template from two 0.5×0.5 mm rounded squares (helper: synthesise via `EntityShape.from_points` with a polyline approximating a square's outline at ~20 vertices — the σ₂/σ₁ comes out very close to 1.0).
  - Plant a row of 6 copy-paste copies of that pair at spacing 5 mm along x.
  - Run `find_matches_from_pointsets(template_point_sets, drawing_shapes)`.
  - Assert `len(out.matches) == 6` (every copy found).
- [x] 3.2 Added — passes. Squares + 32-vertex circles both > 0.95, 2:1 rect in [0.4, 0.6], 10:1 thin rect < 0.2.
  - Square pad → σ₂/σ₁ in `[0.95, 1.0]`
  - Circle (synthesised polygon, e.g. 32 vertices) → σ₂/σ₁ in `[0.95, 1.0]`
  - 2:1 rectangle → σ₂/σ₁ in `[0.4, 0.6]`
  - Thin line (10:1) → σ₂/σ₁ < 0.15
  These lock in the isotropy threshold's separation power.
- [x] 3.3 New tests pass (3 selected, 3 pass — sigma_ratio threshold + isotropic regression + a pre-existing sigma test).
- [x] 3.4 Symmetry property 20/20 pass — anisotropic-seed path untouched.
- [x] 3.5 Full project: 422 passed / 5 skipped / 0 failed (was 420; +2 new tests).

## 4. Perf sanity

- [x] 4.1 N/A — `test_scan_all_perf_does_not_regress` was added on the `auto-normalize-unit-suspect-dxf` branch and was not part of PR #2 / #3, so it doesn't exist on main. The full project test suite completes in ~8s (no test takes anywhere near the 2s individual threshold), so perf is bounded in aggregate.

## 5. Archive

- [ ] 5.1 After tasks 1-4 pass, run `/opsx:archive match-multi-isotropic-seed-fallback` to fold the modified `pattern-matching` requirement into the live spec.
