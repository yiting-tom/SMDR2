# role-based-ui Specification

## Purpose
TBD - created by archiving change add-role-based-ui-gating. Update Purpose after archive.
## Requirements
### Requirement: UI never offers a write the server would reject for the role

The frontend SHALL gate write affordances by the caller's effective role on
the relevant product (`viewer < editor < admin`), so that no control which
the server's `editor_guard` / `admin_guard` would reject for that role is
presented as available. Backend enforcement remains the authoritative
boundary; this gate is advisory and MUST keep its 403/423 fallback handling.

#### Scenario: Viewer-role write controls are not actionable
- **WHEN** a user whose effective role on a product is `viewer` views any
  surface (dashboard card or viewer page) for that product
- **THEN** every write affordance (upload / replace / add / delete file,
  layer·view pick, 開始編輯, 畫押, 解除畫押, 新增版本, Rule Check / Upload
  Rule JSON, Save Match, 範本 manage, template create) for that product is
  hidden
- **AND** no such control is presented in a clickable state

#### Scenario: 403 fallback is retained for stale roles
- **WHEN** the gate shows a control because the cached role is stale (e.g. a
  grant was revoked after load) and the user clicks it
- **THEN** the server still returns 403/423 and the existing error handling
  surfaces the rejection

### Requirement: Dashboard read-only experience for viewer role

For a product whose effective role is `viewer`, the dashboard SHALL render a
clean read-only card: write affordances omitted, read affordances retained,
and an empty file slot shown as a non-interactive read-only placeholder
rather than an upload target.

#### Scenario: Viewer card keeps read affordances
- **WHEN** a `viewer`-role product card renders
- **THEN** 開啟 viewer, 查看結果 (when available), 比較版本, and the version
  switcher remain available
- **AND** Delete product, 開始編輯, 畫押 / 解除畫押, 新增版本, Rule Check, and
  all file-slot upload/replace/add/delete controls are not rendered

#### Scenario: Empty slot is read-only for viewer
- **WHEN** a `viewer`-role product has an empty role slot
- **THEN** the slot shows a read-only placeholder (e.g. "（唯讀)") and does
  not respond to click or drag-and-drop upload

### Requirement: Viewer page read-only experience for viewer role

For a file whose product effective role is `viewer`, the viewer page SHALL
hide the write tools while keeping inspection tools, without altering canvas
rendering or interaction.

#### Scenario: Viewer toolbar hides write tools
- **WHEN** the viewer opens a file on a `viewer`-role product
- **THEN** 開始編輯 (edit lock), Save Match, and 範本 (library manage) are
  hidden
- **AND** Scan All, Measure, Layers, Rules, class inspection, and pan/zoom
  remain available

### Requirement: Admin-only controls gated to admin role

Controls the server restricts to `admin` SHALL be presented only when the
effective role is `admin`.

#### Scenario: Unsign hidden from editor
- **WHEN** an `editor`-role user views a signed-off version
- **THEN** the 解除畫押 (unsign) control is not shown
- **WHEN** an `admin`-role user views the same signed-off version
- **THEN** 解除畫押 is shown

### Requirement: Editor and admin retain full affordances

Editor- and admin-role users SHALL retain the full set of write affordances
they are entitled to (including bypass-admin dev mode), with no behavioral
change from before this capability.

#### Scenario: Editor sees write controls
- **WHEN** an `editor`-role user views a product card and opens its viewer
- **THEN** upload/replace/add/delete, 開始編輯, 畫押, 新增版本, Rule Check,
  and Save Match are all available

#### Scenario: Bypass-admin dev mode is unaffected
- **WHEN** the app runs in default bypass mode (effective role resolves to
  `admin`)
- **THEN** all write affordances are shown exactly as before this change

### Requirement: Read-only state is signposted

The UI SHALL show a small, consistent read-only indicator on any surface
gated to read-only because the effective role is `viewer`, explaining the
absence of edit controls.

#### Scenario: Read-only chip on dashboard card
- **WHEN** a `viewer`-role product card renders
- **THEN** a "唯讀" indicator appears in the card header

#### Scenario: Read-only chip in viewer header
- **WHEN** the viewer opens a file on a `viewer`-role product
- **THEN** a "唯讀" indicator appears in the viewer header

