## ADDED Requirements

### Requirement: Rule sidebar restores scroll position across navigation

The viewer SHALL persist the rule-check sidebar's scroll position
(`#rule-sidebar-body`) per file and restore it after the sidebar is rebuilt on
load, so a cross-file rule jump returns the operator to their previous place in
the rule list instead of the top. The position SHALL be saved before navigating
away (the cross-file rule jump and on page hide) and SHALL persist in
`sessionStorage` keyed by file id. Restoration SHALL clamp to the rebuilt
sidebar's scrollable height so a shorter rule list does not overscroll. The
`?rule=&idx=` focus flow SHALL be unaffected.

#### Scenario: Returning to a file restores the rule-sidebar scroll
- **WHEN** the operator scrolls the rule sidebar, clicks a sub-rule that
  navigates to another file, then returns to the original file's viewer
- **THEN** the rule sidebar is scrolled back to approximately its previous
  position, not reset to the first rule

#### Scenario: Each file restores its own scroll
- **WHEN** the operator returns to a file whose sidebar scroll was never saved
- **THEN** that file's rule sidebar loads at the top
- **AND** another file's saved scroll position is not applied to it

#### Scenario: Restore clamps to a shorter rule list
- **WHEN** the saved scroll position exceeds the rebuilt sidebar's scrollable
  height
- **THEN** the sidebar scrolls to the bottom of the available content without
  error

#### Scenario: Rule focus still works on load
- **WHEN** the viewer loads with `?rule=&idx=` focus and a saved sidebar scroll
- **THEN** the focused sub-rule is still highlighted and the canvas recenters as
  before
