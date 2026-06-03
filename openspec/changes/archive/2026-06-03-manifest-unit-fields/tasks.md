## 1. Unit helpers + manifest builder (`app/drc_bundle.py`)

- [x] 1.1 Add `_original_unit(rec)`: map `rec.insunits` via `{1:"inch", 4:"mm", 5:"cm", 6:"m", 7:"km", 13:"um"}`, returning `None` for anything else (0/2/3/None/unsupported).
- [x] 1.2 Add `_user_unit(rec)`: import `SCALE_TO_UNIT` from `app.dxf`; return the translated `rec.user_unit_override` if set, else the translated `SCALE_TO_UNIT.get(rec.applied_scale)` when it maps to a named unit, else `None`. Translation maps internal `μm`→`um` (others identity).
- [x] 1.3 Extend `_file_entry(rec)` to emit `user_unit` and `original_unit`.
- [x] 1.4 Bump `BUNDLE_VERSION` from `"1.2.0"` to `"1.3.0"`.

## 2. Manifest JSON Schema

- [x] 2.1 In `openspec/specs/design-rule-checking/drc-manifest.schema.json`: add `user_unit` and `original_unit` to `$defs.file_entry.properties` as `{"type": ["string","null"], "enum": ["mm","m","inch","cm","um","km", null]}` with descriptions; add both to `file_entry.required`; bump the `bundle_version` example to `"1.3.0"`.

## 3. Tests (`tests/test_drc_bundle.py`)

- [x] 3.1 Assert every `file_entry` carries `user_unit` + `original_unit` and the manifest still validates against the schema with `bundle_version == "1.3.0"`.
- [x] 3.2 Cover the value cases: operator override `μm` → `user_unit == "um"`; no override with `applied_scale` 25.4 → `user_unit == "inch"` and 1.0 → `"mm"`; `insunits` 0/None → `original_unit is None`; `insunits` 4/5/6/1/7/13 → `mm`/`cm`/`m`/`inch`/`km`/`um`; the rare non-standard `applied_scale` (e.g. 100) → `user_unit is None`.

## 4. Verify

- [x] 4.1 Run the full backend test suite (`pytest`) green, including a deterministic-order run.
- [x] 4.2 `openspec validate manifest-unit-fields` passes.
