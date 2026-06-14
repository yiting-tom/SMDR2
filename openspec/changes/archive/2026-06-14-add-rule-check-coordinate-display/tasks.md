## 1. Schema + validation (backend)

- [x] 1.1 Update the RuleChecking shape docstring in `app/rule_check.py` to
      document `from_coordinates`, `to_coordinates`, `from_entity`,
      `to_entity` and the two presentation modes (per the spec table).
- [x] 1.2 Extend `_validate_sub_rule` / add helpers in `app/rule_check.py`:
      validate `from_coordinates`/`to_coordinates` as paired length-2 finite
      number arrays; `to_entity` as a non-empty list of length-2 finite
      number arrays (reject `[]` and malformed points); normalise
      `from_entity` to `from` and reject a conflicting `from`/`from_entity`.
- [x] 1.3 Relax the handle-only "renderable group" check so a coordinate-group
      sub-rule needs no `file_id`, while handle-group `file_id` stays
      required; keep all existing handle invariants intact.

## 2. Viewer rendering (frontend)

- [x] 2.1 In `app/static/canvas.js`, carry the new fields on `focusedSubRule`
      when a sub-rule is focused (read `from_coordinates`/`to_coordinates`/
      `to_entity`; treat `from_entity` as `from`).
- [x] 2.2 In `drawFocusedSubRule`: draw a **solid** line between
      `from_coordinates` and `to_coordinates` (world→screen), and a **closed
      dashed polygon** for `to_entity` (points in order, last→first), using
      the existing pass/fail colour. Leave the handle-mode branch untouched.
- [x] 2.3 In `drawFocusedLabel` (or equivalent): render the **distance in mm**
      at the midpoint of the `from_coordinates`/`to_coordinates` line, reusing
      the measure readout's number formatting. `text` continues to show in the
      sidebar for every mode.

## 3. Rule sidebar focus

- [x] 3.1 Make coordinate-mode sub-rules focusable in the rule sidebar (today
      a row is treated as text-only / non-clickable when it has no
      `from`/`to`/`tol`). A row with a coordinate group SHALL focus and draw;
      no cross-file navigation is needed (coords are already in the open view).

## 4. Tests

- [x] 4.1 Extend the envelope-validation tests with coordinate-mode cases:
      valid point-to-point pair (no `file_id`), valid `to_entity` polygon,
      unpaired coordinates rejected, malformed coordinate rejected, empty/bad
      `to_entity` rejected, `from_entity` alias accepted, conflicting
      `from`/`from_entity` rejected.
- [x] 4.2 Add an Upload Rule JSON fixture exercising a coordinate-mode
      sub-rule end to end (POST `/api/versions/{vid}/rule-check/upload`
      validates + persists), confirming the envelope round-trips.

## 5. Verification

- [x] 5.1 `uv run pytest` — new + existing rule-check / envelope tests green;
      no handle-mode regression.
- [x] 5.2 Manual: upload a coordinate-mode RuleChecking JSON (point-to-point
      + `to_entity`) via the dashboard dev affordance, open the viewer, focus
      the sub-rule, and confirm the solid distance line + mm label and the
      closed dashed polygon draw at the right place; a handle-mode sub-rule
      still renders as before (screenshot both).
