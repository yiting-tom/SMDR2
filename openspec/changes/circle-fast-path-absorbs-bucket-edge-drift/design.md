## Context

The circle fast path was introduced by `9d66024` to avoid running
PCA + Chamfer alignment on every BGA ball in a circle-heavy drawing.
The trick: bucket all `kind == "circle"` drawing shapes by an
integer key derived from their radius, then a single-CIRCLE template
match becomes a constant-time dict lookup.

The bucketing logic (`app/matching.py`):

```python
CIRCLE_RADIUS_KEY_DIGITS = 4

def _radius_bucket_key(r: float) -> int:
    return round(r * (10 ** CIRCLE_RADIUS_KEY_DIGITS))   # banker's rounding

def _get_radius_buckets(drawing):
    buckets = {}
    for h, s in drawing.items():
        if s.kind == "circle" and s.radius > 0:
            buckets.setdefault(_radius_bucket_key(s.radius), []).append(h)
    return buckets

def _match_single_circle(template, drawing, skip):
    key = _radius_bucket_key(template.radius)
    hits = _get_radius_buckets(drawing).get(key, [])
    return MatchOutput(matches=[
        MatchResult(handles=[h], score=0.0, scale=1.0)
        for h in hits if h not in skip
    ], near_misses=[])
```

The drawing side uses `from_circle`'s analytical radius (`r` straight
from the DXF parser). The template side (when loaded from the
library through `find_matches_from_pointsets`) uses `from_points`'s
numerical radius: `max(|pts - centroid|)` over 24–64 synthesised
points around the circle. The two radii are equal to within
ULP-scale precision (the synthesis preserves r exactly; the
recomputation reads it back with a few ULPs of FP drift).

`_radius_bucket_key` uses Python's `round()` (banker's rounding,
round-half-to-even). For most r values this is fine — ULP-scale
drift never crosses an integer boundary. But when `r * 10^4` lands
near a half-integer (`.5` fence-post), `round()` is **unstable
under ULP-scale perturbation**:

| Input             | `round(...)` |
|-------------------|--------------|
| `412.5`           | 412          |
| `412.5 + 1e-13`   | 413          |
| `412.5 - 1e-13`   | 412          |

This is the bug. On the user's affected DXF the LS-fitted circle r
for the BGA grid lands such that the analytical r (from the parsed
primitive) and the recomputed r (from stored points after
synthesis + round-trip through JSON + `from_points`) sit on
opposite sides of a fence-post. Drawing shapes land in bucket
`key=N`. Template lookup asks for `key=N+1`. Result: zero matches.

Other DXFs the user has don't hit this — their LS-fitted radii
don't land near a fence-post. The bug is data-dependent but the
fence-post population is dense enough (every `10^-4 mm` of radius
contains one) that any real packaging design with a less-round
radius will eventually hit it.

## Goals / Non-Goals

**Goals:**

- `_match_single_circle` returns the same matches it would have
  returned BEFORE `9d66024` made the drawing-side analytical, while
  preserving the post-`9d66024` performance gain.
- No false positives: the widened lookup MUST NOT pull in circles
  that real packaging classes would consider distinct.
- The fix is local to `_match_single_circle`'s body. No surrounding
  function (`build_entity_shapes`, `from_circle`, `from_points`,
  `_radius_bucket_key`, `_get_radius_buckets`,
  `find_matches`, `find_matches_from_pointsets`) is touched.

**Non-Goals:**

- Don't change `_radius_bucket_key`'s rounding mode. Banker's
  round-half-to-even is consistent with everything else in the
  matcher and the fence-post instability is patched at the lookup
  side anyway.
- Don't change `_get_radius_buckets`'s storage. Each handle still
  lives in exactly one bucket — only the lookup widens.
- Don't widen the bucket grid (raising `CIRCLE_RADIUS_KEY_DIGITS`
  would tighten precision, not loosen it; the spec's stated 1-µm
  design-step distinction stays comfortably in range).
- Don't add a post-lookup radius tolerance check. The bucket grid
  IS the tolerance and ±1 patches the rounding boundary; layering a
  numeric tolerance on top would be belt-and-suspenders that
  obscures what's actually happening.
- Don't touch the multi-entity fingerprint bucket
  (`_get_fingerprint_buckets`) — it already does ±1 neighbour
  lookup under `350088b`. This change brings the single-circle path
  in line with that established pattern.

## Decisions

### 1. ±1 neighbour bucket lookup

```python
def _match_single_circle(template, drawing, skip):
    key = _radius_bucket_key(template.radius)
    buckets = _get_radius_buckets(drawing)
    hits: list[str] = []
    for k in (key - 1, key, key + 1):
        hits.extend(buckets.get(k, []))
    return MatchOutput(matches=[
        MatchResult(handles=[h], score=0.0, scale=1.0)
        for h in hits if h not in skip
    ], near_misses=[])
```

Three sequential dict `.get` calls + a single `list.extend` per
non-empty bucket. Total cost ≈ 3 hash lookups + one allocation.

**Alternative considered:** add a numeric radius tolerance after
the bucket lookup:

```python
for k in (key - 1, key, key + 1):
    for h in buckets.get(k, []):
        if abs(drawing[h].radius - template.radius) <= 1.5e-4:
            matches.append(...)
```

