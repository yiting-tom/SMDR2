## ADDED Requirements

### Requirement: Per-class view constraint registry

The system SHALL expose a data-driven registry
`library.CLASS_VIEW_CONSTRAINTS: dict[str, frozenset[str]]` that
maps a class **display ID** to the frozen set of allowed view
prefixes (`"top_view"`, `"bottom_view"`, `"side_view"`), encoding
the physical fact that some IC-packaging classes only appear in
specific views (e.g., a C4 bump only appears in the chip's
top-down view; a BGA ball only appears in the package's bottom or
side cross-section view).

The registry SHALL include at minimum:

| Display ID | Allowed views                  |
|------------|--------------------------------|
| `C4Ball`   | `{"top_view"}`                 |
| `BGABall`  | `{"bottom_view", "side_view"}` |

A class whose display ID is **absent** from the registry SHALL be
treated as unconstrained (matches in any view, including unassigned,
are allowed).

A class whose display ID **is** in the registry SHALL be treated
strictly: the "unassigned" position (no view rectangle covers the
instance) is never allowed, even if no relevant view rectangle is set
on the file. The match in that case is dropped, not preserved.

The system SHALL expose a helper
`library.is_allowed_view(class_name: str, view: str | None) -> bool`
returning `True` when the `(class_name, view)` pair is permitted under
the rule above. Both the match-JSON serialiser (see `dxf-pipeline`)
and the viewer's Scan All overlay (see `viewer-ui`) SHALL use this
helper as their single oracle.

#### Scenario: Unconstrained class admits every view
- **WHEN** `CLASS_VIEW_CONSTRAINTS` does not contain `"Substrate"`
- **THEN** `is_allowed_view("Substrate", "top_view")` returns `True`
- **AND** `is_allowed_view("Substrate", "bottom_view")` returns `True`
- **AND** `is_allowed_view("Substrate", "side_view")` returns `True`
- **AND** `is_allowed_view("Substrate", None)` returns `True`

#### Scenario: C4Ball is allowed only in top_view
- **WHEN** `CLASS_VIEW_CONSTRAINTS["C4Ball"] == frozenset({"top_view"})`
- **THEN** `is_allowed_view("C4Ball", "top_view")` returns `True`
- **AND** `is_allowed_view("C4Ball", "bottom_view")` returns `False`
- **AND** `is_allowed_view("C4Ball", "side_view")` returns `False`
- **AND** `is_allowed_view("C4Ball", None)` returns `False`

#### Scenario: BGABall is allowed only in bottom_view and side_view
- **WHEN** `CLASS_VIEW_CONSTRAINTS["BGABall"] == frozenset({"bottom_view", "side_view"})`
- **THEN** `is_allowed_view("BGABall", "bottom_view")` returns `True`
- **AND** `is_allowed_view("BGABall", "side_view")` returns `True`
- **AND** `is_allowed_view("BGABall", "top_view")` returns `False`
- **AND** `is_allowed_view("BGABall", None)` returns `False`

#### Scenario: Constrained class with unassigned position is rejected
- **WHEN** a file has no `top_view_rect` set
- **AND** a `C4Ball` match instance is therefore unassigned
- **THEN** `is_allowed_view("C4Ball", None)` returns `False`
- **AND** the instance SHALL be dropped by the match-JSON serialiser
  and by the Scan All overlay
