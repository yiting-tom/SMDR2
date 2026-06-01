## ADDED Requirements

### Requirement: Rule sidebar fuzzy search and filters

The rule sidebar SHALL provide a fuzzy search input and two filter dimensions — category and pass/fail/all status — that narrow the displayed rules client-side without re-fetching. Fuzzy search SHALL match (case-insensitive subsequence) against each rule's name and description text. The category dimension SHALL group rules by the `<category>` prefix of the `<category>-<index>` rule-name format, offering each distinct category plus an "all categories" option. The status dimension SHALL offer pass, fail, and all. Active filters SHALL combine (logical AND).

When any filter is active, the sidebar summary SHALL indicate how many rules are shown; when the active filters match no rules, the sidebar SHALL show a "no rules match" message instead of an empty list. With no search text, no category selected, and status "all", every rule SHALL be shown (the prior behaviour).

#### Scenario: Fuzzy search narrows by name or description

- **WHEN** the operator types into the rule search box
- **THEN** only rules whose name OR description contains the query as a case-insensitive subsequence SHALL remain visible
- **AND** clearing the box SHALL restore the full list

#### Scenario: Category filter restricts to one category

- **WHEN** the operator selects a category derived from the `<category>-<index>` rule names
- **THEN** only rules in that category SHALL be shown
- **AND** selecting "all categories" SHALL remove the category restriction

#### Scenario: Status filter and combination

- **WHEN** the operator chooses the Fail status filter
- **THEN** only failing rules SHALL be shown
- **AND** when a search query and a category are also active, only rules satisfying all three SHALL be shown
- **AND** the summary SHALL indicate the number of rules currently shown
