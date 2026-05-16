## ADDED Requirements

### Requirement: RenderOutput carries source DXF $INSUNITS

`flatten_for_render` SHALL extract the source DXF's `$INSUNITS` header
value (`doc.header.get("$INSUNITS")`) and expose it on `RenderOutput`
as a nullable integer. The value SHALL be returned verbatim with no
remapping; consumers downstream are responsible for interpreting the
DXF spec enum (0 = unitless, 1 = inch, 2 = foot, 4 = mm, 5 = cm,
6 = m, …). When the header is missing or unparseable, the field SHALL
be `None`.

#### Scenario: A DXF with INSUNITS=4 (mm) is flattened
- **WHEN** a DXF whose header declares `$INSUNITS = 4` is flattened
- **THEN** `RenderOutput.insunits == 4`

#### Scenario: A DXF with no INSUNITS header is flattened
- **WHEN** a DXF whose header does not set `$INSUNITS` (or sets it to 0) is flattened
- **THEN** `RenderOutput.insunits` is `0` if explicitly set, else `None`
