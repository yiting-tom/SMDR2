## Context

Three observations shape the design:

1. **Where matches get a view prefix today**: `app/main.py` calls
   `split_matches_by_side()` (in `app/side_regions.py`) for every
   `(class, template)` pair after running `find_matches_from_pointsets`.
   The helper looks at each instance's bbox center and assigns one of
   `top_view` / `bottom_view` / `side_view`, or leaves it unprefixed
   (an "unassigned" match). The helper is currently class-agnostic.

2. **Two distinct read surfaces consume matches**: the data layer
   (`POST /api/files/{file_id}/match-json` → `data/match/{file_id}.json`
   → `rule_check.py`) and the viewer overlay (`/api/files/{file_id}/prematch`
   → `scanAllByHandle` in `canvas.js`). Both must apply the same filter
   or the engineer sees a "BGABall in top_view" instance in the viewer
   that has no corresponding entry in DRC results — a confusing
   divergence.

3. **The pre-match cache predates view rectangles**: pre-match runs at
   upload time, before the engineer has drawn any view rectangles. It
   produces a flat handle list (`scanAllByHandle`) keyed only by class.
   So we cannot filter at pre-match time without re-running pre-match
   on every view-rect change — wasteful and complex. Instead we filter
   at *read time*, where view rectangles are guaranteed to be current.

## Goals / Non-Goals

**Goals:**
- Make C4Ball physically impossible to appear outside `top_view`, and
  BGABall outside `{bottom_view, side_view}`, throughout the system
  surfaces the engineer interacts with (Scan All overlay) or that feed
  downstream consumers (match-JSON → DRC).
- Keep the constraint **data-driven** — adding a new constrained class
  in the future is one line in a Python dict, no new code.
- Save real compute in match-JSON when the file's view geometry makes
  a constrained class definitionally empty.

**Non-Goals:**
- Re-running pre-match every time the engineer edits view rectangles.
  Out of scope; pre-match stays view-agnostic.
- Hiding constrained classes from the toolbar / template library UI
  even when the file has no relevant view. The engineer can still
  *commit* a C4Ball template against any file — the file's matches
  just won't include it. (Toolbar-level hiding is a follow-up if it
  turns out to be needed.)
- Mutating already-saved `data/match/*.json` files retroactively. The
  next Save Match call rewrites them with the new filter; old files
  rot as usual when the file's match cache is invalidated.
- Changing the `rule_check.py` API. It reads whatever keys exist in
  match-JSON; once filtered, it reads filtered data.

## Decisions

### Where the constraint lives: `library.CLASS_VIEW_CONSTRAINTS`

Pattern follows `CLASS_JSON_KEY`: a module-level dict mapping display
ID to a `frozenset[str]` of allowed view names. Absent key → no
constraint (all four positions allowed: `top_view`, `bottom_view`,
`side_view`, unassigned).

```python
CLASS_VIEW_CONSTRAINTS: dict[str, frozenset[str]] = {
    "C4Ball":  frozenset({"top_view"}),
    "BGABall": frozenset({"bottom_view", "side_view"}),
}
```

**Why a frozenset of view names (not "top-only" / "bottom-side" sentinels):**
Future classes might be e.g. "top + side" (some fiducials), and a
sentinel scheme would explode in cases. Plain set membership is
trivial to reason about. The "unassigned" position is implicit: a
class is unconstrained iff its key is absent; once the key is present,
unassigned is **never** in the allowed set (strict mode).

**Alternative considered:** per-class DB column on `classes`. Rejected:
the constraint is a physics fact about the package class, not a
per-library configuration. It belongs in code, like `CLASS_JSON_KEY`.

### The filter primitive: `is_allowed_view(class_name, view)`

A pure helper in `app/library.py`:

```python
def is_allowed_view(class_name: str, view: str | None) -> bool:
    """view is one of 'top_view', 'bottom_view', 'side_view', or None
    (unassigned). Classes absent from CLASS_VIEW_CONSTRAINTS are always
    allowed; constrained classes admit only views in their allow-set
    (and never the unassigned None)."""
    allowed = CLASS_VIEW_CONSTRAINTS.get(class_name)
    if allowed is None:
        return True
    return view is not None and view in allowed
```

This is the single oracle used by both the match-JSON path and the
canvas Scan All path (the latter via a mirrored JS lookup table —
JS can't import Python, so the dict is duplicated as a JS constant
generated from the same source of truth). See **Risk** below.

### Match-JSON path: filter inside `split_matches_by_side`

`split_matches_by_side(base_key, matches, shapes, top, bottom, side)`
already classifies each instance to one of the four positions. The
minimal change is to add a `class_name: str` parameter and consult
`is_allowed_view(class_name, prefix)` before emitting:

```python
prefix = side_prefix_for(m.handles, shapes, top_view, bottom_view, side_view)
if not is_allowed_view(class_name, prefix):
    counts["dropped"] += 1
    continue
key = f"{prefix}.{base_key}" if prefix else base_key
out.setdefault(key, []).append(list(m.handles))
counts[prefix if prefix else "unassigned"] += 1
```