Rejected — the bucket grid already encodes the tolerance and a
secondary `abs(...) <= ε` check muddles the contract. The widened
bucket window MUST be the canonical tolerance.

**Alternative considered:** double the bucket grid by changing
`CIRCLE_RADIUS_KEY_DIGITS` from `4` to `3`. Rejected — coarsening
the grid increases the chance two real design steps share a
bucket (10 µm precision instead of 0.1 µm), and the spec's
"FP-noise radii within bucket precision still match" scenario
implies the current grid is part of the contract. The fence-post
problem is a rounding-boundary issue, not a grid-resolution issue;
the ±1 lookup is the targeted fix.

**Alternative considered:** unify `from_points` and `from_circle` to
produce bit-identical radii (e.g., make `from_points` detect circle
point clouds and recover the analytical r). Rejected — that's a
much bigger surface to get right, requires a "is this point cloud a
circle?" heuristic, and would regress the original `9d66024`
performance argument (the whole point of `from_circle` was to skip
the per-point centroid + max-norm scan).

### 2. No dedup needed

Each drawing handle lives in **exactly one** bucket (the bucket
keyed by its own `_radius_bucket_key(s.radius)`). Concatenating the
three ±1 buckets cannot produce a duplicate handle, so
`hits.extend(...)` does the right thing without a `set` round-trip.
This keeps the ordering deterministic (preserves bucket-insertion
order) and avoids the per-call cost of building a set.

### 3. Safety: ±1 window vs real design distinctions

Bucket grid is `10^-4 mm = 0.1 µm`. The ±1 window admits radii
within `±1.5 × 10^-4 mm = ±0.15 µm` of the template radius (at
worst, when the template radius sits exactly on a bucket midpoint
and a candidate sits in the adjacent bucket's far edge).

Real packaging class radius ranges (rough working numbers):

| class            | radius range                |
|------------------|-----------------------------|
| `C4Ball`         | 20 – 100 µm                 |
| `BGABall`        | 150 – 500 µm                |
| `FiducialCircle` | 100 – 500 µm                |
| `2DBarcode`      | dots ≈ 50 – 200 µm          |

The smallest meaningful design step within a real layout is on the
order of 1 µm — driven by the photolithography registration budget,
not by the matcher. `0.15 µm` is **6× tighter** than the smallest
real design step, so the ±1 window cannot accidentally accept a
circle from a different design intent.

The only case where ±1 changes results: when the SAME design intent
sits on opposite sides of a banker's fence-post due to FP drift.
That's exactly the bug being fixed.

### 4. No behavioural change for non-boundary radii

If `template.radius` doesn't land near a fence-post, the recomputed
radius and the analytical radius hash to the same bucket key
(post-`round()`, the half-integer is the only unstable point).
`buckets.get(key - 1, [])` and `buckets.get(key + 1, [])` return
empty lists; the result is identical to today's `buckets.get(key,
[])`. The fix is **silent on the common path** — only at the
fence-post does the new behaviour activate.

This means every existing test in
`tests/test_matching_circle_fast_path.py` (radii `1.0`, `1.001`,
`1.05`, `2.0`, `1.0 + 1e-12`, …) continues to pass unmodified —
none of those radii sit on a fence-post.

## Risks / Trade-offs

- [Performance: 3× hash lookups instead of 1] → Mitigated:
  per-template constant cost, dominated by the matching pipeline's
  per-candidate work. No measurable impact in profiling-relevant
  workloads (BGA scans run thousands of templates; the extra cost
  is sub-microsecond per template).

- [A future code change reuses `_match_single_circle` for a
  context where ±1 over-matches] → Mitigated: any such context is
  out of scope of this change, and the current callers
  (`find_matches`, `find_matches_from_pointsets`) both rely on the
  fast path being a "find all radius-equivalent circles" primitive.
  The ±1 tolerance is consistent with that mental model.

- [`_radius_bucket_key`'s banker's rounding could be replaced later
  with a different rounding mode, making the ±1 patch redundant] →
  Accepted: the ±1 lookup is robust to any rounding mode (it absorbs
  drift up to half a bucket), so a future rounding change would not
  break correctness. The two changes are independently valid.

- [Drift larger than 1 bucket (≥ 2 ULP) escapes the ±1 window] →
  Not a realistic concern: the synthesised-points round-trip
  empirically holds radii to within 1 ULP of the analytical r for
  typical packaging coordinates (verified at file-development time
  for `9d66024` to "≤ 1e-13" per its commit message). Even for
  pathologically large world coords (the affected file's
  `36000 mm`-scale geometry), the relative precision is `~10^-11`
  of the radius — at `r = 0.4 mm` that's `~4e-15 mm`, eight orders
  of magnitude below a single bucket. The fence-post-crossing case
  is **exactly** a 1-bucket shift, never larger.

## Migration Plan

None — frontend-unaffected backend change, no data on disk and no
API moves. The first deploy that ships the new `app/matching.py`
applies on the next scan-all / save-match invocation; cached
artifacts (`parsed/*.json`, `match.json`, `prematch.json`,
`rule_check.json`) are independent.
