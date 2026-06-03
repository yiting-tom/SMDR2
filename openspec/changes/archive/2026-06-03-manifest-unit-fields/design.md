## Context

`app/drc_bundle.py:_file_entry(rec)` builds each manifest file entry from a
`FileRecord`. The record already carries everything needed:

- `insunits: int | None` — the raw `$INSUNITS` header code (0 unitless, 1 inch,
  2 foot, 4 mm, 5 cm, 6 m, 7 km, 13 micron, …), persisted by `update_parsed`.
- `applied_scale: float` — the multiplier `_maybe_rescale` applied to bring
  coordinates into mm. `detect_scale_factor` (`app/dxf.py:125`) returns 25.4
  (inch), 10.0 (cm), 1000.0 (m), 1.0 (mm / unitless-in-range), or, on the
  unitless heuristic path, one of `{0.001, 0.01, 100, 1000}`.
- `user_unit_override: str | None` — the operator's unit-picker choice, one of
  `{"mm","cm","m","inch","μm"}` or `None`.

`app/dxf.py` owns the canonical vocabulary: `UNIT_TO_SCALE` maps unit→mm
multiplier and `SCALE_TO_UNIT` is its inverse. Micrometre is spelled `μm`
(Unicode U+03BC) internally.

## Goals / Non-Goals

**Goals:**
- Surface per-file `user_unit` and `original_unit` in the manifest using the
  exact vocabulary `{mm, m, inch, cm, um, km}` (ASCII `um`).
- Reuse data already on `FileRecord`; no new persistence or plumbing.

**Non-Goals:**
- Changing how units are detected, stored, or rescaled (`detect_scale_factor`,
  `_maybe_rescale`, the picker) — read-only consumption.
- Fixing the pre-existing gap that `detect_scale_factor` ignores `$INSUNITS`
  7 (km) and 13 (micron) for rescaling — out of scope; `original_unit` still
  *reports* them.
- Adding `km` to the operator picker (none requested).

## Decisions

**D1 — Per-file placement.** Units are a property of each DXF, and a bundle
mixes roles (SBT / BD / POD / RING / LID) that may differ. The fields go on
`file_entry`, not the top level.

**D2 — `user_unit` = override, else effective unit (decision B).**
`user_unit(rec)` returns:
1. `user_unit_override` translated to manifest spelling, when set; else
2. `SCALE_TO_UNIT.get(applied_scale)` translated, when the applied factor maps
   to a named unit (covers 1.0→mm, 10.0→cm, 1000.0→m, 25.4→inch, 0.001→μm→um);
   else
3. `null`.
Step 3 only fires for the rare unitless-heuristic factors `0.01` / `100`, which
correspond to no unit in the picker's vocabulary. *Alternative (A: `null` unless
the operator overrode)* rejected per the operator's choice — the effective unit
is more useful to the external checker and is populated in the common case.

**D3 — `original_unit` = `$INSUNITS` mapped, else `null`.**
`original_unit(rec)` maps `insunits` via `{1:"inch", 4:"mm", 5:"cm", 6:"m",
7:"km", 13:"um"}`; everything else (0 unitless, 2 foot, 3 miles, `None`,
unsupported) → `null`. This reports the header verbatim, independent of whether
the rescaler acts on it.

**D4 — Translation layer, manifest vocabulary.** A small map translates internal
spellings to the manifest vocabulary; the only divergence is `μm → um`
(`mm`/`cm`/`m`/`inch` are identity). `km` and `um` are written directly in the
`insunits` map. Both fields therefore only ever emit `{mm, m, inch, cm, um, km}`
or `null`.

**D5 — Schema: required + nullable; version bump.** Both fields are added to
`file_entry.properties` as `{"type": ["string","null"], "enum": [..6 units.., null]}`
and to `file_entry.required` (the builder always emits them, so they are always
present — possibly `null`). `bundle_version` bumps `1.2.0` → `1.3.0` (additive
minor, mirroring the `1.1.0`→`1.2.0` customer-field bump). `additionalProperties:
false` on `file_entry` means the schema MUST list the new keys, which it now does.

**D6 — No new plumbing.** All three inputs are already on the `FileRecord`
`_file_entry` receives; the helpers import `SCALE_TO_UNIT` from `app.dxf`.

## Risks / Trade-offs

- **`user_unit` is `null` for heuristic factors `0.01` / `100`** → These are
  unitless files the detector rescaled to a non-standard factor; no named unit
  exists. `null` is the honest answer; documented, and the common cases
  (declared units, mm, in-range unitless) all resolve.
- **`original_unit` reports `km`/`um` the rescaler ignores** → `detect_scale_factor`
  returns 1.0 for `$INSUNITS` 7 and 13 today, so geometry may not be rescaled
  even though the header is reported. This is a faithful report of the header,
  not a correctness claim about scaling; the pre-existing rescale gap is out of
  scope.
- **`additionalProperties: false` + new required fields** → A consumer validating
  against the old (1.2.0) schema would reject a 1.3.0 manifest's extra keys; the
  version bump is exactly the signal for that. Major version is unchanged, so
  major-version-pinned consumers are unaffected per the contract.

## Migration Plan

Additive; effective on the next bundle export. No data migration. `bundle_version`
moves to `1.3.0`. Rollback = revert the change (manifests return to 1.2.0 / four
file_entry keys).

## Open Questions

None. (`user_unit` semantics resolved to B; `original_unit` unsupported-unit →
`null` confirmed.)