A new `"dropped"` counter joins the `counts` dict and surfaces in the
endpoint response as `side_counts["dropped"]`. Existing fields stay
unchanged.

### Match-JSON micro-optimisation: skip-when-impossible

Before invoking the matcher for a `(class, template)` pair, check:

```python
allowed = CLASS_VIEW_CONSTRAINTS.get(cls_name)
if allowed is not None and not any(
    rect_for(view) is not None for view in allowed
):
    continue  # every match would be dropped — skip the matcher entirely
```

Where `rect_for("top_view")` returns `rec.top_view_rect`, etc. This
saves a full `find_matches_from_pointsets` call per constrained
template on files where the relevant view rectangle is unset.

Importantly the skip is purely a perf optimisation — even without it,
the filter inside `split_matches_by_side` would produce the same
(empty) output. We're only skipping the *work*, not the *correctness*.

### Scan All overlay: mirror the filter in the renderer

The overlay reads pre-match JSON (`scanAllByHandle`: handle → class
name). When rendering, for each handle whose class is in the
constraints table, compute the handle's bbox-center position relative
to `sideRects.top_view` / `.bottom_view` / `.side_view` (already
client-side state), look up the class's allowed set, and skip drawing
if the position isn't in the allow-set or is unassigned.

The per-class count text in the status bar SHALL reflect post-filter
totals so the engineer sees the same number the DRC will see.

### Source-of-truth duplication: JS mirror of the constraint table

JavaScript can't import from Python, so `CLASS_VIEW_CONSTRAINTS` will
be duplicated as a literal at the top of `canvas.js`. To keep these
two copies from drifting:

1. **Add a unit test** in `tests/test_canvas_constants.py` that parses
   `canvas.js` for the JS literal (a simple regex over a stable
   `// CLASS_VIEW_CONSTRAINTS_BEGIN` / `_END` sentinel comment block)
   and asserts it matches the Python dict.
2. Use the sentinel comment as the contract: any reviewer can see the
   Python source and the JS duplicate side-by-side in the spec / PR
   description.

### Drop vs. reassign: drop wins

When a constrained-class instance is found in a disallowed view we
**drop** it rather than re-bucket as unassigned. Rationale:

- The match is physically impossible. Keeping it under any key
  invites downstream rules to count it (Rule 2 today counts every
  `bga_ball.*` key, including unprefixed).
- Reassigning to "unassigned" would silently move the noise rather
  than remove it — same problem in a different bucket.
- Engineers who see "0 BGABall in top_view" after running Save Match
  should infer "good, the matcher correctly didn't hallucinate BGA in
  the top view," not "wait, where did the noise go."

**Alternative considered:** emit a `dropped` map alongside `out` so the
diagnostic counters expose which class/view combos got filtered. We do
this in `counts["dropped"]` (an aggregate count, not per-class), which
is enough for the endpoint response without ballooning the API. Per-class
diagnostic logging can be a follow-up if engineers want it.

## Risks / Trade-offs

- **Risk**: Python/JS drift in `CLASS_VIEW_CONSTRAINTS`. → **Mitigation**:
  sentinel-comment unit test that parses both. CI catches drift on PR.
- **Risk**: A real-world DXF where the engineer's `top_view_rect` is
  slightly off and legitimate BGABall geometry's bbox-center falls
  inside `top_view_rect` by accident. Today those matches survive (as
  `top_view.bga_ball.*`); after this change they SHALL be silently
  dropped. → **Mitigation**: the new `side_counts["dropped"]` field
  surfaces the count in the API response so the engineer notices and
  fixes the rectangle. Tests cover this scenario.
- **Risk**: Engineers commit a C4Ball template against a file with no
  `top_view_rect`. The match-JSON will be empty for that class; the
  Save Match endpoint quietly skips the matcher (perf-optimised
  empty result). → **Mitigation**: the response's `side_counts`
  already shows 0 in every view for that class; we'll add a unit test
  asserting this is the user-facing signal.
- **Trade-off**: The constraint is hard-coded into application code,
  not configurable per library. Adding "fiducial-only-in-top-view"
  later is a one-line dict edit, but a customer asking for a custom
  rule would need a code change. Acceptable for now — every existing
  example is genuine packaging physics, not customer preference. If
  customer-specific constraints emerge, we promote the dict to a
  per-library DB column without breaking the existing keys (absent key
  = no constraint stays the same semantic).

## Migration Plan

- No database migration. Constraint applied at read/serialise time.
- Existing `data/match/{file_id}.json` files are not retroactively
  rewritten. They become stale on the next view-rect PATCH per the
  existing *Side-region edits invalidate saved match* requirement,
  and the next Save Match writes a filtered version.
- No feature flag — the constraint is physics, not a togglable
  behaviour. Roll forward only.
