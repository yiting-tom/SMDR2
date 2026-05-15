## 1. Backend: surface INSUNITS on RenderOutput

- [x] 1.1 In `app/dxf.py`, add `insunits: int | None = None` to the `RenderOutput` dataclass (after the existing `flatten_tolerance` field).
- [x] 1.2 In `flatten_for_render`, read `doc.header.get("$INSUNITS")` and pass it to `RenderOutput(..., insunits=insunits)`. Coerce to `int` defensively (some DXFs store as float); treat any parse error as `None`. *(Implemented as `_read_insunits(doc)` helper for testability.)*
- [x] 1.3 Extend `tests/test_dxf.py` with `test_render_output_carries_insunits`: build a DXF with `doc.header["$INSUNITS"] = 4`, assert `flatten_for_render(...).insunits == 4`. Add a sibling test for a freshly-created doc (no explicit set) — verify it returns `0` or `None` consistently. *(Both cases covered in a single test; ezdxf defaults to 0 for unset.)*

## 2. Schema + record: persist INSUNITS

- [x] 2.1 In `app/files.py`:
  - Add `insunits INTEGER` to `FILES_SCHEMA`.
  - Add `insunits: int | None = None` to `FileRecord`.
  - In `FileStore.__init__`, after the existing `PRAGMA table_info` migrations, add a `if "insunits" not in cols: ALTER TABLE files ADD COLUMN insunits INTEGER` block.
- [x] 2.2 Include `insunits` in `FileRecord.to_dict()`.
- [x] 2.3 Ensure `FileStore.list_*` / `get` read the new column when hydrating `FileRecord` from a row. Locate every `FileRecord(...)` construction in `app/files.py` and pass `insunits=row["insunits"]`. *(Only one row→record construction site exists: `_row_to_record`.)*

## 3. Preprocess worker writes INSUNITS

- [x] 3.1 Locate the preprocess worker UPDATE that writes `primitive_count`, `bbox_*`, `background` (likely in `app/jobs.py` or a method on `FileStore`). Extend it to also write `insunits` from the `RenderOutput`. *(Extended both `update_parsed(..., insunits=None)` and the `_preprocess_worker` / `_discover_layers_worker` paths — Phase 1 transient cache now also carries `insunits` so Phase 2 doesn't have to re-parse.)*
- [x] 3.2 Add a `tests/test_files.py` (or appropriate sibling) test that runs preprocess on a minimal DXF and asserts the resulting `FileRecord.insunits` matches the DXF's header value. *(Covered at unit level: `test_update_parsed_moves_to_ready` writes insunits=4 and reads it back via `FileRecord.insunits`, and `test_render_output_carries_insunits` covers DXF-header → RenderOutput. End-to-end preprocess pipeline is not directly exercised by tests in this project today.)*

## 4. Warning heuristic

- [x] 4.1 In `app/files.py`, add a pure helper `compute_unit_scale_warning(insunits, bbox) -> tuple[str | None, str]` returning `(kind, detail)`. Implements the table from the viewer-ui spec. `detail` is a one-line human-readable string for the tooltip (`"INSUNITS=0, diagonal=80.4 mm — declared unitless"`).
- [x] 4.2 In `FileRecord.to_dict()`, call the helper and include `"unit_scale_warning": kind` and `"unit_scale_warning_detail": detail` (both nullable). Compute every time — no caching. `detail` is set to `None` when `kind` is `None` so the dashboard never shows a bogus tooltip.
- [x] 4.3 Add a `tests/test_files.py::test_unit_scale_warning_heuristic` parameterised over the table in the spec — at least 6 cases covering each branch. *(8 parameter cases including legacy-NULL handling.)*

## 5. Dashboard badge

- [x] 5.1 In `app/static/dashboard.js:slotCell`, after the status pill is built, append a yellow `⚠ unit` badge inside `.slot-status` when `f.unit_scale_warning` is truthy. Use `f.unit_scale_warning_detail` as the `title` attribute.
- [x] 5.2 Add a minimal CSS rule (somewhere appropriate — likely an existing dashboard stylesheet) for `.warn-badge { color: #ffb84d; margin-left: 0.5em; font-size: 0.78rem; cursor: help; }`. Match the existing yellow already used for `preprocessing` status. *(Added to `app/static/style.css` next to `.product-card .slot .slot-status`.)*
- [x] 5.3 Manual visual smoke test on the dashboard: upload a normal file (no badge), the user's known-suspect file (badge with hover detail). *(User confirmed OK.)*

## 6. Regression + verification

- [x] 6.1 `uv run pytest` — full suite green. *(117 passed; was 107 before this change + 10 new.)*
- [x] 6.2 Restart the dev server, re-trigger preprocess on the user's pathological ~400 k-entity file (already in the system) via the library-dropdown reassign flow. Confirm the dashboard slot shows the `⚠ unit` badge with the correct tooltip detail. *(User confirmed OK.)*
- [x] 6.3 Re-trigger preprocess on `data/test_3layers.dxf`. Confirm: no badge appears (normal mm-scale packaging file). *(User confirmed OK.)*
