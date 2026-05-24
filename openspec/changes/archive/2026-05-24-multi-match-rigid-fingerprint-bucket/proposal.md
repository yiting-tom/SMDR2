## Why

The current `_match_multi` was designed for a "find similar shapes within
tolerance" problem: scale window, chamfer distance, ±20% signature gates,
PCA-aligned point-cloud matching. The actual SMDR2 data model is much
narrower — every named object (SMD-2T, BGA pad, etc.) in a packaging DXF
is bit-identical copies of one master pattern transformed only by
translate / rotate / mirror. There is no scale variation, no shape drift,
no vertex-count variation across instances. The matcher pays the cost of
chamfer-based similarity matching to solve a rigid-transform congruence
problem.

Two concrete pains today:

1. **Speed.** Every candidate seed is enumerated by walking the full
   handle list and running `signatures_compatible`; then for every
   candidate that passes, the pose-hypothesis loop calls `align_score`
   (resample + 4 cKDTree builds + 4 chamfer queries) per nearby entity.
   Scan-all on real packaging files takes seconds and feels sluggish in
   the viewer. A previous attempt to precompute template-side state
   inside the existing pipeline (`speedup-multi-match-precompute`,
   superseded) measured a clean 12% win for the precompute alone and a
   40% regression for the chamfer seed-gate — i.e. the existing
   algorithm has narrow room to optimise.

2. **A wrong-shape leak.** A drawing entity that passes the cheap
   signatures gate but is geometrically the wrong shape can slip into a
   match group whenever the other template entities happen to align at
   predicted positions. The seed itself is never shape-verified.
   `test_match_multi_wrong_shape_seed_rejected` is xfail today
   documenting this.

## What Changes

- Replace `_match_multi` with a rigid-transform / fingerprint-bucket
  algorithm:
  - Pre-bucket every drawing entity by a quantised fingerprint
    `(round(path_length, k), round(radius, k), round(sigma_ratio, k))`
    on the drawing-level cache (same lifetime + invalidation as the
    existing `_radius_bucket_cache`).
  - Enumerate candidate seeds only from `buckets[seed_fingerprint]` —
    no more full-handle scan + signature filter.
  - For each candidate seed, recover the rigid transform `(R, t)` from
    the seed's PCA axes (4 sign variants for the mirror/180° ambiguity),
    predict each "other" template entity's world centroid via `R · c + t`,
    look the entity up by centroid KDTree with a numeric-noise tolerance
    (~1e-6), and require the fingerprint to match.
  - Do not call `align_score` / chamfer anywhere on the multi path.
- **BREAKING (intentional)**: a candidate that today produces a fuzzy
  match (e.g. scale 0.997, chamfer 0.18) will no longer match — the new
  matcher requires bit-identical-modulo-rigid copies. This matches the
  user's stated data contract for packaging DXFs.
- Flip `test_match_multi_wrong_shape_seed_rejected` from `xfail` back to
  a normal passing test; the new matcher satisfies it by construction.
- Keep the three existing `_match_multi` reference scenarios (triangle,
  4-pad SMD, dense neighbours) passing with identical handle sets and
  `scale = 1.0` exactly.
- Single-entity / circle-fast-path / signature-mode paths are
  untouched.

## Capabilities

### New Capabilities
<!-- None — this is an algorithmic replacement inside an existing capability. -->

### Modified Capabilities
- `pattern-matching`: replace the chamfer-based multi-entity requirement
  with a rigid-transform congruence requirement, and document the
  fingerprint-bucket pre-index that makes seed enumeration constant-time
  per bucket.

## Impact

- Code: `app/matching.py` (`_match_multi` rewrite; new private
  fingerprint helper + bucket cache; small refactor of
  `_get_radius_buckets` for symmetry if helpful). `find_matches` /
  `find_matches_from_pointsets` keep their current signatures and
  return shapes. No DB / endpoint / config change.
- Tests: `tests/test_matching.py` — the three multi-entity parity tests
  stay; the wrong-shape-seed test flips from xfail to required-pass; a
  new test covers the fingerprint cache invalidation contract (cache
  bound to drawing dict identity, like the existing radius bucket).
- Behaviour: matches that the old code accepted at the loose-tolerance
  edge (scale ≠ 1, chamfer near `TOLERANCE_ABS`) will no longer match.
  Risk surface is real DXFs where shapes are NOT bit-identical copies.
  Design.md details the fingerprint precision choice and the audit plan.
