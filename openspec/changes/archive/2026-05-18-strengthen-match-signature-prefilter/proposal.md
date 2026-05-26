## Why

Single-entity scans currently produce visible false positives on small or aspect-mismatched entities: the only cheap pre-filter is path-length ratio (±20%), so any entity with a similar perimeter reaches the chamfer stage, where the absolute 0.05-mm tolerance is permissive at small scales (e.g., a 1.5×1.5 square and a 2×1 rectangle both have perimeter 6 and pass `align_score` close to threshold). Adding two cheap rotation-invariant pre-filter gates will reject these candidates before they ever reach alignment, with no impact on true positives.

## What Changes

- `EntityShape` gains two cached signature fields: `pca_sigma1`, `pca_sigma2` (singular values of the centered point cloud, computed once in `from_points`).
- `signatures_compatible` gains two new gates on top of the existing path-length check:
  - **radius ratio** `|r_a/r_b − 1| < 0.20` (uses the `radius` field already on `EntityShape`).
  - **PCA σ-ratio** `|σ2/σ1 (a) − σ2/σ1 (b)| < 0.15` — rotation-invariant principal-axis aspect ratio.
- `_match_single_serial` and `align_score` are updated to reuse the cached σ-values / axes when available, so PCA is not recomputed per candidate.
- Regression tests:
  - Same-perimeter rect vs square is rejected at the signature stage (no chamfer).
  - Thin line vs thick blob with matching perimeter is rejected at signature stage.
  - All existing rotation / mirror / scale / density-invariance tests still pass.

This is purely additive — no breaking changes to API or stored data.

## Capabilities

### New Capabilities
<!-- None. -->

### Modified Capabilities
- `pattern-matching`: `signatures_compatible` requirement changes to add two rotation-invariant gates; `EntityShape` carries PCA singular values as derived signature fields.

## Impact

- **Code**: `app/matching.py` only — `EntityShape` dataclass, `signatures_compatible`, `_match_single_serial`, `align_score`, and internal PCA helper signatures.
- **Tests**: `tests/test_matching.py` — new regression cases; existing tests untouched in expected outcome.
- **APIs**: No HTTP/JSON contract changes.
- **Persistence**: No DB schema changes. Stored templates are unaffected — σ values are derived at runtime from the point sets.
- **Performance**: One extra `eigh` per `EntityShape` construction (negligible vs the existing chamfer cost); offset by skipping more candidates earlier and by reusing the cached σ-axes inside the matcher hot loop.
