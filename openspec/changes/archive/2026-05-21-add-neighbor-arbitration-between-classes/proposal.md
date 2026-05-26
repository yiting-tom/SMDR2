## Why

Two classes can have geometrically identical templates (e.g., `FiducialCircle`
and `BGABall` when their circle diameters coincide). Today, pure pattern
matching pulls the same handles into both classes — the Match JSON ends up
double-counting every fiducial as a BGA ball (and vice versa), which then
corrupts every downstream rule check. Geometry alone cannot break the tie,
but the **spatial context** can: BGA balls live in a dense grid, fiducial
marks are isolated outliers. We need a deterministic post-match arbitration
step that assigns each ambiguous handle to exactly one class based on its
neighbour count.

## What Changes

- Add a `library.CLASS_NEIGHBOR_RULES` registry — sibling to the existing
  `CLASS_VIEW_CONSTRAINTS` — that declares, per class, the neighbour-count
  bracket that membership requires (e.g. `BGABall` → `min_neighbors ≥ 2`,
  `FiducialCircle` → `max_neighbors ≤ 1`).
- Introduce a new post-match arbitration step that runs inside
  `save_match_json` after every class has been matched but before the JSON
  is written. It:
  1. Pools the centroids of all instances belonging to a configured
     **arbitration group** (a set of classes that have overlapping
     geometry and share a `CLASS_NEIGHBOR_RULES` entry).
  2. Auto-derives the local grid pitch from the **median** nearest-neighbour
     distance across the pool — no user configuration needed.
  3. Counts neighbours within `k × pitch` (`k` defaults to 1.5) for each
     instance and reassigns it to the class whose neighbour-count rule it
     satisfies.
  4. Falls back to a configured default class when one population is too
     small to be real (e.g., fewer than `min_population` BGA candidates →
     they were probably all fiducials).
- Mirror the new registry into `app/static/canvas.js` if any UI affordance
  needs it (the same drift-guard pattern used for `CLASS_VIEW_CONSTRAINTS`
  in `tests/test_canvas_constants.py`).
- Surface arbitration counts in the `save_match_json` response payload
  (`arbitration_counts: {reassigned: N, dropped: M, ...}`) so the UI and
  tests can verify behaviour.

## Capabilities

### New Capabilities

- `class-arbitration`: Post-match resolution of class membership when
  multiple classes have geometrically indistinguishable templates. Defines
  the arbitration-group concept, the auto-pitch / neighbour-count algorithm,
  population fallbacks, and how the arbitration result is reflected in
  Match JSON.

### Modified Capabilities

- `template-library`: Add the `CLASS_NEIGHBOR_RULES` registry alongside
  `CLASS_VIEW_CONSTRAINTS` (declaration site, drift guard, seeded defaults
  for `BGABall` / `FiducialCircle` — both already in `DEFAULT_CLASSES`).

## Impact

- **Code**:
  - `app/library.py` — new registry constant, seeded defaults, accessor.
  - `app/main.py:save_match_json` (line 979) — call the arbitration step
    after the per-class loop, before `json.dump`.
  - New module `app/class_arbitration.py` — pitch derivation, neighbour
    count, reassignment logic.
  - `app/static/canvas.js` — drift-mirror block if the UI consumes the
    registry (TBD in design).
- **Match JSON output**: handles that previously appeared under two class
  keys now appear under exactly one. Existing single-class match files are
  bit-identical because arbitration is a no-op when an arbitration group
  has zero overlap.
- **Rule check (`app/rule_check.py`)**: no schema change; the dedupe means
  per-class instance counts become accurate.
- **Tests**: new `tests/test_class_arbitration.py`; update Match JSON
  golden fixtures only where they exercised the bug.
- **Dependencies**: none — uses NumPy / SciPy already in the project.
