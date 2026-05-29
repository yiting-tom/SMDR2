## 1. Canonical signature helper

- [x] 1.1 In `app/library.py`, add module-level constant `TEMPLATE_DEDUP_BUCKET = 10**4` and module imports `import logging` + `logger = logging.getLogger(__name__)`. Place the constant near `DEPRECATED_CLASSES` / `PRODUCT_SCOPED_CLASSES` so the precision dial is discoverable.
- [x] 1.2 Add a module-level pure function `template_signature(entity_point_sets: list[list[tuple[float, float]]]) -> tuple`:
  - Compute global centroid `(gx, gy)` across every point in every entity.
  - For each entity: subtract `(gx, gy)`, multiply by `TEMPLATE_DEDUP_BUCKET`, `round()` to int, sort the `(int, int)` tuples lexicographically.
  - Sort the per-entity tuples lexicographically.
  - Return the nested tuple as the dedup key. Empty input → `()`.
- [x] 1.3 Add a docstring naming the precision (0.1 µm, parallel to `_radius_bucket_key`) and explicitly stating the three invariances (translation YES, entity-order YES, vertex-order YES) and three NON-invariances (rotation NO, scale NO, reflection NO).
- [x] 1.4 Add a private helper `_template_signature_cached(t: Template) -> tuple` that reads `getattr(t, "_signature", None)`, computes via `template_signature(t.entity_point_sets)` if absent, sets via `object.__setattr__(t, "_signature", sig)` to bypass any future frozen-dataclass guard, returns the signature.

## 2. Library.add_template_for_file dedup branch

- [x] 2.1 Change signature: `def add_template_for_file(self, template: Template, *, product_id: str | None) -> tuple[Template, bool]`.
- [x] 2.2 At entry, compute `sig = template_signature(template.entity_point_sets)` once. Compute `effective_pid = product_id if is_product_scoped(template.class_name) else None`.
- [x] 2.3 Look up existing templates **in the same scope**:
  - For **library-scoped** classes (`not is_product_scoped(template.class_name)`): iterate `self._templates.get(template.class_name, [])` and compare signatures via `_template_signature_cached`.
  - For **product-scoped** classes: re-load via `self.store.load_library(self.library_id, product_id=effective_pid)` and iterate the returned `templates_by_class.get(template.class_name, [])`. (Cache is unreliable for product-scoped classes — see design D3.)
- [x] 2.4 On signature match: return `(existing, True)`. Do NOT call `self.store.insert_template`. Do NOT append to `self._templates`. The class IS added to `self._templates` only if it wasn't there yet (existing add-new-class branch stays).
- [x] 2.5 On no match: keep the existing append + insert flow, return `(template, False)`.
- [x] 2.6 Update `Library.add_template(self, template: Template)` to return the tuple from its delegated call.
- [x] 2.7 Audit other callers via `grep -rn "add_template\b\|add_template_for_file" app/ tests/ --include='*.py'`. Update each to either ignore the return value (statement form is fine) or unpack the tuple where the boolean is actually used. (All callers use statement form; tuple return is backwards-compatible.)

## 3. Commit endpoint surfaces `already_existed`

- [x] 3.1 In `app/main.py::commit`, change the call site to `stored, already_existed = lib.add_template_for_file(tmpl, product_id=rec.product_id)`.
- [x] 3.2 Use `stored.id` (not `tmpl.id`) in the response — on a dedup hit `stored` is the existing template and its id is the operator-facing one.
- [x] 3.3 Add `"already_existed": already_existed` to the response dict alongside the existing fields.
- [x] 3.4 The `count` field stays as `lib.count(tmpl.class_name)` — it's the post-call total within the cache's view, which is correct in both branches because on hit we did not append.

## 4. Viewer surfaces the no-op outcome

- [x] 4.1 In `app/static/canvas.js::commitCurrentTemplate`, after the `/commit` POST resolves and the JSON is parsed, read `data.already_existed`.
- [x] 4.2 When `data.already_existed` is true, set the status bar to `template already in library (#${data.count})` and `return` early — bypass any scan-all overlay merge / auto-refresh path.
- [x] 4.3 When false, the existing `saved ${class_name} template (#${count})` message and existing post-commit code path remain unchanged.

## 5. Startup duplicate detection

- [x] 5.1 At the end of `Library.__init__`, after the default-class seeding loop, call a new private method `self._warn_on_duplicate_signatures()`.
- [x] 5.2 Implement `_warn_on_duplicate_signatures(self)`: for each `class_name`, group `self._templates[class_name]` by `template_signature(t.entity_point_sets)`. For each group with `len(group) > 1`, `logger.warning(...)` once with the library_id, class_name, and count.
- [x] 5.3 Leave the duplicate rows in place (no in-memory or persistent migration).

## 6. Tests — signature properties

- [x] 6.1 Create `tests/test_library_dedup.py` with imports `from app.library import template_signature, Library, LibraryRegistry, Store, Template, TEMPLATE_DEDUP_BUCKET` and a small `_pts(*coords)` helper that returns `list[list[tuple[float, float]]]`.
- [x] 6.2 `test_signature_invariant_under_translation` — same shape at two absolute positions produces identical signature.
- [x] 6.3 `test_signature_invariant_under_entity_order_permutation` — `[A, B]` and `[B, A]` for the same multi-entity geometry produce identical signatures.
- [x] 6.4 `test_signature_invariant_under_vertex_order_permutation` — same entity with shuffled point order produces identical signature.
- [x] 6.5 `test_signature_distinguishes_under_rotation` — 90° rotation produces a different signature.
- [x] 6.6 `test_signature_distinguishes_under_above_bucket_drift` — shifting one point by 2×10⁻⁴ mm produces a different signature (above the bucket grid).
- [x] 6.7 `test_signature_collapses_sub_bucket_drift` — shifting one point by 1×10⁻⁶ mm produces the same signature (sub-bucket).
- [x] 6.8 `test_signature_function_is_deterministic` — same input twice produces identical tuples (paranoid check against dict-iteration hash leaks).

