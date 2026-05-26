## 1. Mirror the pipeline in `scan_all`

- [x] 1.1 In `app/main.py:scan_all`, replace the flat `by_class: dict[str, list[str]]` set-accumulation with the same view-split + arbitration pipeline `save_match_json` uses:
  - Build `out: dict[str, list[list[str]]]` (prefixed-key shape that `split_matches_by_side` and `arbitrate` consume).
  - Resolve `rect_for = {"top_view": rec.top_view_rect, ...}` from the file record (same as `save_match_json`).
  - Preserve the existing "skip-when-impossible per `CLASS_VIEW_CONSTRAINTS`" gate (`save_match_json` already has it word-for-word; copy it verbatim).
  - For each class + template, call `find_matches_from_pointsets(...)` (unchanged) → `result.matches`.
  - Compute `base_key = f"{CLASS_JSON_KEY.get(cls_name, cls_name)}.{idx}"`.
  - Call `split_matches_by_side(base_key, result.matches, shapes, rec.top_view_rect, rec.bottom_view_rect, rec.side_view_rect, class_name=cls_name)` to tag with view prefix.
  - Append every key-list to `out` via `out.setdefault(k, []).extend(v)`.
- [x] 1.2 After the per-class loop, call `out, _arbitration_counts, _view_drops = arbitrate(out, shapes, CLASS_ARBITRATION_GROUPS)`. Discard the two diagnostic returns — scan-all's response doesn't expose them.
- [x] 1.3 Build the reverse class-key map once: `display_by_snake = {v: k for k, v in CLASS_JSON_KEY.items()}`. Use `display_by_snake.get(snake, snake)` so classes without a snake-case override (where display == snake-case) work via the fallback.
- [x] 1.4 Collapse `out` back to flat `by_class: dict[str, set[str]]`:
  - For each `key, instance_lists` in `out.items()`:
    - `_parse_key(key)` → `(_prefix, cls_snake, _idx)` (skip if parse fails).
    - `cls_display = display_by_snake.get(cls_snake, cls_snake)`.
    - For each `hl` in `instance_lists`: `by_class.setdefault(cls_display, set()).update(hl)`.
  - Convert the inner sets to sorted lists at the very end so the JSON is deterministic.
- [x] 1.5 Compute `total = sum(len(v) for v in by_class.values())` from the *collapsed* dict. Return `{"by_class": by_class, "total": total}` — same shape as before, post-arbitration content.

## 2. Tests

- [x] 2.1 In `tests/test_api.py`, add `test_scan_all_applies_arbitration_to_bga_vs_fiducial_crossfire` (or place it in `tests/test_class_arbitration.py` near related fixtures if that test module has the right helpers). The test SHALL:
  - Build a synthetic library with both a `BGABall` template (one circle, radius `r`) and a `FiducialCircle` template (one circle, radius `r` — same radius, the cross-fire condition).
  - Build a drawing containing a dense BGA grid (e.g. 4×4 at pitch 0.9 mm in `bottom_view_rect`) plus 2 isolated fiducials (e.g. (-10, -10) and (10, 10) outside the grid, ≥ 10 mm from any grid ball).
  - Register the file via `FILE_STORE.register` with appropriate `bottom_view_rect` and `top_view_rect` so view constraints are satisfied.
  - Hit `GET /api/files/{file_id}/scan-all` via the TestClient.
  - Assert the response: `len(data["by_class"]["BGABall"]) == 16` (all grid balls), `len(data["by_class"]["FiducialCircle"]) == 2` (only the real fiducials), no overlap between the two sets.
- [x] 2.2 Add a regression assertion: the same library and drawing, after `POST /api/files/{file_id}/match-json`, produces identical handle-to-class mapping. (Hit save-match, read back via `GET /api/files/{file_id}/match-json`, verify the BGA handles and Fiducial handles are partitioned exactly the same way as the scan-all response.)
- [x] 2.3 Run `uv run pytest tests/test_class_arbitration.py tests/test_api.py` — confirm everything green, including the 18 existing arbitration tests.
- [x] 2.4 Run `uv run pytest` (full project) — 0 regressions.

## 3. Manual verification

- [ ] 3.1 **[USER]** Pull, then on the 9663-ball file: press scan-all; the overlay SHALL render all 9663 balls in the BGABall colour and the 4 isolated fiducials in the FiducialCircle colour, with zero cross-fire (no BGA ball coloured as fiducial). The colours SHALL match what download-Match-JSON produces.

## 4. Archive

- [ ] 4.1 After tasks 1-3 pass, run `/opsx:archive scan-all-applies-arbitration` to fold the modified `viewer-ui` requirement into the live spec.
