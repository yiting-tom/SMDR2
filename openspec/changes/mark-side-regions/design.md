## Context

SMDR2 today produces a single flat match-JSON keyed `<class>.<index>`
(see `app/main.py:691` `save_match_json`). Each value is a list of
match instances, each instance being a list of DXF entity handles.
The rule checker consumes those keys verbatim
([[project_smdr2_pipeline]]).

A packaging engineer reviewing a typical IC package DXF needs to
distinguish frontside and bottomside artwork — they share the sheet
but represent two different layers of the physical device. The match
output gives no hint of which half each instance came from. The user
asked to tag each match's key with a `frontside.` or `bottomside.`
prefix based on a region they paint on the viewer.

Existing viewer state machine already has analogous modes:
- **add-mode** (per-class template capture)
- **chain-mode** (toggle)
- **measure-mode** (toolbar + `D` hotkey, with Esc cascade — see
  `canvas.js:1985`)

The new mark-side-regions mode follows the measure-mode pattern: a
toolbar button + hotkey, mutually exclusive with the other modes,
participates in the Esc cascade, owns the canvas drag handlers while
active.

## Goals / Non-Goals

**Goals:**
- Let the user define exactly one axis-aligned world-space rectangle
  per side (frontside, bottomside) per file.
- Persist the rectangles on the file row so library swap /
  re-preprocess preserves them.
- Rewrite match JSON keys to include the side prefix based on each
  instance's bbox-center containment.
- Keep the rule checker untouched — it should see the new keys as if
  they had always existed.
- Reuse the existing box-drag visual feedback (the green/blue dashed
  rectangle used for window-select) so the user gets familiar
  affordances ([[feedback_autocad_ux]]).

**Non-Goals:**
- Polygonal or multi-rectangle side regions (single rect per side is v1;
  more shapes can be added later without breaking the JSON contract).
- Region-aware matching (the matcher still scans the whole drawing;
  side assignment is purely a labeling pass at serialization time).
- Auto-detection of frontside/bottomside from layer names or sheet
  dimensions — user explicitly paints both rects.
- Per-product or per-library side regions; v1 is strictly per-file.
- Surfacing the side prefix in the in-canvas overlay text or the
  scan-all overlay UI (those still show by-class colours; the prefix
  shows up in the match-JSON export and the rule report).

## Decisions

### Decision 1: Storage shape — two TEXT columns on `files`

Add two nullable `TEXT` columns to the `files` table:
`frontside_rect` and `bottomside_rect`. Each is either `NULL` or a
JSON string `{"x0":..,"y0":..,"x1":..,"y1":..}` in world coordinates
(normalized so `x0<=x1`, `y0<=y1`).

**Why not** a single `side_regions` JSON column? A flat per-side
column is simpler to filter, makes `UPDATE files SET
frontside_rect = NULL` trivial, and matches how `selected_layers`
already chose JSON-in-TEXT (`app/files.py:65`).

**Why not** a separate `file_side_regions` table? Two scalar regions
per file — a join is overkill.

Migration: ALTER TABLE on startup (same pattern as `selected_layers`,
see `app/files.py:158`).

### Decision 2: Containment test — bbox center

For each match instance (a list of entity handles), compute the
combined bounding box of those entities' point arrays, take the
center, and test it against the two rectangles.

- center ∈ frontside_rect → prefix `frontside.`
- center ∈ bottomside_rect → prefix `bottomside.`
- center ∈ both (rects overlap) → prefer `frontside.` deterministically
  with a warning logged once per request. The UI should prevent
  overlap (see Decision 4) so this is a defensive fallback.
- center ∈ neither (or both rects null) → no prefix.

**Why bbox center** over centroid-of-centroids or any-vertex-inside?
Center of the bbox is one cheap arithmetic per instance and matches
the user's mental model ("this pad is on the frontside half"). A pad
straddling the boundary is rare and the bbox center disambiguates it
consistently.

### Decision 3: Apply prefix in match-JSON serializer only

The matcher (`app/matching.py`) stays unchanged. Only one caller does
the rewrite:

- `save_match_json` (POST `/api/files/{id}/match-json`,
  `app/main.py:691`): rewrites keys before writing
  `data/match/{id}.json`. This is the artifact the rule-checker
  consumes downstream.

Explicitly **not** rewritten:
- `data/prematch/{file_id}.json` is shaped
  `{by_class: {<class>: [handle, ...]}}` — a flat per-class
  deduplicated handle list, not per-instance grouping. It powers the
  viewer's auto-shown scan-all overlay which colors by class. Side
  prefixing doesn't fit its schema and would break the overlay's
  class-color lookup.
- `POST /api/files/{id}/match` (`app/main.py:604`) returns
  `MatchResult`s directly to the viewer for the per-class overlay —
  same reasoning, no prefix.