## 7. Tests — add_template_for_file dedup behaviour

- [x] 7.1 `test_add_template_dedup_returns_existing_for_library_scoped` — build a fresh `Store` + `Library`, add the same library-scoped class template twice → second call returns `(first, True)`; `lib.count(cls)` is 1; `store.load_library` shows one row.
- [x] 7.2 `test_add_template_no_dedup_across_classes` — same signature, different class → second call returns `(_, False)`; two rows persisted.
- [x] 7.3 `test_add_template_no_dedup_across_libraries` — same signature, different library → second call returns `(_, False)`; one row in each library.
- [x] 7.4 `test_add_template_dedup_for_product_scoped_within_same_product` — product-scoped class (e.g. `Substrate`) committed twice with `product_id="P1"` → second returns `(_, True)`; only one row for `(L, Substrate, P1)`.
- [x] 7.5 `test_add_template_no_dedup_for_product_scoped_across_products` — product-scoped class same signature committed with `product_id="P1"` and `product_id="P2"` → both return `(_, False)`; two rows.

## 8. Tests — commit endpoint round-trip

- [x] 8.1 `test_commit_endpoint_surfaces_already_existed_flag` — using `TestClient(app)`, upload a tiny DXF, run the preprocess pipeline so handles are available, commit a 2-handle selection twice under a unique class (`f"DedupTest-{uuid.uuid4().hex[:8]}"`). Assert:
  - First response: `already_existed=False`, captures a `template_id`.
  - Second response: `already_existed=True`, `template_id` equals the first's, `count` unchanged.
- [x] 8.2 If the existing API test harness has a fixture that builds a parsed file end-to-end (check `tests/conftest.py` and `tests/test_api.py`), reuse it. Otherwise build a minimal DXF fixture in the test using `ezdxf` direct calls. (Hand-crafted minimal parsed JSON written under `parsed_path(fid)` with try/finally cleanup — lighter than a real preprocess.)

## 9. Tests — startup WARNING

- [x] 9.1 `test_load_library_warns_on_pre_dedup_duplicates` — seed two same-signature rows directly via `Store.insert_template` (bypass dedup), instantiate `Library`, capture logs via `caplog.at_level(logging.WARNING, logger="app.library")`, assert exactly ONE WARNING record whose message contains the library id, class name, and the count `2`.
- [x] 9.2 `test_load_library_no_warning_when_no_duplicates` — same setup but only one template; assert zero WARNING records from `app.library`.

## 10. Tests — existing test compatibility

- [x] 10.1 In `tests/test_library.py`, locate `test_all_templates_returns_indexed_tuples`. Make t2's geometry distinct from t1's (e.g. 4-point square vs 5-point pentagon, or shift one vertex by > 10⁻⁴ mm) so dedup does not collapse them and the test's intent (two indexed rows under SMD-2T) is preserved.
- [x] 10.2 `grep -rn "lib\.add_template\b\|\.add_template_for_file\b" tests/ --include='*.py'` and audit every call site. Any test that relies on the previous None return (e.g. `assert lib.add_template(t) is None`) gets updated to either drop the assertion or expect a tuple. (No call site asserted on the return shape; statement form is back-compat with tuple return.)

## 11. Project-wide regression check

- [x] 11.1 Run `pytest tests/test_library_dedup.py -q` — 15 new tests pass.
- [x] 11.2 Run `pytest tests/test_library.py -q` — 40 pre-existing library tests pass.
- [x] 11.3 Run `pytest tests/test_matching_circle_fast_path.py tests/test_circle_path_parity.py -q` — 69 matching tests pass.
- [x] 11.4 Run `pytest -q` (full project) — 533 pass, 1 fail. The single failure (`test_save_match_post_with_missing_parsed_file_returns_synchronous_error` in `tests/test_match_json_constraints.py`) is a pre-existing flake on plain `main` (verified via `git stash` + run), caused by another test polluting the module-global `jobs._jobs` dict. NOT introduced by this change.

## 12. Manual verification

- [ ] 12.1 **[USER]** Double-commit path — open viewer with any non-product-scoped class (e.g. `SMD-2T`). Frame-select a pattern, press Enter (status: `saved SMD-2T template (#1)`). With nothing changed in the selection, press Enter again. Status SHALL read `template already in library (#1)`. Class chip count SHALL remain `1`. DevTools Network → second `/commit` POST response SHALL contain `already_existed: true` and a `template_id` equal to the first response's.
- [ ] 12.2 **[USER]** Translation re-frame — frame a DIFFERENT on-canvas instance of the same shape (same physical part rendered at a different absolute position), press Enter. Status SHALL read `template already in library`. Chip count unchanged. If Scan All is active, overlay SHALL NOT flash / re-render.
- [ ] 12.3 **[USER]** Rotation re-frame — frame a copy of the templated shape rotated 90° (if available in the DXF), press Enter. Status SHALL read `saved SMD-2T template (#2)`. Chip count increments to `2`.
- [ ] 12.4 **[USER]** Product-scoped path — upload two files bound to two different products. In product A, commit a `Substrate` template. In product B, commit a `Substrate` template with translation-equivalent geometry. Both commits SHALL report `already_existed: false`; each product's library admin view SHALL show its own row.

## 13. Archive

- [ ] 13.1 After tasks 1–12 pass, run `/opsx:archive dedup-templates-on-commit`.
