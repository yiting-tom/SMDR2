# viewer-ui Specification (delta)

## ADDED Requirements

### Requirement: Version diff modal

The product card SHALL offer a "比較版本" action opening a modal with
two version pickers (defaulting to the previous version → the currently
selected version) and three diff sections: templates added/removed
(rendered as thumbnails with class labels, mirroring the Templates
modal's rendering), per-class match-config changes (from → to), and
binding changes per role (file name added/removed/state-changed).
An empty diff SHALL state explicitly that the versions are identical.
The action SHALL be available on signed-off versions (read-only).

#### Scenario: Diff modal shows an added template
- **WHEN** the user compares `v1` → `v2` where `v2` committed one extra template
- **THEN** the modal's "新增範本" section renders one thumbnail with its class name

#### Scenario: Identical versions state it plainly
- **WHEN** the two selected versions have no differences
- **THEN** the modal shows a "兩版本內容相同" empty-state message
