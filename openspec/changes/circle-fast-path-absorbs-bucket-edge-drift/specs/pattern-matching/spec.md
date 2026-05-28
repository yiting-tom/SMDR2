## ADDED Requirements

### Requirement: Single-CIRCLE radius bucket lookup absorbs banker's-rounding boundary drift

`_match_single_circle` SHALL look up the **three-neighbour window** of radius buckets `(key − 1, key, key + 1)` instead of the single bucket `key`, where `key = _radius_bucket_key(template.radius)`. The function SHALL concatenate the handle lists from these three buckets (in `(key − 1, key, key + 1)` order), apply the existing `skip` filter, and emit each surviving handle as a `MatchResult(handles=[h], score=0.0, scale=1.0)`. The no-`NearMiss` invariant from the underlying fast path is preserved.

The motivation is FP drift at `round()` fence-posts. `_radius_bucket_key(r) = round(r * 10**CIRCLE_RADIUS_KEY_DIGITS)` uses banker's rounding; when `r * 10**CIRCLE_RADIUS_KEY_DIGITS` lies on a `.5` half-integer, ULP-scale perturbations flip the bucket. The drawing side uses `EntityShape.from_circle`'s analytical radius (the DXF primitive's `r`); the library-stored template side uses `EntityShape.from_points`'s numerical radius (`max(|pts − centroid|)` over the synthesised point cloud). The two values agree to within 1 ULP for typical packaging coordinates but can fall on opposite sides of a fence-post, producing a 1-bucket shift that the lookup MUST absorb.

The widened window is constrained to `±1` bucket. The bucket grid is `10⁻⁴ mm = 0.1 µm`; at the bucket-midpoint worst case the window admits radii within `±1.5 × 10⁻⁴ mm = ±0.15 µm` of the template radius. Real packaging class radii differ by `≥ 1 µm` (`≥ 10` buckets), so the window cannot reach across any real design distinction. The lookup SHALL NOT widen to `±2` or beyond.

Each drawing handle lives in exactly one bucket (the bucket keyed by its own `_radius_bucket_key(s.radius)`); the concatenation of three adjacent buckets therefore cannot produce a duplicate handle. The function SHALL NOT perform set-based deduplication on the concatenated hit list.

This refines the lookup half of the "Single-CIRCLE template fast path via radius bucket" requirement introduced by `add-circle-scan-fast-path`. The bucket-construction side (`_get_radius_buckets`) and the key function (`_radius_bucket_key`) are unchanged — each drawing shape still occupies exactly one bucket keyed by its analytical radius.

#### Scenario: Template recomputed radius drifts to adjacent bucket

- **WHEN** a library-stored CIRCLE template's recomputed-via-`from_points` radius lies in bucket `key + 1` while every drawing CIRCLE of the same physical radius sits in bucket `key` (because the analytical r and the recomputed r straddle a `.5` banker's fence-post)
- **THEN** `_match_single_circle(template, drawing, skip=set())` returns every drawing CIRCLE in bucket `key` (the ±1 window covers `key − 1`, `key`, `key + 1`)
- **AND** the returned `MatchResult` entries carry `score == 0.0` and `scale == 1.0`
- **AND** `MatchOutput.near_misses == []`

#### Scenario: Drift the opposite direction is also absorbed

- **WHEN** the template's recomputed radius lies in bucket `key − 1` while same-radius drawing circles sit in bucket `key`
- **THEN** `_match_single_circle` still returns those drawing circles via the ±1 window

#### Scenario: Non-boundary radii produce identical results to single-bucket lookup

- **WHEN** the template radius is far from any banker's fence-post and `_radius_bucket_key(template.radius)` agrees bit-identically between the analytical and the recomputed paths
- **THEN** buckets `key − 1` and `key + 1` are empty for that template
- **AND** the returned matches are identical to the prior single-bucket implementation

#### Scenario: ±1 window does not reach across real design steps

- **WHEN** the drawing contains a CIRCLE template with radius `r₀`
- **AND** a candidate CIRCLE with radius `r₀ + 1 µm` (10 bucket steps away under `CIRCLE_RADIUS_KEY_DIGITS = 4`)
- **THEN** the candidate is NOT in any of `(key − 1, key, key + 1)` and SHALL NOT appear in matches

#### Scenario: No duplicate handles emitted

- **WHEN** every drawing handle is bucketed exactly once (per the unchanged `_get_radius_buckets` contract) and the ±1 window concatenates three adjacent buckets
- **THEN** the resulting hit list contains each handle at most once
- **AND** `_match_single_circle` SHALL NOT apply set-based deduplication; insertion order is preserved
