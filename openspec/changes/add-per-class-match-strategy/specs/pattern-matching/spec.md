## MODIFIED Requirements

### Requirement: Transform-invariant matching

The matcher SHALL find candidates that match a template under any
combination of translation, rotation (any angle), mirroring (any axis),
and isotropic scaling within the closed interval [0.95, 1.05]. The
match acceptance threshold under the default `chamfer` strategy SHALL
be a chamfer distance ε (`TOLERANCE_ABS = 0.05` in drawing units).

The matcher SHALL support a `strategy` kwarg on its public entry points
(`find_matches`, `find_matches_from_pointsets`), defaulting to
`"chamfer"`. When `strategy == "chamfer"`, the existing pipeline runs
unchanged: signature pre-filter (using global `PATH_LENGTH_RATIO = 0.20`
and `RADIUS_RATIO = 0.20`), scale window check, PCA-aligned chamfer ≤
`tolerance`.

When `strategy == "signature"` AND the template is a single entity, the
matcher SHALL:
1. Apply `signatures_compatible` with both `PATH_LENGTH_RATIO` and
   `RADIUS_RATIO` replaced by the caller-supplied `bbox_ratio`
   (callers SHALL pass `0.05` when the class has no override). The σ-ratio
   tolerance (`SIGMA_RATIO_TOL = 0.15`) is unchanged.
2. Skip the scale-window and chamfer stages.
3. Emit signature-compatible candidates as matches with `score = 0.0` and
   `scale` derived from the radius ratio
   (`candidate.radius / template.radius`).
4. Emit nothing — not even a near-miss — for signature-incompatible
   candidates under `signature` mode.

When `strategy == "signature"` AND the template is multi-entity, the
matcher SHALL fall back to the `chamfer` pipeline and emit one info-level
log line per call recording the bypass.

#### Scenario: Default chamfer behavior unchanged
- **WHEN** the matcher is called without a `strategy` kwarg
- **THEN** every existing match / near-miss outcome from before this change holds

#### Scenario: Signature mode matches identical-bbox candidate with mismatched vertex count
- **WHEN** a single-entity template (closed polyline approximating a 25 × 12 mm rectangle, 11 vertices) and a candidate (same closed polyline shape, 7 vertices, same bbox, same path length, same aspect ratio) are passed to the matcher
- **AND** the matcher is invoked with `strategy="signature"`, `bbox_ratio=0.05`
- **THEN** the candidate appears in `matches` with `score == 0.0`
- **AND** the same input under default `strategy="chamfer"` does NOT match (chamfer > `TOLERANCE_ABS`)

#### Scenario: Signature mode rejects wrong-sized candidate via tightened bbox_ratio
- **WHEN** the matcher is invoked with `strategy="signature"`, `bbox_ratio=0.05`
- **AND** a candidate's `max-radius-from-centroid` is 15 % larger than the template's
- **THEN** the candidate does NOT appear in `matches`
- **AND** the candidate does NOT appear in `near_misses` (signature mode emits match-or-nothing)

#### Scenario: Signature mode accepts rotation
- **WHEN** the matcher is invoked with `strategy="signature"`, `bbox_ratio=0.05`
- **AND** a candidate is the template rotated by an arbitrary angle (e.g., 30°, 45°, 137°)
- **THEN** the candidate appears in `matches`

#### Scenario: Signature mode accepts mirror
- **WHEN** the matcher is invoked with `strategy="signature"`, `bbox_ratio=0.05`
- **AND** a candidate is the template mirrored across the y-axis
- **THEN** the candidate appears in `matches`

#### Scenario: Multi-entity template under signature falls back to chamfer
- **WHEN** a 2-entity template is passed with `strategy="signature"`
- **THEN** the matcher runs the chamfer pipeline (single-entity short-circuit OR multi-entity pose-based path) ignoring the strategy override
- **AND** the behavior matches what the same call would produce under `strategy="chamfer"`

#### Scenario: Scan callsites resolve per-class strategy
- **WHEN** `scan_all`, `save_match_json`, the prematch worker, or the
  add-mode `match` endpoint (with a `class_name` in the request body)
  scans templates for a class with `match_strategy = "signature"` and
  `bbox_ratio = 0.05`
- **THEN** the matcher call for that class uses `strategy="signature"` and
  `bbox_ratio=0.05`
- **AND** other classes in the same scan continue using their own resolved
  strategies (default chamfer)
