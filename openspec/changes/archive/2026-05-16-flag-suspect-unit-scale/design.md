## Context

Two related signals are already available server-side after preprocess
but never reach the user pre-viewer:

1. **`$INSUNITS` header value** — DXF spec enum: 0 = unitless, 1 = inch,
   2 = foot, 4 = mm, 5 = cm, 6 = m, … Packaging files should be 4 (mm),
   and in practice they're frequently 0 because the original CAD
   session never declared units. When a `0` file is imported into a
   downstream system that defaults to "meters", every coordinate
   silently picks up a 1000× factor.
2. **Bbox diagonal** — already computed for `adaptive-curve-flattening`
   via the DXF header's `$EXTMIN` / `$EXTMAX` shortcut. Packaging
   designs are mm-scale: a typical diagonal is 30–500 mm. Anything
   above 1000 drawing units in this workflow is almost certainly
   either a units bug or a non-packaging file.

Currently the user only sees the symptom (viewer slow, rule-check
distances bizarre, file fails to open) — not the cause. The fix is
surfacing the diagnosis at the dashboard.

The DB schema already has a precedent for nullable, ALTER-added
columns (see `app/files.py:147-179` for the layered migration
pattern). This change follows the same model.

## Goals / Non-Goals

**Goals:**
- A user uploading a `$INSUNITS = 0` or 1000×-scale file SHALL see a
  visible warning on the dashboard before they open the viewer.
- The warning SHALL carry enough detail to be actionable (raw
  INSUNITS, raw bbox diagonal) — no detective work required.
- Cost: one extra header read per preprocess (microseconds), one
  extra column on the `files` table, one extra DOM badge per slot
  cell.

**Non-Goals:**
- **Auto-rescaling the geometry**: too risky. Rewriting coordinates
  would change every primitive's position, which in turn invalidates:
  - matcher fingerprints (point distances would scale)
  - any saved templates, products, rule-check results that reference
    handles in the old coordinate frame
  - the existing parsed JSON cache (`data/parsed/<file_id>.json`)
  The user is best placed to decide whether to fix the source DXF
  (set `$INSUNITS = 4` and re-export) or accept the scale and live
  with it.
- **Per-rule unit calibration**: rule values live in mm today
  (`Rule3: distance < 5 mm`). If a file's true unit is something else,
  the rule values would be wrong — but that's an out-of-scope question
  about the rule schema, not about flagging the file.
- **Detecting unit mismatches across files of the same product**: two
  files in a product with different `$INSUNITS` is a stronger signal
  than either file alone. Useful, but cross-file logic is a second
  step; this change only flags per-file.

## Decisions

### 1. Storage: one nullable INTEGER column

```sql
ALTER TABLE files ADD COLUMN insunits INTEGER
```

NULL for legacy rows (treat as "unknown — no warning until
re-preprocessed"). The column lives on `files` because it's a stable
attribute of the source DXF; we don't want to derive it from the
parsed JSON at read time. Bbox is already in columns; combine with
the new `insunits` in derivation.

Alternative considered — JSON column `metadata` for misc DXF header
bits. Rejected: YAGNI, and one INTEGER is cheaper to query.

### 2. Where to read INSUNITS

ezdxf exposes the header value at `doc.header.get("$INSUNITS")`. It's
already cheap because `readfile` parses the header. Extract it in
`flatten_for_render` (alongside the existing bbox-diagonal read) and
attach to `RenderOutput`:

```python
@dataclass
class RenderOutput:
    ...
    insunits: int | None = None  # raw $INSUNITS header value
```

The preprocess worker (which already consumes `RenderOutput.bbox`,
`background`, etc.) picks `insunits` off and writes it to the DB row.

### 3. Heuristic — keep it conservative, label honestly

We deliberately don't try to be clever. Two thresholds:

```python
WARN_DIAGONAL_PLAIN = 1000  # anything bigger is suspect for packaging
WARN_DIAGONAL_WITH_UNITLESS = 100  # unitless + over-100 = strong signal
```

| insunits | diagonal | warning |
|---|---|---|
| any | ≤ 100        | `null` |
| 4 (mm) / 5 (cm) / 6 (m) | 100–1000 | `null` (declared unit, plausible scale) |
| 4 / 5 / 6 | > 1000      | `"suspect_scale"` |
| 0 (unitless) | > 100   | `"suspect_scale"` |
| 0 (unitless) | ≤ 100   | `"unitless"` (mild — just missing the declaration) |
| other / null | > 1000  | `"suspect_scale"` |
| other / null | otherwise | `null` |

The warning string is computed server-side (in `FileRecord.to_dict`
or a sibling helper) so the frontend can render directly without
re-implementing the rule. Hover-tooltip text comes from the same
function: `"INSUNITS=0, bbox diagonal=42619.3 — looks like a 1000×
scale issue"`.

### 4. Badge UX

In `dashboard.js:slotCell`, after the status line, append:

```js
if (f.unit_scale_warning) {
  const badge = document.createElement("span");
  badge.className = "warn-badge";
  badge.textContent = "⚠ unit";
  badge.title = f.unit_scale_warning_detail;
  cell.querySelector(".slot-status").appendChild(badge);
}
```

CSS: `color: #ffb84d`; same yellow as the `preprocessing` status pill
so the user reads it as "needs your attention" rather than red-error.

Hovering shows the actionable detail. Click is a no-op for now — if
later we add a "Mark as OK" dismissal or a "fix this for me" option,
this is where it'd hook in.

### 5. Migration plan

- New rows: `insunits` populated during preprocess.
- Legacy rows: `insunits IS NULL` ⇒ heuristic returns `null`
  warning ⇒ no badge. To force a warning on a legacy file the user
  reuses the existing "click library dropdown to retrigger preprocess"
  workflow (same mechanism as the `optimize-bga-render` /
  `adaptive-curve-flattening` rollouts). No data migration needed.

## Risks / Trade-offs

- **[Risk] False positives on legitimately large designs**: a 2 m
  PCB panel would correctly have diagonal > 1000 mm. Showing it as
  "suspect" would be wrong.
  → Mitigation: the warning text says "looks suspect for a packaging
  file" rather than asserting wrongness. The user can ignore it.
  Future enhancement: a per-library "expected diagonal range" setting.
- **[Risk] False negatives**: a file with `INSUNITS = 4` (mm) but
  coordinates actually in metres would have diagonal ~ 0.3 mm and
  pass under the radar (looks like a tiny design).
  → Mitigation: this is the inverse mistake and is much rarer in
  practice (designers don't usually scale *down*). Out of scope.
- **[Trade-off] Computing the warning on every `to_dict()` call**:
  recomputing per call has a few µs cost. Trivial; cleaner than
  caching.

## Migration Plan

1. Add the column via the same in-place ALTER pattern already used
   in `FileStore.__init__`.
2. Extend `FileRecord` + `to_dict` to surface the warning fields.
3. Update the preprocess worker to write `insunits`.
4. Ship the dashboard badge.
5. Rollback: revert the commit; the new column stays in the DB
   (SQLite doesn't drop columns easily) but no code reads it. No
   data corruption risk.

## Open Questions

- Should the viewer status line also surface the warning (it has
  more screen real estate)? Probably yes as a small follow-up —
  defer to a future change so this one stays small.
- Do we want to suppress the badge once the user has explicitly
  acknowledged it? Could add a `dismissed_warnings` column later;
  not worth the complexity now.
