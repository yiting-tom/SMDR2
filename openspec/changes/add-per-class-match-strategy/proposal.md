## Why

The matcher's chamfer pipeline is the right shape-discrimination tool for
small entities (BGA balls, SMD pads) where every vertex of a few-dozen-
vertex polyline carries genuine geometric information. It is the wrong
tool for substrate-class entities: their bbox and aspect ratio are the
discriminative features, while their internal vertex distribution is
noisy across CAD authoring (7-vert vs 11-vert renditions of the same
physical substrate chamfer at 0.46 mm with scale ≈ 1.0). Per-class
tolerance was tried and rejected ("還是不行，只會抓出大小不同的"):
loosening chamfer past 0.46 forces the global ±20 % path-length /
radius pre-filter to admit candidates of obviously wrong dimensions
before chamfer even runs. The right separation: substrate-style classes
get a different *match strategy* — pass the (tightened) signature gate,
declare match, skip chamfer — while BGA-style classes keep the existing
chamfer pipeline.

## What Changes

- **Class schema gains two coupled fields** (nullable, default ⇒
  pre-change behavior): `match_strategy ∈ {"chamfer", "signature"}` (default
  `"chamfer"`) and `bbox_ratio: float | null` (only honored when
  `strategy == "signature"`). NULL `bbox_ratio` ⇒ fall back to the global
  `PATH_LENGTH_RATIO = 0.20` / `RADIUS_RATIO = 0.20`.
- **New API**: `PUT /api/libraries/{library_id}/classes/{class_name}/strategy`
  body `{strategy, bbox_ratio?}` sets both atomically (they're coupled —
  `bbox_ratio` is meaningless under `chamfer`). Class summary surfaces
  both fields.
- **Matcher gains a `strategy` plus `bbox_ratio` kwarg pair** on the
  public entry points (`find_matches`, `find_matches_from_pointsets`):
  - `strategy == "chamfer"` (default): current pipeline, unchanged.
  - `strategy == "signature"`: skip chamfer. Use `signatures_compatible`
    with the per-class `bbox_ratio` substituted for both `PATH_LENGTH_RATIO`
    and `RADIUS_RATIO`. If signatures compatible → emit as match
    (score=0, scale derived from `radius_ratio`). If not compatible → no
    result for this candidate (signature mismatch is "different shape",
    not "almost matched" — `near_misses` stays empty under signature mode).
- **Scan-all / save-match-json / prematch worker / add-mode preview**:
  resolve per-class `(strategy, bbox_ratio)` from the library and pass to
  the matcher. Add-mode preview takes the same optional `class_name` body
  field as scan-all uses internally.
- **Viewer**: class buttons gain a small badge `chf` or `sig·5%`
  (only when overridden from default). Right-click opens a two-step
  prompt: strategy then (if signature) bbox_ratio.
- **No change to global `TOLERANCE_ABS`, `PATH_LENGTH_RATIO`, `RADIUS_RATIO`,
  `SIGMA_RATIO_TOL`, `SCALE_MIN/MAX`** — those stay the BGA-friendly
  defaults; only the per-class signature path overrides the dimensional
  ratios.

## Capabilities

### New Capabilities

(none — feature extends existing capabilities)

### Modified Capabilities

- `template-library`: classes carry `match_strategy` and `bbox_ratio` fields;
  new PUT endpoint sets them.
- `pattern-matching`: matcher accepts a strategy + bbox_ratio override and
  implements the alternative `signature` pipeline.

## Impact

- `app/library.py`: schema additions, migration column-adds, getters /
  setters, `summary()` includes both fields.
- `app/main.py`: new PUT endpoint; thread `(strategy, bbox_ratio)` into
  scan-all, save-match-json, and the `match` endpoint when class context
  is known.
- `app/jobs.py`: same threading in the prematch worker.
- `app/matching.py`: `find_matches` / `find_matches_from_pointsets` accept
  `strategy` and `bbox_ratio` kwargs; new internal `_match_signature_mode`
  routine.
- `app/static/canvas.js`: badge + right-click editor.
- `tests/`: schema migration, API validation, matcher behavior under both
  strategies (including rotation/flip invariance and per-class
  bbox_ratio tightening rejecting wrong-sized candidates).
- DB migration: existing libraries get `match_strategy = 'chamfer'` and
  `bbox_ratio = NULL` for every row — identical behavior to pre-change.
