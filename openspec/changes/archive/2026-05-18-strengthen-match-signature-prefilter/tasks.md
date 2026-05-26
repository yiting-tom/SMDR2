## 1. EntityShape signature fields

- [x] 1.1 Add `pca_sigma1: float` and `pca_sigma2: float` fields to the `EntityShape` dataclass in `app/matching.py`.
- [x] 1.2 In `EntityShape.from_points`, compute σ values from the centered cloud (reuse the existing `eigh` pattern from `_pca_axes`); handle degenerate clouds (`< 2` rows or zero covariance) by setting `σ1 = σ2 = 0`.
- [x] 1.3 Add named module constants `RADIUS_RATIO = 0.20` and `SIGMA_RATIO_TOL = 0.15` next to `PATH_LENGTH_RATIO`.

## 2. signatures_compatible gates

- [x] 2.1 Add the radius-ratio gate: skip when either radius is 0; otherwise reject if `|r_a/r_b − 1|` exceeds `RADIUS_RATIO`.
- [x] 2.2 Add a helper that computes σ-ratio from an `EntityShape` (`σ2 / σ1` with fallback `0` when `σ1 == 0`).
- [x] 2.3 Add the σ-ratio gate: reject if `|ratio_a − ratio_b|` exceeds `SIGMA_RATIO_TOL`.
- [x] 2.4 Keep the gates after the existing degenerate-check and path-length check so cheap-first ordering is preserved.

## 3. Regression tests

- [x] 3.1 Add `test_signature_rejects_same_perimeter_rect_vs_square`: 2×1 rect template, 1.5×1.5 square candidate at distinct position — `signatures_compatible` returns `False`.
- [x] 3.2 Add `test_signature_rejects_thin_line_vs_thick_blob_same_perimeter`: long thin line template, near-square polyline of comparable path length — `signatures_compatible` returns `False`.
- [x] 3.3 Add `test_signature_tolerates_sigma_ratio_within_threshold`: two shapes whose σ-ratios differ by ≤0.15 still pass the σ gate (combined with other gates if applicable).
- [x] 3.4 Add `test_signature_invariant_under_rotation`: rotate the same shape by an arbitrary angle; assert `radius` and σ-ratio match within numerical noise and `signatures_compatible` returns `True`.
- [x] 3.5 Extend `test_find_matches_same_perimeter_different_shape_rejected` (already exists) to additionally assert the rejection happens via signature, not via chamfer — e.g., call `signatures_compatible` directly.

## 4. Existing-test verification

- [x] 4.1 Run the full `tests/test_matching.py` suite — all rotation, mirror, scale, density-invariance and multi-entity tests pass unchanged.
- [x] 4.2 Run `tests/test_matching_circle_fast_path.py` — circle fast path is unaffected (still bypasses signatures).
- [x] 4.3 Run the API-level matching tests if any exist that exercise `/api/match` end-to-end.

## 5. Spec sync

- [x] 5.1 Confirm the MODIFIED requirement in `openspec/changes/strengthen-match-signature-prefilter/specs/pattern-matching/spec.md` matches the implemented gates exactly (thresholds, ordering, degenerate handling).
- [x] 5.2 Run `openspec validate strengthen-match-signature-prefilter --strict` and resolve any issues.

## 6. Archive prep

- [x] 6.1 Once tests pass and code is reviewed, run `openspec archive strengthen-match-signature-prefilter` to merge the delta into `openspec/specs/pattern-matching/spec.md`.
