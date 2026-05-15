## Why

DXF files in this workflow regularly have either `$INSUNITS = 0`
(unitless / undefined) or a coordinate scale that's been silently
inflated 1000× by some downstream import. Today the user only finds
out something is wrong when the viewer is sluggish, the rule-check
distances look bogus, or — in the worst case — the file fails to open
because the parsed JSON exceeds V8's ~512 MiB string limit (the issue
that motivated `adaptive-curve-flattening`).

The signals to detect this exist server-side at preprocess time
(`$INSUNITS` from the DXF header, bbox diagonal from
`adaptive-curve-flattening`). Surfacing them on the dashboard turns a
silent failure mode into a pre-emptive warning the user sees before
they open the file.

## What Changes

- **Backend** (`app/dxf.py`): expose `$INSUNITS` from the modelspace
  header as part of `RenderOutput` so the preprocess worker can persist
  it alongside the bbox.
- **Schema** (`app/files.py`): add nullable `insunits INTEGER` to the
  `files` table (lightweight migration via the existing
  `PRAGMA table_info` flow); plumb through `FileRecord` and `to_dict`.
- **Worker** (`app/jobs.py` / wherever preprocess lands the bbox today):
  read INSUNITS off the RenderOutput and write it to the file record
  on the same UPDATE that writes bbox / primitive_count.
- **Heuristic** (`app/files.py:FileRecord` or sibling helper):
  derive a `unit_scale_warning` field on the wire (not persisted —
  computed from `insunits` + `bbox`) with three states:
  - `null` — looks fine
  - `"unitless"` — `INSUNITS == 0`, bbox diagonal in plausible
    packaging range (≤ 1000 drawing units)
  - `"suspect_scale"` — bbox diagonal > 1000 drawing units (regardless
    of INSUNITS) **or** INSUNITS == 0 AND diagonal > 100. Hover detail
    spells out the raw values.
- **Dashboard** (`app/static/dashboard.js` slot cell): add a small
  yellow `⚠ unit` badge next to the existing status pill when
  `unit_scale_warning` is non-null. Native `title="..."` tooltip
  carries the human-readable detail (raw INSUNITS value, diagonal,
  why we think it's wrong).

Explicit **non-goal**: auto-rescaling the geometry. Rewriting coords
would invalidate matcher fingerprints / library templates / saved
rule-check results. The change is informational only.

## Capabilities

### New Capabilities
<!-- None — both touched capabilities already exist. -->

### Modified Capabilities

- `dxf-pipeline`: `RenderOutput` SHALL carry the source DXF's
  `$INSUNITS` value so downstream consumers (preprocess worker) can
  persist it without a second DXF read.
- `viewer-ui`: dashboard slot cells SHALL display a warning badge
  when the file's unit scale is suspect, with hover-text spelling out
  the underlying signal.

## Impact

- **Code**: `app/dxf.py` (read header, extend `RenderOutput`),
  `app/files.py` (schema + dataclass + to_dict), the preprocess
  worker (write INSUNITS + compute warning), `app/static/dashboard.js`
  (badge), maybe a tiny CSS rule for the badge.
- **DB migration**: one nullable column added via the existing
  in-place PRAGMA-driven ALTER TABLE path used for prior migrations
  in `FileStore.__init__`. Existing files get `insunits = NULL` and
  show no warning until they're re-preprocessed.
- **Tests**: `tests/test_dxf.py` for header read; `tests/test_files.py`
  for the schema + warning derivation; one small `dashboard.html`-ish
  visual check is optional (no UI test infra in the project today).
- **No impact** on: matching engine, rule check, library, the
  already-archived `optimize-bga-render` / `adaptive-curve-flattening`
  code paths (this change only reads + reports; it doesn't change
  any geometry behaviour).
