## Context

`_match_multi`'s rigid-transform recovery (current state, post-PR #2
landing at commit `284d68d`) works like this:

```python
seed = min(template_shapes, key=_bucket_size_with_neighbours)
seed_axes, _ = _pca_axes(seed.points - seed.centroid)
others_local = [(... (t.centroid - seed.centroid) @ seed_axes.T, ...)]
sign_variants = [(1, 1), (1, -1), (-1, 1), (-1, -1)]

for cand_handle in seed_bucket:
    cand_axes, _ = _pca_axes(cand.points - cand.centroid)
    for sx, sy in sign_variants:
        scaled_axes = cand_axes * np.array([[sx], [sy]])
        for t, local_pos, t_fp, t_key in others_local:
            expected = local_pos @ scaled_axes + cand.centroid
            ...
```

For each candidate the matcher tries 4 sign variants of the
candidate's own PCA axes, applies the rigid transform implied by
`(cand_axes_signed, cand.centroid)`, and verifies the predicted
position of every other template entity.

This assumes `_pca_axes(shape)` returns a *stable* orientation. For
an anisotropic shape (`σ₂/σ₁` clearly less than 1) it does —
eigendecomposition gives a unique principal axis direction modulo
sign, and the 4 sign variants exhaustively cover the sign ambiguity.

For an isotropic shape (`σ₂/σ₁ ≈ 1`), the eigendecomposition is
*degenerate*: any orthogonal pair of axes is a valid PCA. The
returned direction is whatever the eigen solver picks based on
µm-scale numerical noise. Two copy-pasted instances of the same
isotropic shape produce PCA axes pointing in random unrelated
directions. The 4 sign variants only cover 90°/mirror — not the full
360° rotation ambiguity — so the predicted other-entity centroids
miss by typically tens of mm, far beyond `CENTROID_NOISE_TOL = 1e-3 mm`.

User-confirmed concrete case: rounded-square SMD marks (σ₂/σ₁ ≈ 1.0
exactly — square is 2D-isotropic), 2-entity template, row of
copy-paste SMDs, irregular partial match.

## Goals / Non-Goals

**Goals:**
- Restore full recall for multi-entity templates whose seed is
  isotropic, especially the 2-entity case where there's no third
  anchor to mask the broken prediction.
- Preserve existing behaviour for anisotropic seeds (don't change a
  working path).
- Preserve all multi-entity matching invariants from the existing
  spec — same `MatchResult.scale = 1.0` contract, same cluster
  expansion, same near-miss accounting.
- Single-commit, narrowly-scoped change. Locks in regression test.

**Non-Goals:**
- Reworking `_pca_axes` itself or the seed-selection strategy
  (`_bucket_size_with_neighbours`). The seed pick stays the same.
