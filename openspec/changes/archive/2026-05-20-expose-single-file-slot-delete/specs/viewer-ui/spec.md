## ADDED Requirements

### Requirement: Dashboard slot per-file action bar

Each file row in a dashboard product card's slot SHALL render a
consistent action bar regardless of whether the slot holds one file
or multiple. The action bar SHALL include, at minimum:

- An **Open** link to the viewer (when the file's status is
  `ready_to_match`) or a **Pick layers** action (when the status is
  `awaiting_layers`).
- A **Layers** button (when the status is not `discovering_layers`
  or `error`) that opens the layer-selection modal for that file.
- A **Replace** button that opens the file picker bound to the same
  `(product, role)` slot, with the current file id passed as
  `replace_file_id` so the upload evicts it before landing the new
  one.
- A **Delete** button (rendered as `✕`) that detaches the file from
  the slot via `DELETE /api/products/{product_id}/files/{file_id}`.
  The Delete action SHALL be available for both single-file and
  multi-DXF slots; the single-file case SHALL NOT hide it. The
  Delete button SHALL prompt for confirmation before issuing the
  request and SHALL refresh the dashboard on success.

The Delete affordance is a **detach**, not a destructive deletion:
the underlying file row remains in `FILE_STORE` (so reuploads of
the same content reuse it via content-addressable storage); only
the `product_id` / `dxf_role` / `dxf_view` binding clears, plus the
cached Match JSON.

The viewer header SHALL NOT carry a Delete affordance; file
management remains scoped to the dashboard.

#### Scenario: Single-file slot exposes Delete
- **WHEN** a product has exactly one DXF under role `SBT`
- **THEN** the SBT slot's action bar SHALL include a Delete button (`✕`) alongside Replace
- **AND** the Delete button SHALL share the styling and confirm-on-click behaviour used by the multi-DXF case

#### Scenario: Delete on a single-file slot detaches the file
- **WHEN** the engineer clicks Delete on the SBT slot's only file and confirms the prompt
- **THEN** `DELETE /api/products/{product_id}/files/{file_id}` is issued
- **AND** on HTTP 204 the dashboard refreshes
- **AND** the SBT slot returns to the empty drop-zone state

#### Scenario: Delete on a multi-DXF slot detaches only the targeted file
- **WHEN** a product has DXFs `A` and `B` under role `BD`
- **AND** the engineer clicks Delete on `A`'s row and confirms
- **THEN** `DELETE /api/products/{product_id}/files/{A.id}` is issued
- **AND** the slot continues to show `B`

#### Scenario: Delete unlocks RING / LID configuration switching
- **WHEN** a product holds one DXF under role `RING` and zero under `LID`
- **AND** the engineer clicks Delete on the RING slot's file and confirms
- **THEN** the RING file is detached and the LID half of the 4th slot becomes enabled
- **AND** the engineer MAY then upload a LID file without rebuilding the product

#### Scenario: Delete confirm dialog cancels cleanly
- **WHEN** the engineer clicks Delete on any file
- **AND** dismisses the confirm dialog
- **THEN** no DELETE request is issued
- **AND** the dashboard state is unchanged

#### Scenario: Viewer header has no Delete control
- **WHEN** the engineer opens any file in the viewer
- **THEN** the viewer header SHALL NOT render a Delete button for the loaded file
- **AND** the only role-related controls in the header SHALL be the per-role sibling-DXF switcher described above
