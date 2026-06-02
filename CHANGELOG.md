# Changelog

All notable changes to SMDR2. Entries are grouped by area; within each group,
newer work is listed first. Source of truth is the git history on `main` plus
the OpenSpec changes under `openspec/changes/` (archived = formally closed;
active = code merged into `main`, pending manual verification + `/opsx:archive`).

Generated 2026-06-02. Covers 65 OpenSpec changes — 40 archived (formally closed)
and 25 active (code merged into `main`, pending manual verification + archival).
Every feature that has been built is on `main`.

## Status legend

- **Shipped** — code is on `main`.
- **Pending archive** — code is on `main`; only `[USER]` manual verification +
  `/opsx:archive` (and spec-sync) remain. The 25 active changes are in this state.

---

## Matching / detection engine

- **rule-filter-prefix-category** (2026-06-02) — rule sidebar category filter now
  derives categories from the `<prefix>` of the `<prefix>-<ruleId>` rule name
  (split on first `-`), instead of requiring a trailing numeric id.
- **matcher-winding-invariant-resample** — phase/winding-invariant chamfer
  resampling; two identical substrate outlines stored with opposite winding /
  different start vertex now match instead of reporting a `reason="shape"` near-miss.
- **large-outline-classes-default-signature / substrate-lid-signature-default** —
  `Substrate`, `LidOuter`, `LidInner` default to signature matching (winding-insensitive
  for big rigid outlines).
- **match-multi-isotropic-seed-fallback** — `_match_multi` adds an isotropic seed
  fallback when per-candidate recovery fails.
- **multi-match-rigid-fingerprint-bucket** — rigid fingerprint bucketing for
  multi-entity scans (replaces the old "similar shapes within" design).
- **strengthen-match-signature-prefilter** — two cheap rotation-invariant pre-filter
  gates reject false positives (e.g. 1.5×1.5 square vs 2×1 rect) before alignment.
- **improve-polyline-density-invariance** — single-entity polyline matching tolerant
  to differing vertex density on the same shape.
- **add-per-class-match-strategy** — per-class choice of match strategy (chamfer vs
  signature).
- `from_points` uses absolute tolerance for closed-polyline dedup (`e90b702`).
- ±1 bucket window absorbs banker's-rounding fence-post drift (`6ca8c0a`);
  **circle-fast-path-absorbs-bucket-edge-drift** extends this to the circle fast path.

## Circle / primitive detection & rendering

- **add-circle-scan-fast-path** — analytical single-CIRCLE scan fast path (hot path
  on BGA/SMD DXFs).
- **add-highlight-zoom-lod** — zoom level-of-detail for highlight rendering.
- **improve-circle-fit-least-squares** — least-squares circle fit in
  `_detect_circle_subpath`.
- **extend-circle-promotion-to-polylines** — promote polyline-authored round shapes
  (BGA balls etc.) to circle primitives.
- **add-filled-circle-fast-render** — filled-circle fast render path.
- **optimize-bga-render** — render path tuned for hundreds of thousands of entities.

## Class taxonomy, scope & arbitration

- **remove-density-arbitration-subsystem** — removes the now-dead arbitration module,
  registry, call sites, and tests (no behaviour change; registry was already empty).
- **disambiguate-bga-fiducial-by-view** — split `BGABall` vs `FiducialCircle` by
  mutually-exclusive view constraints instead of the neighbour-density heuristic.
- **arbitration-skip-when-single-class-pool** — short-circuit when the pool resolves
  to one class (fixes isolated circles re-emitted as `fiducial_circle.0`).
- **arbitration-fallback-requires-default-evidence** — population fallback no longer
  fires when `default_class` has no matched instance in the pool.
- **arbitration-context-wrappers** — context wrappers around the arbitration step
  shared by the three pipeline stages.
- **add-neighbor-arbitration-between-classes** — neighbour-density arbitration between
  geometrically identical classes (later superseded by view constraints).
- **scan-all-applies-arbitration** — viewer scan-all overlay applies arbitration so
  toolbar counts aren't over-counted; **scan-all-incremental-on-commit** updates the
  overlay incrementally on commit.
- **add-class-view-constraints** — classes constrained to the views they can appear in.
- **split-class-scope-library-vs-product** — class templates scoped at library vs
  product tiers.
- **rework-class-taxonomy** — cleanup of the DEFAULT class taxonomy drift.
- **add-protrusion-class**, **add-fiducial-square-class** (+ FiducialSquare),
  **add-c4-ball-class** — new classes.
