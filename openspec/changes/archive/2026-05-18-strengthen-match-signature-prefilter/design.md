## Context

Single-entity scanning runs every drawing entity through `signatures_compatible` to drop obviously-different candidates before the expensive PCA + chamfer alignment. After the density-invariance change the vertex-count gate was removed and only one cheap filter remains: path-length ratio ±20%. With chamfer tolerance fixed at 0.05 mm (absolute), small entities and entities with similar perimeter but different shape (e.g., 2×1 rectangle vs 1.5×1.5 square, both perimeter 6) routinely pass alignment and produce false positives in real BGA / SMD drawings.

The matcher allows arbitrary rotation and mirror, so any new gate must be rotation- and mirror-invariant. Bbox-derived features (axis-aligned diagonal, area, aspect ratio) are explicitly rotation-variant and unsuitable — the same entity rotated 45° produces a different bbox.

## Goals / Non-Goals

**Goals:**
- Reject same-perimeter-different-shape candidates at the signature stage with O(1) checks, no chamfer.
- Keep every existing true positive: rotation, mirror, scale ∈ [0.95, 1.05], density-invariant resampling, multi-entity matching all unaffected.
- Avoid recomputing PCA per candidate inside the hot loop — share whatever the new signature computes with `_match_single_serial` / `align_score`.

**Non-Goals:**
- Touching the absolute chamfer tolerance `TOLERANCE_ABS` (still 0.05 mm). Relative tolerance is a separate concern.
- Re-introducing a vertex-count gate (density-invariant resampling has made that unsafe).
- Changing the multi-entity matcher's per-entity verification (`_match_multi` calls `align_score`; it picks up the new signature gates transparently via `signatures_compatible`).
- Touching the single-CIRCLE fast path (`_match_single_circle` bypasses signatures entirely by design).

## Decisions

### Decision 1: Gate features — `radius` ratio + PCA σ-ratio

Two rotation-invariant scalars derived from the entity's centered point cloud:

1. **`radius`** = max distance from centroid. Already on `EntityShape` (the "compact bound" field). Linear in scale, so under `[0.95, 1.05]` scale tolerance plus noise margin, gate is `|r_a/r_b − 1| < 0.20`.
2. **PCA σ-ratio** = `σ2 / σ1` where `σ1 ≥ σ2` are singular values of the centered cloud's covariance matrix. Already lives in `[0, 1]`: ≈0 for thin lines, ≈1 for near-isotropic shapes (squares, circles). Rotation-, mirror- and scale-invariant. Gate is absolute difference `|ratio_a − ratio_b| < 0.15`.

**Why both?** Radius alone misses the rect-vs-square case (`2×1` radius ≈1.118 vs `1.5×1.5` radius ≈1.061, ratio 0.95 — passes). σ-ratio catches it cleanly (`0.5` vs `1.0` — fails). σ-ratio alone misses two long thin entities of different size (both σ-ratios near 0); radius catches that.

**Alternatives considered:**
- Bbox area / axis-aligned diagonal / aspect ratio — rejected, rotation-variant.
- Oriented bbox (PCA-aligned) area — equivalent to σ-ratio + scale, no additional information at higher cost.
- Hu moments — overkill, expensive, ill-conditioned for thin shapes.
- Vertex count — already removed because it killed density-invariance.

### Decision 2: Cache σ-values on `EntityShape`

Add `pca_sigma1: float` and `pca_sigma2: float` to the dataclass, populated in `from_points`. The σ-axes (eigenvectors) are NOT cached on the dataclass — they're not needed by `signatures_compatible`, and the `_match_single_serial` template-side computation already runs PCA once per call. Storing the axes would bloat every `EntityShape` (one per drawing entity, possibly thousands per drawing) for no win.

**Alternative considered:** also cache the 2×2 axes array. Rejected — `_match_single_serial` runs template-side PCA once, then candidate-side PCA inside the loop. Caching candidate axes saves one `eigh` per candidate, but each `eigh` on a 2×2 covariance is ~µs; the chamfer dominates. Keeping `EntityShape` lean is more valuable than the micro-saving.

### Decision 3: Compute σ via the existing `_pca_axes` helper, refactored to also return σ

`_pca_axes` already computes `eigh(cov)` and the singular values (it returns `(axes, sv)` but the second value is currently unused at most call sites). `EntityShape.from_points` will call a small helper that extracts just the two σ values from the centered cloud. No new linear algebra primitives introduced.

### Decision 4: Gate thresholds

- Radius ratio gate: `0.20` (matches existing path-length tolerance, covers scale tolerance ±5% plus arclength-resample / PCA noise).
- σ-ratio absolute difference: `0.15`. σ-ratio is already in [0, 1] so an absolute gate is the natural unit. Chosen to be loose enough to absorb resampling noise (≈0.01 in tests) while still cleanly rejecting `0.5` vs `1.0`.

Both thresholds will live next to `PATH_LENGTH_RATIO` as named module constants so they're easy to tune.

### Decision 5: Empty-PCA handling

A degenerate cloud (single point, or all coincident vertices) has `σ1 = σ2 = 0`. Defining σ-ratio as `0` in that case is consistent with "thin line" and keeps the gate well-defined. The existing `_pca_axes` already falls back to `(I, zeros)` for sub-2-row inputs; the σ-ratio helper will follow that convention.

## Risks / Trade-offs

- **[Risk]** Tighter signature gates could drop a true positive that previously squeaked through due to resampling noise. → **Mitigation**: gate thresholds are loose (radius ±20%, σ-ratio ±0.15) relative to typical noise levels observed in `tests/test_matching.py` (≈0.01). All existing tests must continue to pass; add explicit regression for "high-noise true positive" if any existing test gets close to the gate edge.
- **[Risk]** PCA on extremely thin entities (2-vertex lines) is degenerate. → **Mitigation**: σ-ratio falls back to 0 for these; both candidates being lines pass the gate (both have ratio ≈0).
- **[Trade-off]** Computing σ at `from_points` time adds one `eigh` per `EntityShape`. → For a 1,000-entity drawing this is ~1 ms total, negligible vs the chamfer pass. Net win: every candidate that fails the new gates skips PCA + chamfer entirely.
- **[Risk]** The pattern-matching spec still lists "vertex count within ±25%" in a requirement that no longer reflects code. → **Mitigation**: MODIFIED Requirements delta will update the single-entity matching requirement so the spec stays in sync.

## Migration Plan

No data migration needed. The change is purely in-memory and additive. Rollout:

1. Land code + spec delta + tests in one PR.
2. CI runs the existing test suite plus the new regression cases.
3. After merge, re-running any cached `prematch` is unnecessary — `EntityShape` is rebuilt from primitives on demand, and the new fields are derived from the same points.

Rollback: revert the PR; cached `prematch` JSON keeps working because it stores handles, not `EntityShape` instances.
