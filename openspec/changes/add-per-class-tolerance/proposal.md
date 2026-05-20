## Why

The global `TOLERANCE_ABS = 0.05` mm chamfer tolerance is tuned for BGA-ball
class entities (~0.3 mm radius), but substrate-class entities are two
orders of magnitude larger (~20 mm). Real example: an "identical" substrate
authored as a 7-vertex closed polyline vs an 11-vertex one (same bbox, same
path length) chamfers at **0.46 mm** with scale ≈ 1.0 — a 9× the global
tolerance, so it gets parked in near-misses even though it's clearly the
same physical part. Per-class tolerance lets the substrate class run loose
(e.g., 0.5 mm) without disturbing BGA-ball class strictness.

## What Changes

- **Class schema gains a nullable `tolerance` (mm)**. NULL means "use the
  global default" (back-compat for every existing row).
- **New API**: `PUT /api/libraries/{library_id}/classes/{class_name}/tolerance`
  body `{tolerance: number | null}` to set; class-listing endpoint surfaces
  the field. `GET /api/libraries/{library_id}/classes` SHALL include
  `tolerance` for each class.
- **Matching plumbed through per-class tolerance**:
  - `scan_all` and `save_match_json` look up each class's tolerance before
    calling `find_matches_from_pointsets`.
  - The prematch worker does the same when generating the cached overlay.
  - `POST /api/files/{file_id}/match` (add-mode preview) accepts an
    optional `class_name`; if set, the endpoint uses that class's
    tolerance so the in-flight preview matches what scan-all will see
    after commit.
- **Dashboard UI**: each class row in the class list gets a small numeric
  input next to the class name. Blur saves (PUT). Empty / cleared field =
  NULL = default.
- **No change to `TOLERANCE_ABS` default** — BGA ball workflows stay
  identical.

## Capabilities

### New Capabilities

(none — feature extends an existing capability)

### Modified Capabilities

- `template-library`: classes gain an optional `tolerance` field; class
  listing and a new PUT endpoint expose it.
- `pattern-matching`: the matcher SHALL accept a per-class tolerance
  override threaded from the calling endpoint. Default behaviour
  (`tolerance == None`) continues to use `TOLERANCE_ABS = 0.05`.

## Impact

- `app/library.py`: schema add (`tolerance REAL NULL` on `classes`),
  migration column-add, getters/setters, `summary()` includes tolerance.
- `app/main.py`: new PUT endpoint; thread tolerance into `scan_all`,
  `save_match_json`, and the `match` endpoint when a class is named;
  prematch worker reads from class config too.
- `app/preprocess.py` (or wherever prematch runs): same class-tolerance
  lookup.
- `app/matching.py`: no signature change required — `find_matches` and
  `find_matches_from_pointsets` already accept a `tolerance` kwarg. The
  module's existing default constants stay.
- `app/static/dashboard.js`: numeric input per class, PUT on blur,
  re-render to show persisted value.
- `tests/`: new unit tests for the schema migration, PUT endpoint
  validation (tolerance must be positive float or null), and an
  integration test that confirms `scan_all` honors a class-level
  tolerance.
- DB migration: existing rows get NULL tolerance — behaviour identical to
  pre-change.
