## Why

DXFs with a unit-scale anomaly (most commonly a 1000× inflated bounding
box from a unitless export) currently only get a yellow `⚠ unit` badge
on the dashboard — the geometry is left at 1000×. Downstream every
absolute-distance tolerance (matcher `TOLERANCE_ABS = 0.01` mm, scale
gate `SCALE_MIN/MAX = 0.9999/1.0001`, rule-check distance thresholds)
silently breaks: a chamfer cap of 0.01 mm against a 50-m feature is a
10⁻⁵ relative tolerance, and a "3 mm spacing" rule sees the value as
3000. The user just experienced this — left-right-mirrored substrates
in a suspect file refuse to match; 180°-rotated substrates fail
without even surfacing as near-misses.

The classifier already knows when a file is suspect; the missing piece
is acting on it. Doing the rescale inside preprocessing means every
downstream consumer (matcher, rule-check, viewer, thumbnails, rule
distances) keeps working in mm with no further changes.

- Preprocess auto-applies a scale factor `M` to all flattened
  coordinates, primitive geometry, and the recorded bbox whenever a
  new `detect_scale_factor` helper concludes the file is in the wrong
  units. Semantics: `rescaled_coord = original_coord * M`. The
  factor is recorded on the file row so the dashboard can
  communicate what happened. Supported factors:

  | Source | Factor `M` | Trigger |
  |---|---|---|
  | unitless 1000× too big | `0.001` | INSUNITS ∈ {0, NULL} AND chosen power-of-10 brings bbox into 10–5000 mm |
  | unitless 100×, 10×, 10×-too-small, etc. | `10^k` for `k ∈ {-4..+4}` | same rule, closest-to-1 factor wins |
  | inch declared, treat as inch | `25.4` | INSUNITS = 1 (inch) — trusted declaration |
  | cm declared, treat as cm | `10` | INSUNITS = 5 (cm) — trusted declaration |
  | m declared, treat as m | `1000` | INSUNITS = 6 (m) — trusted declaration |
  | mm declared / no-op | `1.0` | INSUNITS = 4, or no trigger fires |

  Safety guard for the unitless path: ONLY rescale when the chosen
  factor differs from 1 by ≥ one order of magnitude (`M ≤ 0.1` or
  `M ≥ 10`). Marginal cases (M = ~3×, ~7×) keep `M = 1.0` and fall
  back to the existing "suspect" badge so a human decides.

- The `unit_scale_warning` payload extends from `(kind, detail)` to
  `(kind, detail, applied_scale)`. When `applied_scale != 1.0` the
  badge becomes an informational pill ("auto-rescaled 1/1000",
  "auto-rescaled ×25.4 (inch → mm)") rather than a warning to chase,
  and the pill title spells out the source unit and the resulting
  factor.
- One-time migration on startup re-preprocesses every existing file
  whose `applied_scale == 1.0` but whose recorded INSUNITS + bbox
  would now resolve to a non-`1.0` factor under the new detector, so
  already-uploaded data benefits without a manual re-upload.
- Persisted Match JSON (`data/match/<file_id>.json`) for any file
  whose `applied_scale` changes is **invalidated** — the saved
  point sets reference the old coordinates and cannot be silently
  rescaled without rerunning the pipeline. The file moves back to
  `ready_to_match` and the dashboard surfaces a one-line banner
  explaining why.

## Capabilities

### New Capabilities

None — this change extends the existing pipeline and the existing
unit-warning surface; no new capability is introduced.

### Modified Capabilities

- `dxf-pipeline`: preprocessing gains an auto-rescale step that runs
  before primitives, bbox, and EntityShape outputs are persisted.
  The recorded `bbox` and the rendered primitives reflect the
  rescaled geometry.
- `viewer-ui`: the dashboard's per-file `unit_scale_warning` payload
  carries `applied_scale`, and the badge text/title flips from
  "suspect" to "auto-rescaled" when a factor was applied.

The Match JSON invalidation on rescale is grouped under
`dxf-pipeline` alongside the existing "Side-region edits invalidate
saved match" requirement, since both live in preprocessing.

## Impact

- **Code**: `app/dxf.py` (new `detect_scale_factor` helper +
  rescale step), `app/files.py` (persisted `applied_scale` column,
  `to_dict` payload), `app/main.py` (file-detail payload + startup
  migration), `app/static/dashboard.js` (info pill vs warn badge),
  one-shot migration in the startup path.
- **Data**: `files` table gains an `applied_scale REAL NOT NULL
  DEFAULT 1.0` column. Match JSONs for files whose factor changes
  are removed. Cached preprocessed artifacts (`data/parsed/`,
  `data/prematch/`) for affected files are rebuilt by the
  re-preprocess job.
- **External**: no API breaking changes — `unit_scale_warning_detail`
  text changes for users who script against it (informational only).
  New numeric field `applied_scale` on the file payload.
- **Tests**: `tests/test_files_unit_warning.py` and a new
  `tests/test_dxf_auto_rescale.py` covering every row in the
  factor table above (1000× big, 1000× small, 10×, 25.4× from
  inch, 10× from cm, marginal-case no-op).
