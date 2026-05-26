## Why

The viewer's scan-all overlay shows the *raw* matcher output — every
template's matches, dumped into a flat `by_class` dict keyed by class
display name, with no arbitration applied. `save_match_json`, by
contrast, runs the full `split_matches_by_side` → `arbitrate` chain
before persisting the JSON.

The two diverge whenever multiple class templates fire on the same
handles (the *whole reason* arbitration exists): in the user's reported
case, the FiducialCircle template hits all 9663 BGA balls (same circle
radius), so the scan-all overlay highlights every grid ball in the
FiducialCircle colour even though the persisted Match JSON correctly
classifies them as BGABall after arbitration.

The fix is to apply the same view-split + arbitration pipeline that
`save_match_json` already uses, then collapse the prefixed-keys result
back to the flat `by_class` shape the overlay expects. Single source of
truth: arbitration runs wherever a class assignment is shown or stored.

## What Changes

- **`app/main.py:scan_all`** — replace the existing "matcher → flat
  per-class handle set" loop with the same pipeline `save_match_json`
  uses:
  1. Skip-when-impossible per `CLASS_VIEW_CONSTRAINTS` (already done)
  2. Run `find_matches_from_pointsets` per template (unchanged)
  3. Tag each match with a view prefix via `split_matches_by_side`
  4. Call `arbitrate(out, shapes, CLASS_ARBITRATION_GROUPS)`
  5. **New collapse step**: walk the arbitrated `dict[key_with_view_prefix, list[list[handle]]]`, parse each key back to a class display name via a reverse `CLASS_JSON_KEY` lookup, and flatten every instance's handles into a single set per class. Return `{by_class: {display_name: sorted_handles}, total}` — same response shape as before.
- **Response shape stays identical** — the front-end overlay reads
  `data.by_class[cls]` exactly as today; nothing about `runScanAll`
  (`app/static/canvas.js:2502`) changes.
- **No arbitration logic changes** — `app/class_arbitration.py` is
  untouched; the 18 existing arbitration tests stay green.

## Capabilities

### New Capabilities

_None._ This is a behavioural fix to an existing capability: the
scan-all preview now reflects the same class assignment the
final-Save Match JSON does.

### Modified Capabilities

- `viewer-ui`: the requirement covering scan-all (the
  "Scan-all overlay with per-class colours" requirement and / or
  the broader scan-all endpoint contract) gains an explicit clause
  that arbitration runs before the result is returned. Existing
  scenarios (per-class colour overlay, hit counts) hold unchanged.
- `design-rule-checking`: the "Match JSON output" requirements are
  unaffected — `save_match_json` is not touched by this change; only
  the preview endpoint gains the same pipeline.

## Impact

- **Code**: `app/main.py:scan_all` — the function body is rewritten
  to mirror `save_match_json`'s pipeline, minus the disk-write and
  diagnostics steps. Approximate diff: ~25 lines changed.
- **APIs**: `GET /api/files/{file_id}/scan-all` response shape
  unchanged (`{by_class: {...}, total: N}`); only the contents of
  `by_class` change — they're now post-arbitration.
- **Tests**: 1 new integration test verifies that scan-all on a
  BGA-grid + far-fiducial drawing returns BGABall for every grid
  ball and FiducialCircle for the four real fiducials, mirroring
  what `save_match_json` would write.
- **Dependencies**: none.
- **Operational**: scan-all gains one more `arbitrate(...)` call.
  For the user's 9667-instance case (`derive_pitch` KDTree query
  + `count_neighbors`) this is sub-second. The recently-merged
  perf-guard on scan-all is at the per-template level and is
  unaffected.
- **Front-end**: zero change. The overlay rendering reads the same
  response shape; the user's experience is "scan-all colours now
  match what Save Match would produce".
