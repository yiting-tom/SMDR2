## ADDED Requirements

### Requirement: Scan-all overlay incrementally refreshes on commit

The viewer SHALL refresh the Scan All overlay on a successful
`POST /api/files/{file_id}/commit` so the newly-committed template's
matches become visible in their class colour, and SHALL avoid a
full server-side Scan All re-run in the common case by reusing the
live-preview `matchSet` already populated by the S-key match.

Trigger: the overlay is currently active in the viewer
(`scanAllByHandle !== null` in `app/static/canvas.js`) and the
commit response is HTTP 200.

The viewer's decision tree on a successful commit SHALL be:

1. **If Scan All is not active** (`scanAllByHandle === null`): no
   overlay state to update. The class-count chip on the toolbar still
   increments (as today). No status-line change beyond the existing
   `saved <Class> template (#count)` message.

2. **If Scan All is active AND the committed class is in the
   front-end's `CLASS_ARBITRATION_MEMBERS` set** (today `BGABall`,
   `FiducialCircle`): fall back to a full `runScanAll()` re-run.
   The front-end cannot reproduce the server-side neighbour-density
   arbitration that resolves cross-fire between arbitration-group
   members, so an incremental merge would mis-attribute handles.
   The status-line keeps its `saved <Class> template` message and
   the subsequent `scan-all: running…` status from `runScanAll()`
   takes over once the re-run starts.

3. **If Scan All is active AND the committed class is NOT in
   `CLASS_ARBITRATION_MEMBERS`**: merge incrementally. The handle
   set to merge is the **union of `selection` and `matchSet`** —
   `selection` is always non-empty at this point (the commit
   handler's early-return guard `!selection.size` enforces it), and
   covers the source pattern itself (which the server's
   `find_matches` excludes from its response via the
   `template_handle_set` skip). `matchSet` covers the other
   instances surfaced by the optional S-key live preview. For each
   handle in this union, set
   `scanAllByHandle.set(handle, committedClassName)`, overwriting
   any prior class assignment. Then:
   - recompute `byClass` counts from the updated `scanAllByHandle`;
   - re-apply view constraints via
     `applyViewConstraintsToScanAll(scanAllByHandle, byClass)` so
     any view-disallowed handles (e.g., a `C4Ball` match landing in
     `bottom_view`) are filtered out and excluded from the count;
   - replace `scanAllSummary` with the new
     `{ byClass, total: scanAllByHandle.size }`;
   - append a `· overlay +N <Class>` suffix to the status line where
     `N` is the post-view-constraint count for the new class.

The overlay merge SHALL NOT re-issue any server request other than
the `/api/files/{id}/commit` POST itself (and the optional
`/api/files/{id}/scan-all` GET in the arbitration-fall-back path).

#### Scenario: Non-arbitration commit with Scan All active merges incrementally
- **WHEN** Scan All is active with non-empty `scanAllByHandle`
- **AND** the user commits a new `SMD-2T` template via add-mode after
  pressing S to populate `matchSet`
- **THEN** every handle in `selection ∪ matchSet` SHALL appear in
  `scanAllByHandle` with class `"SMD-2T"`
- **AND** `scanAllSummary.byClass["SMD-2T"]` SHALL equal the number
  of `selection ∪ matchSet` handles that survive view-constraint
  filtering
- **AND** the status-line SHALL include `· overlay +N SMD-2T`
- **AND** no `GET /api/files/{id}/scan-all` request SHALL be issued

#### Scenario: Arbitration-class commit falls back to full Scan All
- **WHEN** Scan All is active
- **AND** the user commits a new `BGABall` or `FiducialCircle`
  template
- **THEN** the viewer SHALL call `runScanAll()` after the commit
  succeeds
- **AND** the incremental-merge code path SHALL NOT execute (i.e.,
  no `· overlay +N` suffix appears for this commit)
- **AND** the status-line SHALL transition through `saved <Class>
  template` then `scan-all: running…` then the final scan-all hit
  total once the re-run completes

#### Scenario: Commit with Scan All inactive does not enable overlay
- **WHEN** Scan All is NOT active (`scanAllByHandle === null`)
- **AND** the user commits a new template
- **THEN** `scanAllByHandle` SHALL remain null after the commit
- **AND** no overlay merge or re-run SHALL fire

#### Scenario: Commit without S-preview still highlights the source pattern
- **WHEN** Scan All is active
- **AND** the user commits a non-arbitration class without first
  pressing S (so `matchSet` is empty)
- **THEN** every handle in `selection` SHALL appear in
  `scanAllByHandle` with the committed class name
- **AND** the `· overlay +N <Class>` suffix SHALL include those
  handles in its count (subject to view-constraint filtering)
- **AND** no Scan All re-run SHALL fire

#### Scenario: Incremental merge re-applies view constraints
- **WHEN** Scan All is active
- **AND** the user commits a `C4Ball` template that matched some
  handles in `top_view` and some in `bottom_view`
- **THEN** only the `top_view` handles SHALL appear in
  `scanAllByHandle` with class `"C4Ball"` (per
  `CLASS_VIEW_CONSTRAINTS["C4Ball"] = ["top_view"]`)
- **AND** the `bottom_view` `C4Ball` matches SHALL be filtered out
  by `applyViewConstraintsToScanAll`
- **AND** the `· overlay +N` count SHALL reflect the
  post-view-constraint total

*Caveat*: `C4Ball` is itself in `CLASS_ARBITRATION_MEMBERS` only if
a future change adds it. Today it is not, so the incremental path
applies. This scenario documents the view-constraint composition,
not arbitration.
