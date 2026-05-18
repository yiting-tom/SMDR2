> Retrospective change — work landed before the OpenSpec proposal was
> written, so every task is already checked off. Listed in implementation
> order for the archive.

## 1. Library / taxonomy core (`app/library.py`)

- [x] 1.1 Replace `DEFAULT_CLASSES` with the new 14-name ordered list (Substrate, Pin-1, Lid, LidOuter, LidInner, DieArea, FiducialCircle, FiducialCross, SMD-2T, BGABall, 2DBarcode, SMD-3T, SMD-8T, SMD-14T).
- [x] 1.2 Add `DEPRECATED_CLASSES = frozenset({"FiducialMark", "Side"})`.
- [x] 1.3 Add the `CLASS_JSON_KEY: dict[str, str]` map (display ID → snake_case match-JSON key) for every entry in `DEFAULT_CLASSES`.
- [x] 1.4 Drop the now-deprecated `"fiducial_mark": "FiducialMark"` row from `LEGACY_CLASS_RENAME`.
- [x] 1.5 Extend `Store._migrate()` to: (a) delete every template / class row whose name is in `DEPRECATED_CLASSES`, (b) `INSERT OR IGNORE` every `DEFAULT_CLASSES` name into every existing library so newly-added defaults exist before re-rank, (c) re-rank each library's `classes` rows so the order matches `DEFAULT_CLASSES`, with user-added classes pushed to the tail (relative order preserved).
- [x] 1.6 Confirm idempotency: a second migration pass on the same DB is a no-op.

## 2. Match JSON serializer (`app/main.py`)

- [x] 2.1 Import `CLASS_JSON_KEY` from `app.library`.
- [x] 2.2 In `save_match_json`, derive `json_cls = CLASS_JSON_KEY.get(cls_name, cls_name)` per class and build `base_key = f"{json_cls}.{idx}"`.
- [x] 2.3 Refresh the module-level docstring on the match-JSON endpoint to document the snake_case `<class>` token and the `{view}.{class}.{idx}` shape.

## 3. Rule-checker prefixes (`app/rule_check.py`)

- [x] 3.1 Switch the prefix arguments in Rule1 from `"Substrate"` / `"SMD-2T"` to `"substrate"` / `"smd_2t"`.
- [x] 3.2 Switch the prefix arguments in Rule2 from `"BGABall"` to `"bga_ball"` for both SBT and POD lookups.
- [x] 3.3 Switch the prefix arguments in Rule3 from `"Substrate"` / `"SMD-2T"` to `"substrate"` / `"smd_2t"`.
- [x] 3.4 Leave the user-facing description strings ("Substrate", "SMD-2T", "BGABall") unchanged — those are display labels, not lookup keys.

## 4. Frontend / viewer (`app/static/canvas.js`)

- [x] 4.1 Remove the `"FiducialMark"` color entry; add `"FiducialCircle"` (teal) and `"FiducialCross"` (darker teal sibling).
- [x] 4.2 Remove the `"Side"` color entry.
- [x] 4.3 Confirm `COLLAPSED_TOOLBAR_CLASSES` and `DEFAULT_FOLDED_CLASSES` already equal `{SMD-3T, SMD-8T, SMD-14T}` so the fold group needs no edit.

## 5. Tests + docstrings

- [x] 5.1 Update `tests/test_rule_check.py` mock match-JSON dicts so every `Substrate.0` / `SMD-2T.0` / `BGABall.0` key becomes the snake_case form.
- [x] 5.2 Update `app/side_regions.py` module docstring example from `SMD-2T.0` → `smd_2t.0`.
- [x] 5.3 Run `uv run pytest tests/` and confirm green (151 passed at landing time).

## 6. Data cleanup

- [x] 6.1 `rm data/match/*.json` (4 files).
- [x] 6.2 `UPDATE files SET match_saved=0 WHERE match_saved=1` against `data/library.sqlite` (3 rows).
- [x] 6.3 Run a one-shot Library boot to verify the migration: classes load in the new order, `FiducialMark` template is gone, no `Side` row remains.
