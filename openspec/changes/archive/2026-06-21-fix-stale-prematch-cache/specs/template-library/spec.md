## ADDED Requirements

### Requirement: Library revision counter

Each library SHALL carry a monotonic, non-decreasing integer `revision` that
the system increments on every mutation able to change scan-all / pre-match
results: inserting a template, deleting a template, moving a template to another
class, and changing a class's match strategy. The system SHALL expose
`Store.current_revision(library_id) -> int` returning the library's current
revision. Only inequality of two revision values is significant; absolute values
and occasional skipped values carry no meaning.

#### Scenario: Inserting a template bumps the revision

- **WHEN** `insert_template` commits a new template to a library at revision `r`
- **THEN** `current_revision(library_id)` returns a value strictly greater than `r`

#### Scenario: Deleting a template bumps the revision

- **WHEN** `delete_template` removes a template from a library at revision `r`
- **THEN** `current_revision(library_id)` returns a value strictly greater than `r`

#### Scenario: Moving a template to another class bumps the revision

- **WHEN** `update_template_class` reassigns a template's class in a library at revision `r`
- **THEN** `current_revision(library_id)` returns a value strictly greater than `r`

#### Scenario: Changing a class strategy bumps the revision

- **WHEN** `update_class_strategy` changes a class's match strategy in a library at revision `r`
- **THEN** `current_revision(library_id)` returns a value strictly greater than `r`

#### Scenario: A pure read does not bump the revision

- **WHEN** the library is loaded or queried without any template/class mutation
- **THEN** `current_revision(library_id)` is unchanged
