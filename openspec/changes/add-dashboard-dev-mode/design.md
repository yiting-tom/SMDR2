## Context

The dashboard (`app/templates/dashboard.html` + `app/static/dashboard.js`)
already renders one card per product with a nested list of role-attached
files. Each file row already carries the metadata we need
(`file.id`, `file.match_saved`, role membership through the parent
card), and each product card already exposes `ready_for_rule_check`.
Two HTTP endpoints exist and need no change:

- `GET /api/files/{file_id}/match-json` — returns the saved Match JSON
  as application/json (no `Content-Disposition`).
- `GET /api/products/{product_id}/drc-bundle` — returns the DRC handoff
  zip with `Content-Disposition: attachment; filename=drc-bundle-<pid>.zip`
  already set. Shipped in `add-drc-bundle-export`.

Stakeholders: dashboard users (default — should see no change unless
they toggle dev mode), maintainers debugging product state, the
external DRC team's contact at SMDR2 who wants to fetch the same
bundle the team consumes without reaching for `curl`.

## Goals / Non-Goals

**Goals:**
- One toggle persistent across reloads.
- Two dev-only downloads, both reusing existing endpoints.
- Default-off; non-dev users see the dashboard exactly as before.
- Minimal CSS — match existing button styles.

**Non-Goals:**
- No "developer console" or arbitrary debugging affordances — this
  is just two specific download buttons. Future dev-mode features
  can be added under the same toggle.
- No new backend endpoints, no new auth, no per-button permissions.
- No download-progress UI, no client-side zipping, no batching across
  products.

## Decisions

**Decision 1: One persistent `localStorage` key, not URL param.**
A URL `?dev=1` is ergonomic for one-off links but means losing the
state when a user follows a normal product link. We pick localStorage
because the typical user pattern is "toggle once on my dev machine,
keep it on for the session/week."

*Alternative considered:* `?dev=1` query parameter (or both). Rejected
for keeping state model simple; cost of "I have to toggle on each
browser" is negligible for the dev audience.

**Decision 2: Buttons hidden, not disabled, when dev mode is off.**
The toggle is a *visibility* control — non-dev users get a clean
dashboard. Disabled buttons would clutter every card with grey ghosts
they can't use.

**Decision 3: Per-file "Download Match" hidden when `match_saved=false`.**
A file without saved Match JSON can't actually be downloaded — the
endpoint 404s. Hide the button rather than render it disabled, because
dev users glancing at a row already see the existing match-progress
indicator.

**Decision 4: Per-product "Download All Match" disabled (not hidden) when not ready.**
The bundle endpoint requires `ready_for_rule_check`. Showing a
disabled button with a tooltip explaining why is more discoverable
than hiding it; the existing rule-check button uses the same pattern
(see `dashboard.js:158-163`), so this is consistency, not novelty.

**Decision 5: Client-side download via Blob + temporary `<a download>`.**
Rather than navigating the browser to a JSON endpoint (which would
render JSON inline in a new tab on most browsers), we fetch the
response, wrap it in a Blob, build a transient `<a download="…">`,
click it, and revoke the object URL. Same pattern works for both
endpoints — the bundle endpoint already streams as
`application/zip` with `Content-Disposition`, but routing it through
the same Blob-and-click flow keeps the JS code symmetric and lets us
control the filename uniformly (`match-<file_id>.json`,
`drc-bundle-<product_id>.zip`).

*Alternative considered:* Use `window.location = url` (browser handles
the download). Works only for the bundle endpoint thanks to its
`Content-Disposition` header; the JSON endpoint would render inline.
Mixing two patterns adds confusion; the Blob path is uniform.

**Decision 6: No `dev mode on` indicator beyond the toggle itself.**
The toggle button reflects its own state (e.g. `[Developer Mode: ON]`
vs `[Developer Mode]`). Adding a banner or background tint would be
noise for a UI-affordance toggle that has no security implications.

## Risks / Trade-offs

- **[Risk]** Large products with many files make "Download All Match"
  click feel laggy because the server streams a multi-MB zip.
  → **Mitigation**: existing endpoint behavior; the dev audience can
  tolerate this. No UI spinner needed for v1.
- **[Risk]** A future feature adds a third "dev" affordance; copy-paste
  drift between callsites.
  → **Mitigation**: factor the toggle check + the
  `downloadAsFile(blob, filename)` helper into top-of-file helpers
  in `dashboard.js`; future dev-only features just consume them.
- **[Trade-off]** Hiding `Download Match` when `match_saved=false`
  means a dev user can't tell "is this file ready?" at a glance from
  the dev affordances alone. They still have the existing
  match-progress indicator on the card. Acceptable.

## Migration Plan

No migration — the toggle defaults to OFF and the dashboard renders
exactly as today for any user who never flips it.

## Open Questions

- Should the toggle live in the existing header strip, or as a small
  affordance near the title? **Tentative**: header strip, beside the
  existing "Create Product" / library controls — matches what the
  user described ("最上面").
- Do we want the toggle keyboard-accessible by default? **Tentative**:
  yes, render as a `<button>` (not a `<div>`); existing dashboard
  controls are buttons so this falls out for free.
