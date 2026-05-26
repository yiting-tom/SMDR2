## Context

`_match_multi` in `app/matching.py` is the only matching path used when
the template contains ≥ 2 entities. Today it runs:

```
for cand_handle in handles:                       # O(N) full scan
    if not signatures_compatible(seed, cand):     # ±20% size + σ-ratio gate
        continue
    cand_axes = _pca_axes(...)
    for sign in 4_variants:
        for t in others:
            tree.query_ball_point(predicted)
            for h in nearby:
                align_score(t.points, drawing[h].points)   # heavy: resample + 5 cKDTrees + 4 chamfer
```

Real packaging DXFs have ~10⁴ entity shapes and the inner
`align_score` call dominates. The user has confirmed the data contract
is much narrower than what this algorithm assumes:

- Every named object is a master pattern copy-pasted under translate /
  rotate / mirror. No scale variation. No shape drift. No vertex-count
  variation.
- Therefore "size + relative distance + relative angle" between
  composing entities is bit-identical across instances.

This change replaces the chamfer-similarity machinery with a rigid-
transform congruence matcher backed by a per-drawing fingerprint bucket
index. The existing `_get_radius_buckets` cache (single-CIRCLE fast
path) is the proof of concept for the bucket lifetime / invalidation
pattern.

## Goals / Non-Goals

**Goals:**
- Replace `_match_multi` with a fingerprint-bucket + rigid-transform
  matcher; ship a 10–100× scan-all speedup on real packaging DXFs.
- Close the wrong-shape-seed correctness gap as a side effect of the
  fingerprint gate (currently xfail in the test suite).
- Keep `find_matches` / `find_matches_from_pointsets` signatures and
  return shapes unchanged so call sites (`/api/match`,
  `/api/scan-all`, `/api/match-json`, `diagnose_swap`) need no change.

**Non-Goals:**
- Touching `_match_single_serial`, `_match_single_circle`, or
  `_match_signature_mode` — single-entity and CIRCLE fast paths already
  match the rigid model and aren't on the slow path here.
- Removing `align_score` / `signatures_compatible` from the module —
  they remain used by single-entity and by `diagnose_swap`.
- Supporting fuzzy multi-entity matches (slight scale or shape drift
  between instances). The new contract is bit-identical-modulo-rigid;
  fuzzy matches that the old code accepted at tolerance edges will no
  longer match. The proposal flags this as an intentional break.
- Parallelising candidate-seed iteration. Bucket sizes are small enough
  after this change that multiprocessing overhead would dominate.

## Decisions

### Decision 1: Fingerprint key = (round(path_length, 6), round(radius, 6), round(sigma_ratio, 6))

Three scalars from `EntityShape` that are all translation-, rotation-,
and mirror-invariant — together they discriminate any shape pair the
matcher cares about. Tuple of three int-ish floats hashes in O(1) and
fits a Python dict key directly.

**Why 6 decimal digits.** For mm-unit DXFs the meaningful coordinate
resolution is ~1e-4 mm (100 nm — well below any packaging tolerance);
6 digits gives us 1e-6 mm (1 nm) headroom that absorbs accumulated
floating-point noise from block-insert transforms while still
distinguishing real design steps. This is the same logic the existing
`CIRCLE_RADIUS_KEY_DIGITS = 4` constant uses for circle radii — we sit
one or two digits tighter because the multi-entity matcher composes
three scalars and we want the tuple to collide only when geometry is
genuinely identical.

**Alternative considered:** Use a single hash of the centred, canonical
point cloud (e.g. SHA of sorted edge-length tuple). Rejected — more
expensive to compute and offers no benefit when the three derived
scalars are already complete invariants for the rigid model.

**Alternative considered:** Match the circle path's digit count (4).
Rejected — the radius bucket already uses radius alone; here we
combine three signals and want collisions to be deliberate, not
incidental.

### Decision 2: Bucket cache lives on the drawing dict identity, like `_radius_bucket_cache`

Add a module-level
`_fingerprint_bucket_cache: dict[int, dict[FP, list[str]]]` keyed by
`id(drawing)`, matching the lifetime semantics of
`_radius_bucket_cache`. When `_shapes_for(file_id)` produces a fresh
drawing dict (library swap, re-preprocess) the new dict gets a fresh
`id()` and a fresh cache slot is allocated on demand.

**Why not attach to `EntityShape` or to a wrapper class.** Buckets are a
property of a drawing as a whole (a per-entity fingerprint plus the
peer entities it shares the bucket with). Storing on `EntityShape`
would either require back-references or recompute the inverse index
each call. The existing per-drawing-id cache is the established
pattern in this module — reuse it.

### Decision 3: Rigid transform recovery via PCA axis alignment, 4 sign variants

Keep the existing approach: compute the seed template's PCA axes once;
for each candidate seed, compute its PCA axes; map template → candidate
via `R = cand_axes.T @ seed_axes` (then 4 diagonal sign variants for
mirror/180°). Translation `t = cand.centroid - R @ seed.centroid`.

**Why not use a closed-form rigid alignment from corresponding
vertices.** The vertex ordering across copies isn't guaranteed to
correspond — different starting vertex, reversed winding, different
densification. PCA axes are derived from the centred cloud as a whole
and don't depend on vertex order.

