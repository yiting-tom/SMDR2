## Why

Two geometrically identical substrate outlines (same bbox 86×75, same vertex count, perimeter identical to 14 significant figures — i.e. an exact CAD copy translated elsewhere on the sheet) registered as a `reason="shape"` near-miss instead of a match. Reported chamfer was `0.2857 mm` against a `TOLERANCE_ABS = 0.2 mm` threshold, with `scale = 1.0` (size/aspect gates all passed).

Root cause: `_resample_arclength` lays its `RESAMPLE_N` samples starting from `points[0]` walking the stored vertex order. Two identical outlines stored with a **different first vertex or opposite winding** (CW vs CCW — exactly what a CAD copy / mirror / rotate-paste produces) resample to *misaligned sample grids*. The matcher's symmetric Chamfer assumes phase is absorbed downstream, but at the sharp corners of a substrate outline the misalignment leaves a residual that exceeds the 0.2 mm tolerance. Reproduced without the confidential file: a synthetic identical copy with reversed winding + a rolled start vertex scored Chamfer **1.78 mm** (≫ 0.2) under the current matcher; even brute-forcing all 64 resample phases × 4 orientations could not bring it down. Anchoring the resample start at a geometry-determined vertex dropped it to **0.0**.

This is a false near-miss — not a tolerance problem. Loosening `TOLERANCE_ABS` would paper over a sampling artifact *and* admit genuinely-near shapes; the correct fix removes the artifact.

## What Changes

- Add `_canonical_start(points)` → index of the vertex furthest from the centroid (always a corner for a substrate/component outline; a rotation- and winding-stable anchor), and `_resample_canonical(points, n)` → `_resample_arclength` after rolling the vertex list to start there.
- Use `_resample_canonical` in the production single-entity matcher `_match_single_serial` (both template-side and per-candidate resampling) and in `align_score`. The sampling phase becomes independent of stored vertex order and winding, so two congruent outlines resample to the *same* grid and score Chamfer ~0.
- Any residual corner-tie ambiguity (e.g. a near-square outline whose 4 corners are equidistant from the centroid, so the anchor vertex differs between the two copies) is absorbed by the existing 4 sign-variant orientations downstream — verified: a symmetric rectangle matches under every winding/start/mirror permutation.
- No change to `TOLERANCE_ABS`, `SCALE_MIN/MAX`, `RESAMPLE_N`, the signature pre-filters, `_match_multi` (pose-based, no chamfer), or `_match_signature_mode`.
- Add regression test `test_find_matches_identical_copy_with_reversed_winding_and_phase` (reversed-winding + rolled-start congruent copy must match; scores < 0.05).

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `pattern-matching`: ADDS a requirement that single-entity chamfer matching is invariant to stored vertex order and winding direction (canonical resample anchor). The existing "Transform-invariant matching" and "Single-entity template matching" requirements are unaffected (translation/rotation/mirror/scale invariance unchanged).

## Impact

- **Code**: `app/matching.py` — two small helpers + three call-site swaps (`_resample_arclength` → `_resample_canonical` in `_match_single_serial` ×2 and `align_score`).
- **Tests**: one new regression test in `tests/test_matching.py`. Full suite 549 passing (was 548). Matching suites (`test_matching`, `test_matching_circle_fast_path`, `test_circle_path_parity`, `test_dxf_auto_rescale`) 189 passing — zero existing-behaviour regression.
- **API**: no signature or return-shape change. Match `score` values for *currently-matching* candidates may drop slightly (better-aligned sampling) but stay below tolerance; match/near-miss outcomes are unchanged for every existing test.
- **Specs**: `pattern-matching` ADDS one requirement.
- **Data**: none. Stale prematch JSON regenerates on the next prematch / Scan All.
- **Performance**: one extra `argmax` + `np.roll` per resampled cloud — negligible vs the PCA + Chamfer already run. No change to the orientation/Chamfer inner loop.
