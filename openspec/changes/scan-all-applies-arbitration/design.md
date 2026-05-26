## Context

`save_match_json` (`app/main.py:1062-1145`) and `scan_all`
(`app/main.py:1020-1040`) both walk `lib.classes`, run each class's
templates through `find_matches_from_pointsets`, and accumulate
hits. They differ in three places:

| Step | `save_match_json` | `scan_all` |
|---|---|---|
| View split | `split_matches_by_side(...)` per match → keys like `top_view.bga_ball.0` | None — handles dropped into `by_class[class_display_name]` set |
| Arbitration | `arbitrate(out, shapes, CLASS_ARBITRATION_GROUPS)` | None |
| Output | Prefixed-key `dict[key, list[list[handle]]]` persisted to disk + response | Flat `dict[class_display_name, list[handle]]` returned to overlay |

The result: a BGA ball that the FiducialCircle template hit ends up
in `scan_all.by_class["FiducialCircle"]` (cross-fire), but
`save_match_json` correctly reassigns it to BGABall via arbitration.
User-visible symptom: the scan-all overlay highlights grid balls
with the FiducialCircle colour even though the persisted Match JSON
has them as BGABall.

## Goals / Non-Goals

**Goals:**
- Apply the same `split_matches_by_side` + `arbitrate` pipeline in
  `scan_all` that `save_match_json` already uses, so the overlay's
  per-class colours match what Save Match would produce.
- Keep `scan_all`'s response shape identical
  (`{by_class: dict[display_name, list[handle]], total: int}`) —
  zero front-end changes.
- Preserve `save_match_json` exactly as-is. This change only adjusts
  `scan_all`.
- Single source of truth for class assignment: arbitration runs
  wherever a class assignment is shown or stored.

**Non-Goals:**
- Persisting scan-all results to disk (still in-memory only).
- Adding new arbitration groups, view constraints, or class
  taxonomy entries.
- Changing `arbitrate`'s internals.
- Front-end overlay rendering changes.
- Adding diagnostic fields to scan-all's response (the existing
  `{by_class, total}` is sufficient for the overlay).

## Decisions

### Decision 1: Re-use `split_matches_by_side` + `arbitrate` rather than re-implement

`save_match_json` already has the complete pipeline. The cleanest fix is
to mirror its pipeline in `scan_all`, with two adjustments:

1. Skip the diagnostic accumulation (`side_counts`, `total_matches`,
   `arbitration_counts`) — scan-all doesn't expose them.
2. Skip the disk write + `FILE_STORE.set_match_saved` calls — scan-all
   is preview-only.
3. After arbitration, **collapse** the prefixed-key dict back to the
   flat `{display_name: handles}` shape the overlay expects.

**Rationale:** the two endpoints now share a coherent semantic
(\"what class does each handle belong to, after disambiguation\").
Future tweaks to the pipeline (e.g. a new arbitration group) flow
to both automatically with no further plumbing.

**Alternative considered: factor a shared `_resolve_classes(file)`
helper.** Tempting but premature — the two endpoints' diagnostic
outputs and persistence behaviour still diverge enough that a
shared helper would have a 6-parameter signature on day one. Worth
extracting later once a third caller appears.

### Decision 2: Reverse-map snake_case keys to display names for the collapse step

`arbitrate`'s output keys use the snake_case class name
(`bga_ball.0`, `top_view.fiducial_circle.0`). The overlay's
`by_class` uses display names (`BGABall`, `FiducialCircle`).

Build a one-shot reverse map at the top of the collapse step:

```python
display_by_snake = {v: k for k, v in CLASS_JSON_KEY.items()}
```

For classes not in `CLASS_JSON_KEY` (no snake-case override), the
display name IS the snake-case name — so `display_by_snake.get(snake, snake)`
covers both.

**Rationale:** `CLASS_JSON_KEY` is small (~10 entries) and lives in
the same module. The reverse map is O(N) to build, O(1) per lookup.
No new state.

### Decision 3: Collapse semantics — union handle sets per class

For each arbitrated key like `top_view.bga_ball.0`, the value is a
`list[list[handle]]` — one inner list per match instance, each
containing the handles of that instance. The overlay's `by_class`
needs a flat sorted set of handles per class display name. So:

```python
by_class: dict[str, set[str]] = {}
for key, instance_lists in arbitrated_out.items():
    parsed = _parse_key(key)
    if parsed is None: continue
    _prefix, cls_snake, _idx = parsed
    cls_display = display_by_snake.get(cls_snake, cls_snake)
    bucket = by_class.setdefault(cls_display, set())
    for hl in instance_lists:
        bucket.update(hl)
# Sort and convert to lists for JSON-stable output.
```

**Rationale:** the overlay only cares about handle→class membership;
match-instance grouping is lost (no harm — the overlay never
displayed instance grouping for scan-all anyway).

### Decision 4: View-constrained classes still get skip-when-impossible

The existing `scan_all` already has the optimisation:

```python
allowed = CLASS_VIEW_CONSTRAINTS.get(cls_name)
if allowed is not None and not any(rect_for[v] is not None for v in allowed):
    continue
```

This is preserved verbatim — pointless to compute matches for classes
whose view constraints can't be satisfied. After this gate,
`split_matches_by_side` will properly tag what remains.

### Decision 5: Don't expose arbitration diagnostics in scan-all response

`save_match_json` returns `arbitration_counts` (pool size, derived
pitch, per-class assignment counts, population-fallback flag) so the
dashboard can show what arbitration did. `scan_all` is a preview
endpoint; the overlay doesn't show these. Keeping the response shape
small is good API hygiene.

**Alternative considered: also return arbitration_counts.** Rejected
for now — no caller. Easy to add later if a debug-mode UI wants it.

## Risks / Trade-offs

- **Risk:** the user's mental model of "scan-all = raw matcher
  output" changes. → **Mitigation:** the new behaviour is what
  every other consumer (Match JSON, DRC bundle, rule check) already
  has. Users who want raw matcher output for debugging can hit a
  per-class scan endpoint (out of scope here; trivial to add later).
- **Risk:** an unforeseen edge case in `split_matches_by_side` /
  `arbitrate` that `save_match_json` happens not to exercise.
  → **Mitigation:** both helpers are heavily tested
  (`test_class_arbitration.py` 18 tests, `test_side_regions.py`
  for split). New integration test covers the BGA+Fiducial
  cross-fire path end-to-end through scan-all.
- **Trade-off:** scan-all now does one more `arbitrate` call. For
  9667 instances this is sub-second (`derive_pitch` is one KDTree
  query, `count_neighbors` is one `query_ball_point`). No tests
  exist yet that would catch a real regression in scan-all wall
  time on main; we accept this and rely on manual verification.

## Migration Plan

Single commit; no data migration. Roll-back is the inverse commit.
Existing callers of `GET /api/files/{file_id}/scan-all` see the
same response shape with corrected `by_class` contents.