- **split-ring-into-ring-or-lid**, **drop-ring-lid-exclusion** — RING/LID roles; a
  product may hold both.

## DXF pipeline / parsing

- **drop-hatch-entities** — HATCH (decorative solder-mask noise) dropped from
  selection/matching; TEXT/MTEXT/DIMENSION already excluded (`396f1cb`, `e5c90a8`).
- **dxf-recover-fallback** — strict-first parse with recover-fallback + audit notes
  for DXFs that open in AutoCAD but fail unrecoverably elsewhere.
- **add-user-unit-override** — manual unit override on top of auto-rescale.
- **auto-normalize-unit-suspect-dxf** — auto-rescale DXFs with a unit-scale anomaly
  (e.g. 1000× inflated bbox).
- **flag-suspect-unit-scale** — flag `$INSUNITS = 0` / suspect-scale DXFs.
- **adaptive-curve-flattening** — adaptive flattening of curves on import.

## Viewer & UI

- **viewer-grey-absent-class-buttons** — grey out class-toolbar buttons for classes
  with no match in the current image.
- **rule-sidebar-filter-search** — rule sidebar fuzzy search + category + pass/fail/all
  filters (client-side, AND-combined).
- **measure-distance-tool** — measure tool (`D`): chained DIST with OSNAP, ortho, and
  circle CEN/QUA snapping; pure helpers extracted to `measure_core.js` + `node:test`.
- **viewer-hide-decorative-primitives** — hide decorative primitives on the canvas.
- **viewer-text-only-sub-rule-display** — render text-only rule sub-rules as inert text.
- **add-layer-preview-filter** — two-phase preprocess + viewer layer-visibility toggle.
- **add-side-view-region** / **mark-side-regions** — side-view region alongside
  frontside/bottomside; side-prefixed match-JSON keys; "top view"/"bottom view" labels.
- **fold-dashboard-by-customer** — dashboard folds product cards by customer.
- **add-multi-dxf-role-switcher** / **multi-dxf-per-role** — multiple DXFs per role
  with a switcher; **expose-single-file-slot-delete** — delete a single file slot.
- **move-readouts-to-status-bar**, **remove-viewer-title-link** — viewer header cleanup.
- Viewer rule sidebar: foldable per-rule, clickable sub-rules (`0c1134e`).

## Rules / DRC

- **rule-json-accept-text-only-sub-rules** — envelope accepts sub-rules with no
  locatable geometry (text-only annotations).
- **rule-check-multi-to** — sub-rule `to` accepts `list[str]` for fan targets.
- **rule-check-affordance** — rule-check modal affordance on the dashboard.
- **externalize-rule-check** — rule logic owned by an external team's Python module;
  SMDR2 calls into it with the bundle dir. Internal mock Rule1/2/3 retired.
- **rule-check-as-background-job** / **async-save-match-json** — rule check + Save
  Match run on the worker pool, return `202 + job_id`.
- **add-drc-bundle-export** — export the DRC handoff bundle (DXFs + Match JSON +
  manifest) as a zip; **add-customer-fields-to-drc-manifest** +
  **document-multi-dxf-rule-bundle** flesh out the manifest/contract.
- **dedup-templates-on-commit** — dedup library templates by canonical signature on
  commit (avoids redundant scan-all cost).
- Rule-check geometry: shortest from→to distance, vertex-to-edge minimum, optional
  `from_edge`/`to_edge` bbox anchors, v2 per-sub-rule highlight + annotation line
  (`61b81a8`, `42078db`, `0ac5be9`, `130574c`).

## Dev tooling, infra & docs

- **observability-launch-hardening** — structured logging, upload size limit, and
  launch-readiness hardening for the internal go-live.
- **add-dashboard-dev-mode** — dashboard dev mode for auditing the DRC pipeline.
- **dev-skip-layer-picker** — dev-mode `skip_layer_pick` bypasses the Phase 1 layer picker.
- **expose-dev-parameter-overrides** — tune matcher/DXF tolerances at runtime instead
  of editing source + restarting.
- **add-rule skill** — agent-neutral guide for adding DRC rules.
- **initial-build** — FastAPI app scaffold: dashboard, viewer, per-file endpoints,
  pattern-matching engine, SQLite-backed library, background job queue, DXF flatten
  pipeline, mock rule check, test suite, sample DXF.
