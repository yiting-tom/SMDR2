## Context

Three independent pressures converged on the class taxonomy:

1. **Spec drift.** The `template-library` spec still describes a 9-class
   lowercase seed list (`smd, fiducial_mark, …`). The implementation
   long ago grew to 14 CamelCase classes (`SMD-2T, FiducialMark,
   Side, …`) and the `design-rule-checking` scenarios already
   reference snake_case prefixes (`substrate.*`) — but `save_match_json`
   was still writing CamelCase keys, so the rule-checker matched
   nothing once side regions were drawn.
2. **UX request.** The engineer wants the toolbar in a deliberate
   sequence (Substrate first, fiducials in the middle, SMDs last),
   fiducials separated by shape (圓形 vs. 十字), and the `Side` class
   removed because it never gets used in practice.
3. **Downstream identifier hygiene.** Exports / spreadsheet adapters /
   shell tooling all dislike keys with `-` (`SMD-2T`) and mixed case.
   A stable snake_case match-JSON key removes a class of escaping
   bugs without affecting the user-facing labels.

The implementation has already landed (proposal authored retroactively
to keep the spec record honest).

## Goals / Non-Goals

**Goals:**
- Make the seed list, ordering, and snake_case key mapping
  authoritative in one place (`app/library.py`).
- Migrate every existing DB to the new state on boot, idempotently,
  without manual SQL.
- Keep the viewer / API surface visually identical — display labels
  stay CamelCase, only the persisted `data/match/*.json` keys change.
- Split `FiducialMark` into `FiducialCircle` + `FiducialCross` so the
  engineer can frame-select each fiducial shape into the right bucket
  going forward.

**Non-Goals:**
- Auto-classifying existing `FiducialMark` templates as Circle vs.
  Cross. The committed point sets carry no shape metadata that would
  let us do this reliably; the operator deletes them and re-frames.
- Fixing the side-prefixed key lookup in `rule_check.py`'s helpers
  (`_first_match_handles` etc.) — that's a pre-existing bug whose
  scope is orthogonal to this rename and is left to a follow-up.
- Renaming `app/static/canvas.js`'s `COLLAPSED_TOOLBAR_CLASSES` set;
  it already lists exactly `{SMD-3T, SMD-8T, SMD-14T}`, which matches
  the user's intended "fold" group.

## Decisions

### Two stable identifiers per class, not one

We keep CamelCase display IDs (`Substrate`, `BGABall`) **and** add a
parallel snake_case match-JSON key (`substrate`, `bga_ball`) via a
`CLASS_JSON_KEY: dict[str, str]` map.

Alternatives considered:
- *Rename everywhere to snake_case.* Would force the viewer to
  expose `bga_ball` in the toolbar, breaking the engineer's mental
  model and triggering a much wider blast radius (DB rename, color
  map, hotkey labels, tests, screenshots).
- *Snake_case only in the rule-checker.* Rejected — would still leak
  CamelCase keys into persisted `data/match/*.json` files that
  downstream exporters / spreadsheet adapters consume.

Two-name policy wins because the cost is one ~20-line dict and the
only place that has to know about both is the match-JSON serializer.

### Hard-delete deprecated classes and their templates on boot

The `_migrate()` pass purges `FiducialMark` + `Side` rows from
`classes` and every template filed under them. The operator
confirmed they want the legacy fiducial template gone rather than
migrated to `FiducialCircle`.

Alternatives considered:
- *Leave deprecated rows in place and just stop seeding them.* The
  toolbar would keep showing stale buckets indefinitely.
- *Auto-move FiducialMark → FiducialCircle.* Would assume every
  legacy fiducial is a circle, which isn't necessarily true.

Hard-delete is idempotent (the second boot finds no rows to delete)
and matches the operator's "整筆刪掉" instruction.

### Re-rank every library's classes on boot

`Library.add_class` uses `MAX(rank) + 1`, so newly-added defaults
(`FiducialCircle`, `FiducialCross`) would naturally land at the end
of the toolbar. To slot them at positions 7/8 instead, the migration
re-numbers `rank` for every library in `DEFAULT_CLASSES` order, with
user-added custom classes pushed to the tail preserving their
relative order. This makes `DEFAULT_CLASSES` the single source of
truth for toolbar order, which simplifies future reorderings (edit
the list, ship, every DB re-syncs on next boot).

### Match JSON files are dropped, not rewritten

Existing `data/match/*.json` files are deleted and their owning
files' `match_saved` flag is reset to 0. Operator confirmed they're
fine re-saving from the viewer.

Alternatives considered:
- *Write a one-shot rewriter that maps old keys → new.* Adds a
  throwaway script that needs to know both the snake_case map AND
  the side-prefix rules. Not worth it given the 4 files in flight.

## Risks / Trade-offs

- **[Risk] Pre-existing `rule_check.py` helpers don't match side-
  prefixed keys.** `key.startswith(f"{class_prefix}.")` never matches
  `top_view.substrate.0` against prefix `substrate`. This bug existed
  before this change (when keys were `top_view.Substrate.0`), so
  switching to snake_case doesn't make it worse. → Mitigation: file
  a follow-up; the rule-check tests still pass because their fixture
  keys are unprefixed.
- **[Risk] Hard-deleting `FiducialMark` templates is destructive.**
  → Mitigation: operator explicitly chose this path; only 1 such
  template existed in the dev DB; the legacy class rename history
  already establishes precedent for hard-cleanup migrations.
- **[Trade-off] Custom user classes skip the snake_case map.** A
  user-added class named `My-Class` would land in match-JSON under
  the key `My-Class.0` (with a `-`), which downstream tooling may
  still dislike. → Acceptable: the table is the canonical set; users
  willing to add custom names accept the consequences. We can revisit
  with an "auto-snakeify" helper if it becomes an issue.

## Migration Plan

The migration is fully automatic on Store boot — no operator action
required beyond restarting the server. Sequence on every boot:

1. Run existing legacy-rename / schema-rebuild passes (idempotent).
2. **DELETE** templates whose `class_name ∈ DEPRECATED_CLASSES`.
3. **DELETE** `classes` rows whose `name ∈ DEPRECATED_CLASSES`.
4. **INSERT OR IGNORE** every `DEFAULT_CLASSES` entry into every
   existing library (so newly-added defaults exist before re-rank).
5. **UPDATE** `classes.rank` per library: sort by
   `(DEFAULT_CLASSES.index(name) if known else len(DEFAULT_CLASSES),
   old_rank, created_at)` and re-number from 0.

Manual one-shot cleanup performed alongside the code change:
- `rm data/match/*.json`
- `UPDATE files SET match_saved=0 WHERE match_saved=1`

Rollback: revert the commit; the old code still tolerates
`FiducialCircle` / `FiducialCross` rows existing in the DB (they
just show up as extra classes the engineer can choose to ignore).
The snake_case match-JSON key change is a one-way break for any
existing `data/match/*.json` consumer — but those files are
recreated on demand from the viewer.