**Mirror handling.** A right-handed cand→template basis would map under
a single `R`; mirror copies need a reflection. Enumerating 4 sign
variants of the orthonormal basis covers identity, mirror-x, mirror-y,
and 180° — same logic the old `_match_multi` already used. Cost is
trivial: 4 × small matrix multiplies.

### Decision 4: "Other" entity lookup uses centroid KDTree with tol=1e-6

Build a centroid KDTree from the drawing's centroids (already done in
the current `_match_multi`). For each `other` template entity,
`expected = R @ other.centroid + t`. Query `tree.query(expected, k=1)`
and accept only when the nearest centroid sits within `1e-6` of
`expected`. Then require `fingerprint(drawing[h]) == fingerprint(other)`.

**Why tol = 1e-6, not 1e-9 or `tolerance * 2` (current `pos_tol`).**
The transform composes a centroid subtraction + 2x2 matrix multiply +
centroid addition; for mm-coordinates of magnitude ≤ 10³ the
accumulated FP error is on the order of 1e-12 to 1e-10. A 1e-6 budget
covers four orders of magnitude of safety and still rejects entities
that are at the wrong predicted position by even sub-µm. The current
`pos_tol = max(0.1, tolerance * 2)` is a relic of the chamfer-tolerant
design and is no longer appropriate.

**Why `query(k=1)` not `query_ball_point(r)`.** Under the rigid model
the predicted position is exact; the nearest centroid is either THE
match or far away. `k=1` returns immediately and the distance check
acts as the gate.

### Decision 5: Verify fingerprint equality at the "other" lookup, not chamfer

Because fingerprints encode the same rigid invariants, two entities
with equal fingerprints are guaranteed to be congruent under rigid
transform. No chamfer needed.

**Risk:** "fingerprint equal" is necessary but not technically
sufficient — a degenerate cloud (e.g. two distinct shapes with
coincidentally equal `(path_length, radius, sigma_ratio)`) could
collide. In practice this is vanishingly rare for real geometry; the
three signals are independent enough that collisions don't show up in
the existing test suite. If a collision does surface from a real DXF
we can extend the fingerprint with a 4th scalar (e.g. a second moment
or a perimeter-to-area ratio) — purely additive change, no algorithmic
impact.

### Decision 6: Seed selection still uses bucket size as "rarity"

`min(template_shapes, key=lambda t: len(buckets.get(fingerprint(t), [])))`.
The rarest template entity by drawing-side multiplicity gives the
smallest candidate set, same as today's `candidate_count` but resolved
in O(M) bucket lookups instead of O(M·N) signature checks.

**Edge case:** if a template entity has a fingerprint not present in
the drawing at all, `len(...) == 0` and that entity becomes the seed
— the iteration immediately produces zero matches. Correct behaviour.

### Decision 7: Degenerate-PCA fallback — return empty match output, not raise

If a template entity has near-degenerate PCA (σ-ratio ≈ 1.0 — perfectly
isotropic / square cloud) the recovered rigid transform is ambiguous
beyond the 4 sign variants (any rotation aligns the cloud).
Out-of-scope to fix here; existing `_match_multi` has the same problem.
Document it and return an empty match output for those templates.

**Risk:** a square SMD pad template would hit this. Mitigation:
square-pad templates in real packaging are almost always part of a
multi-entity pattern where some OTHER entity is non-square, and that
other entity ends up being chosen as the seed (rarer + non-degenerate).
If the entire template is degenerate, no match — the user will see
this in scan-all results and can split the template differently.

## Risks / Trade-offs

- **Risk:** Real DXFs with hand-traced (not block-inserted) patterns
  have visually-identical instances whose fingerprints don't exactly
  match (vertex count differs → path_length wobbles in the 4th or 5th
  digit; σ-ratio shifts because PCA is sensitive to vertex sampling).
  These would silently drop from matches under the new code.
  → **Mitigation:** Before flipping any default, run the new matcher
  against the `data/uploads/` corpus and diff match counts vs the old
  matcher. If real-world regressions appear, widen the fingerprint
  precision (round to 5 or 4 digits) or fall back to the old chamfer
  path for buckets that miss. The `diagnose_swap` endpoint stays
  available for spot-debugging.

- **Risk:** Square pads (σ-ratio ≈ 1) produce ambiguous PCA → wrong
  pose hypothesis → silent missed matches.
  → **Mitigation:** Document this in `_match_multi`'s docstring; rely
  on seed selection picking a non-degenerate template entity when one
  exists. A future change can add 4-fold rotational enumeration for
  square templates if it shows up as a real problem.

- **Risk:** Fingerprint collisions across genuinely distinct shapes.
  → **Mitigation:** Three independent scalars at 1e-6 precision —
  collision space is sparse. If a collision surfaces, extend the
  fingerprint tuple; the bucket-cache structure tolerates this without
  algorithmic change.

- **Trade-off:** The 4-PCA-sign-variant loop is now the only mirror
  handling. If a DXF uses an exotic transform (e.g. a glide reflection)
  this won't help — but the data contract excludes that, so it's a
  non-issue.
