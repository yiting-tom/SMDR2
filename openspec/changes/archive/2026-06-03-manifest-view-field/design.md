## Context

`app/drc_bundle.py:_file_entry(rec)` builds each manifest file entry from a
`FileRecord`, which already carries the three side-region rectangles:

- `top_view_rect: dict | None`
- `bottom_view_rect: dict | None`
- `side_view_rect: dict | None`

Each is a `{x0,y0,x1,y1}` dict when the operator has painted that view's region
in the viewer, or `None` when unset. `split_matches_by_side` uses exactly these
to assign a match's `<view>_view.` key prefix, so the set of views a DXF has
matches in is always a subset of the views whose rect is set. This change
surfaces the operator's view declaration directly.

## Goals / Non-Goals

**Goals:**
- Emit a per-file `view` array of the views the DXF carries, ordered
  top → bottom → side, values `"top"` / `"bottom"` / `"side"`.
- Reuse the rects already on `FileRecord`; no new persistence or plumbing.

**Non-Goals:**
- Changing how side regions are painted, stored, or used for the view split.
- Emitting the rect geometry itself — only the presence of each view.
- Deriving `view` from Match JSON content (a strict subset; see D1).

## Decisions

**D1 — Source of truth: the side-region rects, not Match JSON content.**
`view` lists a view iff its rect is set on the file. This is the operator's
declaration of which views the sheet contains — the question the field answers
("which views does this DXF have"). *Alternative (derive from the views that
actually appear in the Match JSON key prefixes)* rejected: that is a strict
subset (a view with a rect but no matches would be dropped), and it answers a
different question ("which views produced matches").

**D2 — Values `"top"` / `"bottom"` / `"side"`, canonical order.**
The field strips the `_view` suffix the internal prefixes carry, per the
requested vocabulary. Order is fixed top → bottom → side regardless of the order
the operator painted the regions, so the output is deterministic. The schema
documents that these correspond to the Match JSON prefixes `top_view` /
`bottom_view` / `side_view`.

**D3 — Always present; empty array allowed; version bump.**
`view` is added to `file_entry.required` and is always emitted; a file with no
side regions set yields `[]`. The schema types it as an array of the three enum
strings with `uniqueItems: true`. `bundle_version` bumps `1.3.0` → `1.4.0`
(additive minor, consistent with the `customer` and unit-field bumps).

**D4 — No new plumbing.** `_views(rec)` reads the three rects already on the
`FileRecord` that `_file_entry` receives; presence is rect truthiness (matching
`FileRecord.to_dict`'s `if self.<...>_rect` convention).

## Risks / Trade-offs

- **`view` vs Match JSON prefix suffix mismatch** (`"top"` vs `top_view`) → A
  consumer joining the two must map `<v>` ↔ `<v>_view`. Documented in the field
  description; the mapping is mechanical.
- **A rect set with zero matches still lists the view** → Intended: the field
  reports the declared views, not the populated ones (D1).
- **`additionalProperties: false` + new required field** → A consumer validating
  against the old (1.3.0) schema would reject the new key; the version bump is
  the signal. Major version unchanged.

## Migration Plan

Additive; effective on the next bundle export. No data migration.
`bundle_version` moves to `1.4.0`. Rollback = revert (manifests return to 1.3.0
without the `view` key).

## Open Questions

None. (`view` derives from the side-region rects; order top → bottom → side;
empty array when no regions are set.)