Add one helper `side_prefix_for(handles, shapes, frontside_rect,
bottomside_rect) -> str | None` in `app/matching.py` (or a new
`app/side_regions.py`) that both callers share.

**Why** at serialization time and not in the matcher? The matcher is
the hot loop and is fed by templates that have no notion of
frontside. The label is a pure function of position + the file's
saved rectangles, applied after the matcher returns. Keeps
`matching.py` orthogonal.

### Decision 4: Invalidate cached match JSON on region change

`PATCH /api/files/{file_id}/side-regions` rewrites the two columns. If
either changes, the route SHALL also:
1. Delete `data/match/{file_id}.json` (if present) and set
   `match_saved = 0` — the user has to re-Save Match so the cache
   stays in sync with the new labels.
2. Re-emit `data/prematch/{file_id}.json` synchronously by replaying
   the preprocess prematch step (cheap — it just re-runs the
   already-cached library match against the already-cached shapes).

**Why not** silently rewrite on read? Because match JSON is consumed
by the product-level rule checker which reads files from disk; we
want a single source of truth on disk that matches whatever the user
last confirmed.

The prematch cache is NOT regenerated here — it carries no side
labels (see Decision 3) so it's still valid after a region edit.

### Decision 5: UI — two-step rectangle capture, modal-free

Mode entry (toolbar button "Sides" or hotkey `R` for "Region") →
status hint reads `MARK frontside · drag a rectangle`. User
left-presses, drags, releases → the rectangle is saved as
frontside_rect, the hint flips to `MARK bottomside · drag a
rectangle`. Second drag → bottomside_rect saved → exit mark-mode
automatically.

Persistent overlay renders both rectangles whenever they exist (even
outside mark-mode) as a thin tinted outline — frontside in
`#7ce7c2` (existing crossing-select green-tinted hue is fine, just at
20% opacity), bottomside in a contrasting tone like `#e7a07c`.

To redraw a single side: button has a dropdown / shift-click variant
→ `Redraw frontside only` / `Redraw bottomside only` / `Clear both`.
A simple right-click on the toolbar button can drop the menu.

**Why** two-step instead of a modal with x/y inputs? Engineers in
AutoCAD drag rectangles to do this; matching that muscle memory wins
([[feedback_autocad_ux]]). The modal-free flow also keeps the canvas
visible the entire time so the user can sight-check the boundary.

**Why** axis-aligned only? The two halves of a typical IC package are
axis-aligned to the sheet. Rotated rectangles would require an extra
angle picker and we'd still hit edge cases at the diagonal boundary.

### Decision 6: Mark mode integrates with Esc cascade

Esc cascade order (extending `canvas.js:1991`):
1. Cancel active box drag
2. Cancel active measurement
3. **Cancel in-progress side-region drag → exit mark-mode**
4. Clear scan-all overlay
5. Exit add-mode
6. Clear selection

This puts side-region cancellation right next to measure cancellation
since they're both transient capture modes.

### Decision 7: Mark mode disables / is disabled by other modes

While add-mode or measure-mode is active, the `R` hotkey is a no-op
(symmetric to how `D` is a no-op during add-mode, `canvas.js:2014`).
Pressing `R` exits chain-mode (toggle off) first.

## Risks / Trade-offs

[Regions drawn before the user has zoomed/panned can be inaccurate]
→ The overlay is persistent — the user can re-enter mark mode and
redraw any time. Add an `Edit sides` affordance under the toolbar
button.

[Match instance whose bbox center falls in neither rect gets no
prefix, which may surprise the user] → The proposal already states
"no prefix" for that case. Surface a per-file count in the match-JSON
response: `{ "frontside": N, "bottomside": M, "unassigned": K }` so
the user can spot a region that's drawn too small.

[Stale `data/match/{file_id}.json` after a region edit] → Mitigated
by Decision 4 (delete cache + clear `match_saved` on edit).

[Per-side-prefixed keys break a downstream rule that hardcoded
`smd.0`] → Existing rules iterate over keys with a class-name match
(see `app/rule_check.py`), and the suffix `class.index` is intact,
so existing patterns continue to work. New rules can opt in to the
prefix.

[Two rectangles overlapping mid-package] → UI should warn but not
block. Defensive fallback in Decision 2 picks frontside; engineering
expectation is overlap is a drawing error worth flagging.

## Migration Plan

1. Run schema migration on app startup (additive — two nullable
   columns; rollback is dropping the columns or ignoring them).
2. Existing files: both rects are `NULL` → no behavior change, match
   JSON keys stay `<class>.<index>`. No data backfill needed.
3. New feature is fully opt-in per file — the engineer only sees a
   difference once they paint regions and Save Match again.
4. Rollback: revert the serializer rewrite + drop the toolbar button
   + leave the columns in place (no-op).
