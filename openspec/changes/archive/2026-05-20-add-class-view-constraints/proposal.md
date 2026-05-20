## Why

Packaging physics says some classes can only show up in specific views:
**C4 bumps** sit between the chip and substrate and are visible only from
the chip's top-down perspective (`top_view`); **BGA balls** sit on the
package bottom and are visible from the underside (`bottom_view`) or
in cross-section (`side_view`), never from the top. Today the matcher
and Scan All overlay happily produce both `top_view.bga_ball.*` and
`bottom_view.c4_ball.*` keys whenever geometry coincides — physically
impossible matches that pollute downstream rule-check inputs and
confuse the engineer reading the overlay.

## What Changes

- Add a new data-driven registry `library.CLASS_VIEW_CONSTRAINTS:
  dict[str, frozenset[str]]` that maps a display ID to its **allowed
  set of view prefixes**. Seed it with:
  - `C4Ball → {"top_view"}`
  - `BGABall → {"bottom_view", "side_view"}`
  - Other classes: unconstrained (key absent → all views allowed).
- `POST /api/files/{file_id}/match-json` SHALL drop every constrained
  class's match instance that lands in a disallowed view OR lands
  unassigned (no view rect covers it). Side-counts SHALL reflect only
  surviving matches.
- As a micro-optimisation, the match-JSON endpoint SHALL skip the
  `find_matches_from_pointsets` call entirely when none of the
  constrained class's allowed view rectangles is set on the file
  (e.g., skip C4Ball templates on a file with no `top_view_rect`).
- The viewer's **Scan All overlay** SHALL apply the same filter when
  rendering — instances of a constrained class whose handles' bbox
  center is outside every allowed view rectangle SHALL NOT be drawn,
  and the per-class status counts SHALL reflect the filtered totals.
- The pre-match worker (`/api/files/{file_id}/prematch` cache) is
  **not** changed — it runs at upload time before view rectangles are
  drawn, so it stays view-agnostic. The filter lives at the read /
  render layer instead.

## Capabilities

### New Capabilities
<!-- none — this extends behavior across three existing capabilities -->

### Modified Capabilities
- `template-library`: gains a new requirement specifying
  `CLASS_VIEW_CONSTRAINTS` as a class-attribute registry alongside
  `CLASS_JSON_KEY`, with the C4Ball and BGABall seed entries.
- `dxf-pipeline`: the *Side-prefixed match JSON keys* requirement
  gains a constrained-class filter step — disallowed-view and
  unassigned instances of `C4Ball` / `BGABall` (and any future entry)
  SHALL be dropped from the saved JSON, not merely keyed differently.
- `viewer-ui`: the *Scan-all overlay with per-class colours*
  requirement gains a matching filter so the overlay and the
  match-JSON view stay consistent.

## Impact

- **Code**: `app/library.py` (new `CLASS_VIEW_CONSTRAINTS`),
  `app/side_regions.py` (filter aware of class), `app/main.py`
  (match-JSON endpoint: filter + skip-when-impossible),
  `app/static/canvas.js` (Scan All render path; per-class counters).
- **Spec**: deltas for `template-library`, `dxf-pipeline`, `viewer-ui`.
- **Data migration**: none. The constraint is applied at read /
  serialise time, not at storage time. Existing `data/match/{file_id}.json`
  files SHALL be invalidated as usual on the next Save Match action;
  there is no retroactive rewrite.
- **Rule check**: `app/rule_check.py` is **not** changed. Today's rules
  already operate on the keys present in match-JSON; once the
  constrained-class violators stop being emitted, existing rules
  automatically read filtered data.
- **No breaking changes** to API shape: response of
  `POST /api/files/{file_id}/match-json` is the same shape, just with
  fewer keys/instances when constrained classes violate their view.
  `total_matches` and `side_counts` SHALL reflect the filtered counts.
