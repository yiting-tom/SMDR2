## Why

Semiconductor back-end packaging engineers verify DXF design files by manually
measuring every object (SMD, BGA ball, substrate, lid, fiducial, ...). The
work is slow and error-prone. SMDR2 automates the "find every instance of this
kind of object" step so design rule checking can run over labelled geometry
instead of raw entity dumps.

This change records the initial system build — everything from DXF parsing
through the interactive matching workflow and the design-rule-check hook —
so subsequent changes can extend specific capabilities without re-deriving
existing behaviour.

## What Changes

- Server-side DXF flatten pipeline (ezdxf-based) → JSON drawing primitives
  consumed by both the Canvas viewer and the matching engine.
- Multi-file upload with background pre-processing (parse + library
  pre-match) running in a ProcessPoolExecutor; lifecycle status visible
  to the user (`preprocessing` → `ready_to_match` → `checking_rules` →
  `report`).
- SQLite-backed template library, scoped per-library so different
  customers can have isolated template sets. Default seed of 9 IC-packaging
  classes (smd, substrate, die_area, lid_outer, lid_inner, bga_ball,
  pin_mark, fiducial_mark, 2d_barcode).
- Pattern matching engine — transform-invariant (translate / rotate /
  mirror / scale ∈ [0.95, 1.05] / ε tolerance), single- and multi-entity
  templates, parallel (`N_JOBS` env var, default 1).
- Canvas-based DXF viewer with AutoCAD-style selection (middle-drag pan,
  L→R window-select, R→L crossing-select, left-click pick + pickbox
  tolerance, shift toggle, Esc cascade), chain-select, per-class hotkeys,
  scan workflows (S = scan current selection, A = scan-all overlay),
  library management modal, rule-check side panel with hover + click-pin
  highlight.
- Match JSON export — `{<class>.<index>: [[handles...], ...]}` produced
  on demand, persisted to disk, consumed by the design rule checker.
- Mock design rule checker producing RuleChecking JSON keyed by rule
  name, with a sample geometric rule (substrate-to-first-SMD distance).

## Capabilities

### New Capabilities

- `dxf-pipeline`: server-side DXF parsing + flatten to renderable
  primitives + persistent file metadata + background preprocessing
  pipeline + Match JSON export.
- `template-library`: multi-library SQLite-backed storage of geometric
  templates, organised by object class; per-file library binding with
  in-place reassignment.
- `pattern-matching`: transform-invariant geometric matching for
  single- and multi-entity templates with optional process-pool
  parallelism.
- `viewer-ui`: Canvas DXF viewer with AutoCAD-style selection, scan
  workflows, library / rule-check panels, dashboard.
- `design-rule-checking`: pluggable rule-check hook keyed on Match JSON,
  with a mock implementation and a frontend results panel.

### Modified Capabilities

(none — this is the initial build.)

## Impact

- New Python modules: `app/dxf.py`, `app/storage.py`, `app/library.py`,
  `app/files.py`, `app/jobs.py`, `app/matching.py`, `app/rule_check.py`,
  `app/main.py`.
- New frontend assets: `app/templates/{dashboard,viewer}.html`,
  `app/static/{canvas,dashboard}.js`, `app/static/style.css`.
- New persistent storage layout under `data/`:
  `library.sqlite`, `uploads/`, `parsed/`, `prematch/`, `match/`,
  `rule_check/`.
- New dependencies: fastapi, uvicorn, ezdxf, shapely, rtree, scipy,
  numpy, pillow, jinja2, python-multipart (dev: pytest, ruff, httpx).
- Initial test suite: `tests/` with 48 pytest cases.
- Environment variable: `SMDR2_N_JOBS` (default `1`) controls the
  matching process pool.
