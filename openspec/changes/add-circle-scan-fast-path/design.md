## Decision: Exact-radius bucket lookup, not a numpy compare band

The earliest version of this proposal used a `±CIRCLE_R_RATIO`
vectorised numpy compare. Live measurement on a packaging DXF (68 s,
381,806 matches) showed two problems with the compare model:

1. **Compute is not the bottleneck once PCA/Chamfer is gone.** A
   numpy compare over a 400k-radius array is microseconds. The
   bottleneck shifts entirely to downstream cost — allocating a
   Python `MatchResult` per hit, JSON-serialising the result, and
   ingesting it on the frontend.
2. **A tolerance band over-collects.** The packaging-engineer mental
   model is "find me the *same* ball, not anything within 5%". A
   band-based fast path still folds together different pad/via/ball
   sizes that happen to fall within a few percent of each other.

The new path is a **per-drawing radius bucket**:

```
radius_buckets: dict[int, list[handle]]
key(r) = round(r * 10 ** CIRCLE_RADIUS_KEY_DIGITS)   # integer, default 10 digits
```

Match becomes a single `dict.get(key, [])`. The cost of "look up all
hits" is O(1) in the candidate count.

The integer key collapses two distinct sources of equality:

- **Bit-identical DXF CIRCLE radii.** A block-inserted BGA ball
  carries the same `radius` field across all instances; the float is
  bit-identical, hashes identically, and lands in the same bucket
  without rounding tricks.
- **Polyline-detected circles with FP noise.** When a closed curve is
  recognised as a circle via `_detect_circle_subpath`, its radius is
  `rsum / n` — vulnerable to last-bit FP noise. Rounding to 10⁻¹⁰
  precision absorbs that noise while still distinguishing radii that
  differ by a single µm at the µm scale.

**Real-data calibration:** an initial 10⁻¹⁰ default was tuned against
typical small-radius features (0.05 – 50 mm). On a real BGA packaging
DXF with 400,768 logically-identical CIRCLE entities at r ≈ 189.957671
mm, CAD transforms accumulated ~10⁻¹¹-mm noise across instances. At
10⁻¹⁰ precision that noise straddled the bucket boundary and split the
"same ball" set into two buckets (365k + 35k). Loosening to 10⁻⁶
(`CIRCLE_RADIUS_KEY_DIGITS = 6`, i.e. 1 nm precision in mm) collapses
those instances back to one bucket without compromising real design
distinguishability — 1 nm is six orders of magnitude below the finest
meaningful BGA / SMD tolerance.

## Decision: No NearMiss for the circle fast path

The generic path emits NearMiss objects for two reasons: (a) help
the user understand "why didn't this match?" and (b) feed downstream
debugging. For a circle, similarity is a single number — the user
can read it off the readout, and a near-miss radius differs from the
template radius by a number visible at a glance in the canvas.
Emitting 18,961 NearMiss objects in the measured run paid an
object-allocation per off-bucket entity for information that is
neither displayed nor consumed.

The fast path therefore returns `MatchOutput(matches=[...],
near_misses=[])`. The generic single-entity path still emits
near-misses with its existing semantics. The `MatchOutput` shape is
unchanged; downstream consumers handle an empty list correctly today.

## Decision: Per-drawing bucket cache, keyed by drawing identity

The bucket dict is the cache. It is built lazily on first
single-CIRCLE scan against a given drawing and stashed in a
module-level `_radius_bucket_cache: dict[int, dict[int, list[str]]]`
keyed by `id(drawing)`. Any code path that rebuilds the drawing's
shapes dict (library swap, re-preprocess) produces a new object →
new `id()` → new cache slot, and the old entry is garbage-collected
when the underlying dict goes out of scope.

A weak-reference index would be safer in principle but the
`drawing_shapes` dict is already kept alive for the lifetime of a
file's `_shapes_for(file_id)` cache, and the bucket cache will be
dwarfed by that. Plain `id(drawing)` keying is sufficient.

## Decision: `kind` recorded on EntityShape, not detected per-call

The alternative is re-detecting "is this a circle" from the points
via a radial-uniformity check at match time. That avoids the schema
migration but:

- Pays a heuristic check per candidate at scan time; the dispatch is
  supposed to be cheap.
- `_detect_circle_subpath` lives in `app/dxf.py` and is gated on
  ezdxf-specific `sub.is_closed` + `sub.has_curves`. Reproducing that
  predicate from raw points alone is lossy and would diverge from the
  upstream definition over time.
- Records the truth at the most authoritative point in the pipeline
  (entity construction) and lets every downstream consumer trust it.

We accept the `templates.entity_kinds` migration, with `NULL` →
legacy fallback so that pre-migration libraries still scan correctly
via the generic path.

## Open question: mixed-entity handles

An `EntityShape` can aggregate multiple primitives sharing one DXF
handle (rare in packaging DXFs but legal — e.g., a closed polyline +
a nested arc on the same `handle`). Today `collect_entity_points`
concatenates points across primitive types. We set `kind = None` when
types disagree, which degrades the entity to the generic path —
acceptable because mixed-kind handles are uncommon and the user still
gets a correct match; they just don't get the fast-path speed-up.

## Non-goals

- Other entity kinds (LINE, POINT, polylines) keep using the generic
  `_match_single` path. The spec leaves the dispatch open for future
  kind-specific fast paths but doesn't add them now.
- The multi-entity matcher (`_match_multi`) is unchanged. A
  multi-entity template containing circles still goes through
  pose-based matching; specialising it on circles is a separate,
  harder problem (the seed-rarity heuristic needs to know about
  per-kind candidate counts) and deferred.
- No ±tolerance band on the fast path. If users find legitimate
  same-ball groups split across two buckets due to upstream
  inconsistency (e.g., some balls stored as CIRCLE + others
  detected from polylines with slightly different rmean), the fix
  belongs in the upstream `_detect_circle_subpath` pass, not in the
  matcher.
