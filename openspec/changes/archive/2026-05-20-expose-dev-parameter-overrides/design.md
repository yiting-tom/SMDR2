## Context

`app/matching.py` and `app/dxf.py` define their tolerances and thresholds as module-level constants (`SCALE_MIN`, `TOLERANCE_ABS`, `VERTEX_COUNT_RATIO`, `BASE_TOLERANCE`, `CIRCLE_MIN_VERTS`, …). Today, callers inside both modules reference these constants by bare name — Python resolves bare names to the module's `__dict__` at call time, so reassigning `matching.TOLERANCE_ABS = 0.02` at runtime is picked up by subsequent calls without restart.

The Dashboard already has a Developer Mode toggle (`#dev-mode-toggle`, persisted under `localStorage["smdr2.dashboard.devMode"]`) that mounts dev-only download affordances. The new parameter modal slots into the same dev-mode surface, behind a sibling gear button.

The product is single-user, single-process, dev-tool oriented. There is no auth, no concurrency model for dev-overrides, and no audit trail required.

## Goals / Non-Goals

**Goals:**
- Closing the parameter-tuning loop to seconds: change in modal → next match/preprocess call uses the new value, no restart.
- Keep prod behaviour identical when Dev Mode is off and no overrides have been applied since startup.
- Make the in-memory and not-thread-safe nature of overrides obvious in the modal copy and the spec.
- Allow DXF-side changes to take effect on already-uploaded files via an explicit "Re-preprocess all files" action.

**Non-Goals:**
- Persisting overrides across restarts. Restart is the reset path.
- Per-request, per-product, or per-user overrides. One process-wide set of values.
- Hot-reloading any constants outside the curated allow-list.
- Concurrency safety: dev usage only; explicitly documented.
- Exposing or tuning rule-check thresholds (out of scope per the user's scoping; can be a follow-up change).

## Decisions

### Decision 1: Mutate module attributes in-place (vs. plumb params through call signatures)

The matching and dxf modules have constants referenced from many call sites — some via default-argument injection, but most via bare-name lookup inside helper functions. Threading every tunable through every function would be a wide refactor for a dev-only tool.

Instead, the override store calls `setattr(matching, name, value)` / `setattr(dxf, name, value)` directly. Because the consuming code already uses bare names, this works at zero refactor cost.

**Alternative considered**: introduce a `Settings` dataclass and refactor every caller to take it. Rejected — too much churn for a feature whose audience is one developer at a time. We can do that refactor independently if it ever becomes load-bearing.

**Constraint introduced**: a small allow-list in the new `app/dev_overrides.py` defines which `(module, attribute, type, min, max)` tuples are writeable. POSTs to fields outside the allow-list 400 — this keeps the endpoint from becoming an arbitrary-mutation backdoor.

### Decision 2: In-memory only, no persistence

The user explicitly opted out of `dev_overrides.json`. The override store lives in `app/dev_overrides.py` as a module-level dict initialised from the compiled defaults at import time. Restarting the server returns to defaults.

**Why this is the right default**: it prevents stale dev values from leaking into a teammate's run; "restart to reset" is a familiar dev-tool primitive; no migrations or schema concerns.

### Decision 3: Backend is source of truth; `localStorage` only mirrors the *view*

The browser sends `POST /api/dev/settings` with the full set of values, the server applies them and echoes the post-apply state back. The frontend writes that echo to `localStorage["smdr2.dashboard.devOverrides"]` so the modal restores immediately on next open, but on modal open the frontend always re-syncs via `GET /api/dev/settings` first.

**Why**: a teammate restarting the server while another tab is open mustn't be lulled by stale `localStorage` into thinking their overrides are still applied. The GET on modal open is cheap and definitive.

### Decision 4: "Re-preprocess all files" as an opt-in dev action

`BASE_TOLERANCE`, `CURVE_FLATTENING_DISTANCE`, `CIRCLE_MIN_VERTS`, and `CIRCLE_RADIAL_TOL` are consumed at upload-preprocess time. By the time the user is in Dev Mode, files have baked primitives — changing the DXF constants alone does nothing visible.

The modal exposes an explicit **Re-preprocess all files** button that POSTs `/api/dev/reprocess-all`. The endpoint enqueues a job that, for every file in storage, re-runs the existing preprocess pipeline against the cached source DXF (which is already on disk) using the now-current module constants. The job reuses the existing `app/jobs.py` machinery so progress is reflected in the dashboard status line.

**Alternatives considered**:
- Auto-reprocess on Save → rejected as too easy to misfire; long-running and destructive (wipes prior match JSONs because primitives change).
- Only-affects-new-uploads → rejected as a footgun for the tuning loop the user is trying to close.

**Trade-off the user must accept**: re-preprocessing invalidates any saved Match JSON whose handle set or vertex counts change after re-extraction. The modal copy must warn explicitly before running.

### Decision 5: Modal scope — matching + dxf only

Per the user's scoping the rule-check thresholds (`SUBSTRATE_TO_SMD_MIN_DIST`, `SMD_TO_SUBSTRATE_MAX_DIST`) and the runtime-only `N_JOBS` are not included in this change. They can be added later by extending the allow-list and the modal sections.

## Risks / Trade-offs

- **Race with in-flight jobs** → If the user changes overrides while a match or preprocess job is running, the job will read whatever module attrs are live when each helper runs, which can yield internally inconsistent results. Mitigation: modal copy explicitly says "wait for jobs to settle"; the endpoint does not lock — single-user assumption holds. Documented in the spec as a known limitation.
- **Allow-list drift** → If matching adds a new constant that should be tunable, two places need updating (module + allow-list). Mitigation: keep the allow-list adjacent to the constants in code review; add a comment on each constant pointing at the allow-list location.
- **Re-preprocess wipes match work** → Re-running preprocessing changes the primitive payload; saved Match JSONs become stale. Mitigation: modal confirmation dialog enumerates the consequence ("Match JSONs for already-confirmed files will need to be re-saved"). No code-level enforcement; this is a dev tool.
- **`localStorage` mirror diverges from backend after a restart** → User opens modal in stale tab, sees old override values, hits Save — server happily applies them again, which is fine. The risk is only confusion. Mitigation: GET on modal open establishes ground truth.
- **Discoverability of the dev modal** → Hidden behind two toggles (Dev Mode ON, then the gear). That is intentional; non-dev users should never see it. The gear has `aria-hidden` semantics handled by `display:none` when Dev Mode is OFF.

## Migration Plan

No data migration. Deploy is a single deploy; rollback is a single deploy. Existing files and saved match JSONs are untouched until a user explicitly triggers re-preprocess.

## Open Questions

None — scope was confirmed with the user before drafting (modal placement, scope of params, in-memory persistence, re-preprocess as opt-in button).