- Changing single-entity matching, chamfer alignment, or signature
  pre-filter. Those paths are isotropy-robust by construction
  (single-entity chamfer is N×N distance; signature pre-filter
  doesn't use orientation).
- Adding a new endpoint, new UI surface, or new request shape.
- Tuning the isotropy threshold (`σ₂/σ₁ > 0.95`) past "obviously
  isotropic". Threshold is conservative — anything above 0.95 is
  effectively a circle / square for prediction purposes. Refine
  later only if a real case is mis-classified.

## Decisions

### Decision 1: Detect isotropy via `σ₂/σ₁ > 0.95`

`_sigma_ratio(shape)` already exists (`app/matching.py:181`) and
returns the dimensionless value in `[0, 1]`. Threshold 0.95 is the
operating definition of "this shape's PCA orientation is not
trustworthy". Above 0.95 = square / circle / cross / regular polygon
class. Below 0.95 = rectangle / line / L-shape / pad-with-cutout
class — those have a stable PCA axis.

**Rationale:** `_sigma_ratio` is cheap (already cached in the
`EntityShape`), the threshold is conservative enough that even
nearly-isotropic but technically anisotropic shapes (σ₂/σ₁ = 0.93)
still use the old reliable path.

**Alternative considered: always use 2-point fallback.** Rejected —
non-isotropic shapes get useful information from PCA orientation
(e.g. seed alone is enough to pin down rotation; 4 sign variants
exhaustively cover the rest). Removing that signal would re-introduce
candidate explosion the PCA path already controls.

### Decision 2: 2-point alignment for isotropic seeds

When `_sigma_ratio(seed) > 0.95`, abandon the PCA-axes-based prediction
and use a **2-point alignment**:

For each `(cand_seed, cand_other_first)` pair where:
- `cand_seed` ∈ seed's fingerprint bucket
- `cand_other_first` ∈ the **first other template entity's**
  fingerprint bucket
- `|cand_other_first.centroid - cand_seed.centroid|` ≈
  `|other_first.centroid - seed.centroid|` within
  `CENTROID_NOISE_TOL`

Recover the rigid transform from the line between the two centroids:

```
θ_template = atan2(other_first.centroid - seed.centroid)
θ_drawing  = atan2(cand_other_first.centroid - cand_seed.centroid)
rotation_angle = θ_drawing - θ_template
translation = cand_seed.centroid - R(rotation_angle) @ seed.centroid
```

Then verify each remaining other template entity (`others[1:]`) by
predicting its world centroid via this rigid transform and running
the existing KDTree + fingerprint check.

**Rationale:** two corresponding centroids fully determine the
in-plane rotation and translation (a 2D rigid motion has 3 DOF;
2 anchor centroids provide 4 constraints, with the redundancy used
to disambiguate the chosen pair from random noise via the distance
gate). No PCA orientation needed.

**Alternative considered: rotation-invariant local descriptor on
each entity.** Rejected — overkill for this defect. The 2-point
geometry is the simplest correct primitive.

### Decision 3: For 3+ entity isotropic templates, try 2 transforms

The 2-point alignment uniquely determines a single rotation. But for
3+ entity templates the matcher should still cover the mirror case
(template_shape vs mirror_shape relationship not captured by 2
points alone). So when `len(template_shapes) >= 3`, try both:
- the recovered rotation, and
- the **mirrored** rotation (reflect across the seed→other_first line)

For 2-entity templates, no mirror check needed — by definition the
pair has no handedness.

**Rationale:** 2 transforms vs the current 4 sign variants is
cheaper, not more expensive. The mirror coverage is preserved.

### Decision 4: Bucket-pair iteration is bounded

For typical SMD scans, the first other-entity's fingerprint bucket
contains at most O(N_SMDs) handles (often fewer once the cluster /
template_position skip-list trims). For each cand_seed, the inner
loop pays O(bucket_size_of_first_other) candidate pairs. After the
distance gate, only valid pairs survive — so the effective work per
cand_seed is O(N_valid_pairs), typically 1-2.

Total: O(|seed_bucket| × |first_other_bucket_filtered_by_distance|) =
O(N_SMDs × ~1). Same order as the current PCA path's
O(|seed_bucket| × 4 sign variants × N_other_entities). Recent
perf-guard test (`test_scan_all_perf_does_not_regress`) keeps this
honest in CI.

### Decision 5: Reuse `_cluster_key` skip and cluster-expansion logic

The new path emits raw matches in the same `[(handle, role_key)]`
shape as the PCA path, so the existing cluster-expansion
post-processing (`raw_matches` → `expanded` → `MatchResult` lists,
`matching.py:1175-`) applies unchanged. `template_cluster_keys`
skip (line 1117) and `seen_groups` dedupe (line 1159) port directly.

**Rationale:** post-processing handles all the multi-DXF /
stacked-polyline / shared-cluster correctness work. Reusing it
means the new path inherits every fix that landed on top of the
rigid-bucket matcher without re-deriving any of them.

## Risks / Trade-offs

- **Risk:** the threshold `σ₂/σ₁ > 0.95` mis-classifies a
  borderline-anisotropic shape as isotropic. → **Mitigation:** 0.95
  is conservative (real anisotropic shapes are usually < 0.7);
  shapes between 0.7 and 0.95 will still use the PCA path. If a
  real case reports a mis-classification, threshold can be tuned in
  a follow-up.
- **Risk:** the new path treats `others[0]` specially. If the first
  other entity happens to be unusually common in the drawing, the
  inner bucket iteration grows. → **Mitigation:** the distance gate
  filters aggressively in practice. If we observe a hot case, an
  enhancement is to pick `others[0]` as the rarest other entity
  (same `_bucket_size_with_neighbours` selection) — out of scope for
  this change.
- **Risk:** for templates whose seed AND first-other are both
  isotropic AND identical-shape (e.g. two squares), the bucket
  iteration enumerates many `(seed_cand, other_cand)` pairs where
  `seed_cand == other_cand` (same handle). → **Mitigation:** skip
  pairs where the two handles are identical inside the new loop;
  handled by the existing `matched_handle_set` semantics if we
  thread it through carefully.
- **Trade-off:** the post-fix path enumerates pairs based on
  *distance*, not on PCA-derived prediction. For very densely packed
  drawings with many same-shape entities, the candidate-pair count
  could grow. The perf-guard test sets the upper bound; if it ever
  trips, we'll know.

## Migration Plan

Single commit; no data migration. Rollback is the inverse commit.
Existing callers and consumers are unaffected — the change strictly
*increases* the set of accepted matches for isotropic templates
without changing any other behaviour.

## Open Questions

- Should the isotropy threshold be configurable via the dev-mode
  parameter override system? **Initial answer: no**, leave as a
  module constant (`ISOTROPIC_SIGMA_RATIO_THRESHOLD = 0.95`); add
  configurability later if a real case demands it.
- Should `others[0]` for the 2-point fallback be picked as the
  *rarest* other entity (not the first by source order)?
  **Initial answer: keep as `others[0]`** for the smallest diff;
  revisit if perf-guard test starts trending up.
