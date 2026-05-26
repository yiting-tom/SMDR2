## Why

Single-CIRCLE scan is the hot path on BGA / SMD packaging DXFs — a
typical board has 500–5000 ball entities, and dense packaging DXFs
often carry hundreds of thousands of additional circle-ish primitives
(copper pads, drill holes, vias). Live measurement on one such file
recorded **68 seconds** for a single frame-select scan: 381,806
matches + 18,961 near-misses returned through the generic
`_match_single` pipeline. The cost has two components:

1. **Per-candidate PCA + Chamfer is wasted work for circles.**
   Rotational symmetry makes PCA principal axes degenerate (equal
   eigenvalues, sign variants indistinguishable); any two equal-radius
   circles have ~0 Chamfer regardless of sampling phase.
2. **The current `[0.95, 1.05]` scale band over-collects.** Different
   ball sizes, pads, and vias all land in one bucket, then every
   downstream stage (MatchResult allocation, JSON serialisation, the
   frontend match overlay) eats the inflated set. Tightening the band
   helps, but a numpy-compare loop still emits one Python object per
   hit.

The user is a semiconductor back-end packaging engineer iterating live
on template selection ([[user_role]]); the BGA-ball case is the most
common interaction in the [[project_smdr2_template_flow]] flow, and
"select one ball → show me the same ball everywhere" is the expected
mental model — not "show me everything within 5%".

## What Changes

- `EntityShape` gains a `kind: str | None` field (e.g., `"circle"`,
  `"polyline"`, `"line"`) populated by `build_entity_shapes` from the
  source primitive's `type`. When a handle aggregates primitives of
  more than one type, `kind` is `None` (mixed).
- `Template` persists a per-entity `entity_kinds: list[str | None]`
  list recorded at commit time so `scan_all` retains the "this entity
  was a circle" information after the points lose primitive identity.
- `find_matches` and `find_matches_from_pointsets` detect a
  single-entity CIRCLE template (`kind == "circle"`, `radius > 0`)
  and dispatch to `_match_single_circle`, which is **bucket-based,
  not compare-based**:
  - Build a per-drawing `radius_buckets: dict[int, list[str]]` once,
    keyed by `round(r * 10 ** CIRCLE_RADIUS_KEY_DIGITS)` (default 10
    digits → 10⁻¹⁰ precision; integer key so bit-identical floats
    collide for free and tiny FP noise rounds away).
  - Match = `buckets.get(template_key, []) − skip`. Returns a list of
    handles, no per-candidate iteration.
  - **No NearMiss emitted.** Circle similarity is a single number;
    near-miss information adds nothing visually and costs an
    object-allocation per off-bucket entity.
- New tunable `CIRCLE_RADIUS_KEY_DIGITS = 10`, replacing the original
  proposal's `CIRCLE_R_RATIO`. The fast path is *exact-radius* under
  this precision, not a ±tolerance band.
- Per-drawing `radius_buckets` cached in-memory keyed by `id(drawing)`,
  so a rebuilt shapes dict (library swap, re-preprocess) invalidates
  the cache for free.
- **BREAKING (internal data):** `templates` SQLite table gains an
  `entity_kinds` JSON-text column. Existing rows migrate in-place
  with `NULL`; the matcher treats `NULL` as legacy and falls back to
  the generic path for those templates (no speed-up, no regression).

## Capabilities

### Modified Capabilities
- `pattern-matching`: add an exact-radius bucket fast path for
  single-CIRCLE templates that bypasses PCA/Chamfer and emits no
  near-misses.
- `template-library`: persist per-entity primitive kind alongside
  `entity_point_sets` so `scan_all` can dispatch the fast path.

## Impact

- **Backend (`app/matching.py`)**: `EntityShape.kind` field, new
  `_match_single_circle` (bucket lookup), `radius_buckets` cache,
  dispatch logic in `find_matches` / `find_matches_from_pointsets`.
- **Backend (`app/library.py`)**: `Template.entity_kinds` field,
  `templates.entity_kinds` schema migration, `collect_entity_kinds`
  helper, JSON round-trip in `add_template` / row reads.
- **Backend (`app/main.py`)**: commit handler captures kinds via
  `collect_entity_kinds`; `match` / `scan_all` / `save_match_json`
  thread `Template.entity_kinds` through to
  `find_matches_from_pointsets`.
- **No frontend changes** — both API endpoints stay byte-compatible.
  Frontend will observe far fewer match handles per scan; existing
  rendering and overlay logic handles that without modification.
- **Tests**: new bucket-path tests (exact-radius parity, mixed-radius
  exclusion, no-near-miss invariant), legacy-template fallback test.
