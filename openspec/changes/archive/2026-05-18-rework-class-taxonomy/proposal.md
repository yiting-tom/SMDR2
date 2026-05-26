## Why

The DEFAULT class taxonomy has accumulated drift: the seed list documented
in `template-library` spec still reads `smd, fiducial_mark, …` (lowercase,
9 entries), the implementation seeds 14 canonical CamelCase names
(`SMD-2T, FiducialMark, Side, …`), the match-JSON serializer writes the
CamelCase form into keys, and the rule-checker's scenarios already
reference snake_case prefixes (`substrate.*`). The names are also
out-of-step with the engineer's mental model — fiducials come in two
flavors (圓形 vs. 十字) that today collapse into one `FiducialMark`
class, and `Side` was never used in practice.

Reconciling all three is overdue: the engineer wants the toolbar in a
deliberate order, fiducials separated by shape, and the persisted match
JSON in snake_case so downstream tools (rule checker, exports, future
spreadsheet adapters) can address classes by stable, identifier-safe
keys without escaping `-` or case-folding.

## What Changes

- **Reorder** `DEFAULT_CLASSES` into the engineer's canonical sequence:
  Substrate, Pin-1, Lid, LidOuter, LidInner, DieArea, FiducialCircle,
  FiducialCross, SMD-2T, BGABall, 2DBarcode, SMD-3T, SMD-8T, SMD-14T.
  The trailing three SMD variants remain in the existing
  toolbar-collapsed fold group.
- **Split** `FiducialMark` into two siblings, `FiducialCircle` and
  `FiducialCross`. The split is purely additive at the class level
  (existing FiducialMark templates carry no shape metadata that could
  auto-classify them).
- **Remove** the `Side` class (never used).
- **BREAKING (match JSON):** the class portion of every match-JSON key
  switches from CamelCase to snake_case via a new
  `CLASS_JSON_KEY` map (e.g. `top_view.BGABall.0` →
  `top_view.bga_ball.0`, `Substrate.0` → `substrate.0`). The
  display name in the viewer toolbar stays CamelCase — only the
  persisted/exported key changes.
- **Migration:** every existing library DB is brought to the new state
  on next Store boot — deprecated classes (`FiducialMark`, `Side`) and
  every template filed under them are dropped, missing defaults
  (`FiducialCircle`, `FiducialCross`) are seeded, and class rows are
  re-ranked in the new order. Pre-existing `data/match/*.json` files
  are invalidated and regenerated (the `match_saved` flag is reset).

## Capabilities

### New Capabilities
(none — every change extends existing capabilities)

### Modified Capabilities
- `template-library`: default class seeding list, order, and
  contents change; a new display-name → match-JSON-key mapping is
  added; the migration step gains a deprecation/re-rank pass.
- `dxf-pipeline`: the Match JSON export's `<class>` token is now
  defined as the snake_case form from `CLASS_JSON_KEY` (display
  identifier `BGABall` → key `bga_ball`, etc.), reconciling the
  spec's already-snake_case examples with the implementation.

## Impact

- **Backend (`app/library.py`)**: `DEFAULT_CLASSES` reordered;
  `DEPRECATED_CLASSES` and `CLASS_JSON_KEY` introduced; `_migrate()`
  gains drop-deprecated, seed-missing-defaults, and re-rank passes.
- **Backend (`app/main.py`)**: `save_match_json` uses
  `CLASS_JSON_KEY[cls_name]` for the persisted key.
- **Backend (`app/rule_check.py`)**: prefix arguments switch to the
  snake_case forms (`substrate`, `smd_2t`, `bga_ball`).
- **Frontend (`app/static/canvas.js`)**: `CLASS_COLORS` replaces
  `FiducialMark` with `FiducialCircle` + `FiducialCross`, drops
  `Side`. Existing `COLLAPSED_TOOLBAR_CLASSES = {SMD-3T, SMD-8T,
  SMD-14T}` already matches the new fold group.
- **Tests**: `tests/test_rule_check.py` mock match-JSON keys updated
  to snake_case.
- **Data files**: `data/match/*.json` deleted; `files.match_saved`
  flag reset for affected rows.
- **No API surface change** — endpoints and HTTP shapes are
  unchanged; only the key strings inside the JSON body change.
