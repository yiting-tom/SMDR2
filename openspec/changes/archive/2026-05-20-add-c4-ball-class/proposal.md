## Why

The canonical class list only covers package-level ball-type interconnect
(`BGABall`). Real packaging DXFs also carry **C4 (Controlled Collapse Chip
Connection) bumps** — the flip-chip-level ball interconnect sitting under
the die. Today they have nowhere to land in the template library, so users
either mis-file them under `BGABall` or skip them entirely, which corrupts
downstream counts and rule checks.

## What Changes

- Add a new canonical class `C4Ball` (snake_case JSON key `c4_ball`) to
  `DEFAULT_CLASSES` and `CLASS_JSON_KEY`.
- Position it **immediately before `BGABall`** in the canonical order so
  ball-type interconnect classes cluster together
  (`…SMD-2T → C4Ball → BGABall → Protrusion…`).
- Bump the canonical class count from **15 → 16**.
- Assign a viewer toolbar color in `CLASS_COLORS` (canvas.js).
- On boot, the existing migration's `INSERT OR IGNORE` loop over
  `DEFAULT_CLASSES` SHALL seed `C4Ball` into every existing library, and
  the re-rank pass SHALL slot it into its canonical position.

## Capabilities

### New Capabilities
<!-- none — this extends an existing capability -->

### Modified Capabilities
- `template-library`: canonical default-class list grows from 15 to 16
  entries and gains a `C4Ball` ↔ `c4_ball` mapping; canonical order
  inserts `C4Ball` directly before `BGABall`.

## Impact

- **Code**: `app/library.py` (DEFAULT_CLASSES, CLASS_JSON_KEY),
  `app/static/canvas.js` (CLASS_COLORS).
- **Docs**: `README.md` §6 class-list summary line.
- **Spec**: `openspec/specs/template-library/spec.md` (numbered list,
  count, JSON-key table, add a scenario for the new mapping).
- **DB migration**: none beyond the existing idempotent
  `INSERT OR IGNORE` seed loop — `C4Ball` automatically lands in every
  library on next boot.
- **No breaking changes**: existing libraries keep all current classes;
  the new class simply appears in the toolbar at its canonical position.
