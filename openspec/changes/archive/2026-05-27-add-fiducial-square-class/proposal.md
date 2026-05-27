## Why

The canonical class list covers two fiducial shapes (`FiducialCircle`,
`FiducialCross`) but real packaging DXFs also carry **square fiducial
marks** — square solder-resist or copper alignment features distinct
from circular dots or cross-hairs. Today they have nowhere canonical to
land in the template library, so users either mis-file them under one of
the existing fiducial classes or omit them, which corrupts downstream
counts and rule checks for fiducial population.

## What Changes

- Add a new canonical class `FiducialSquare` (snake_case JSON key
  `fiducial_square`) to `DEFAULT_CLASSES` and `CLASS_JSON_KEY`.
- Position it **immediately after `FiducialCross`** in the canonical
  order so the three fiducial classes cluster together
  (`…DieArea → FiducialCircle → FiducialCross → FiducialSquare → SMD-2T…`).
- Bump the canonical class count from **16 → 17**.
- Assign a viewer toolbar color in `CLASS_COLORS` (canvas.js): `#00acc1`
  — one shade darker teal, continuing the existing fiducial-family
  gradient (`FiducialCircle #4dd0e1` → `FiducialCross #26c6da`
  → `FiducialSquare #00acc1`).
- On boot, the existing migration's `INSERT OR IGNORE` loop over
  `DEFAULT_CLASSES` SHALL seed `FiducialSquare` into every existing
  library, and the re-rank pass SHALL slot it into its canonical
  position immediately after `FiducialCross`.

## Capabilities

### New Capabilities
<!-- none — this extends an existing capability -->

### Modified Capabilities
- `template-library`: canonical default-class list grows from 16 to 17
  entries and gains a `FiducialSquare` ↔ `fiducial_square` mapping;
  canonical order inserts `FiducialSquare` directly after `FiducialCross`.

## Impact

- **Code**: `app/library.py` (`DEFAULT_CLASSES`, `CLASS_JSON_KEY`),
  `app/static/canvas.js` (`CLASS_COLORS`).
- **Docs**: `README.md` §6 class-list summary line (if present).
- **Spec**: `openspec/specs/template-library/spec.md` (numbered list,
  count, JSON-key table, add a scenario for the new mapping).
- **DB migration**: none beyond the existing idempotent
  `INSERT OR IGNORE` seed loop — `FiducialSquare` automatically lands in
  every library on next boot.
- **No breaking changes**: existing libraries keep all current classes;
  the new class simply appears in the toolbar at its canonical position.
- **No `LEGACY_CLASS_RENAME` entry** (no historical class collapses into
  `FiducialSquare`).
- **No `CLASS_VIEW_CONSTRAINTS` entry** (fiducials are view-unconstrained,
  matching existing `FiducialCircle` / `FiducialCross`).
- **No arbitration group changes** (square fiducial has a distinct shape
  signature from BGA balls and circle fiducials — pure pattern matching
  already separates them).
