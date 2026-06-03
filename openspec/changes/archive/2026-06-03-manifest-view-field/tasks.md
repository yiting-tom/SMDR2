## 1. View helper + manifest builder (`app/drc_bundle.py`)

- [x] 1.1 Add `_views(rec)`: return `["top"/"bottom"/"side"]` for each of `rec.top_view_rect` / `rec.bottom_view_rect` / `rec.side_view_rect` that is set (truthy), in the canonical order top → bottom → side; `[]` when none are set.
- [x] 1.2 Extend `_file_entry(rec)` to emit `view`.
- [x] 1.3 Bump `BUNDLE_VERSION` from `"1.3.0"` to `"1.4.0"`.

## 2. Manifest JSON Schema

- [x] 2.1 In `openspec/specs/design-rule-checking/drc-manifest.schema.json`: add `view` to `$defs.file_entry.properties` as `{"type": "array", "items": {"type": "string", "enum": ["top","bottom","side"]}, "uniqueItems": true}` with a description; add `view` to `file_entry.required`; bump the `bundle_version` example to `"1.4.0"`.

## 3. Tests (`tests/test_drc_bundle.py`)

- [x] 3.1 Assert `view` reflects the set side-region rects in canonical order: all three set → `["top","bottom","side"]`; only top set → `["top"]`; none set → `[]`; manifest validates against the schema with `bundle_version == "1.4.0"`. (Set rects via the existing store/endpoint path used elsewhere in the tests.)
- [x] 3.2 Update the `bundle_version` pin test to `"1.4.0"`.

## 4. Verify

- [x] 4.1 Run the full backend test suite (`pytest`) green, including a deterministic-order run.
- [x] 4.2 `openspec validate manifest-view-field` passes.
