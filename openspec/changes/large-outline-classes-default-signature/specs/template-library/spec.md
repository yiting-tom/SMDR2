## ADDED Requirements

### Requirement: Built-in large-outline classes default to signature matching

The built-in large rigid-outline classes `Substrate`, `LidOuter`, and `LidInner` SHALL seed with a `signature` match strategy and `bbox_ratio = 0.0001`, rather than the `chamfer` / NULL default used by other classes. Their outline is fully characterised by perimeter + max-radius + principal-axis aspect, and PCA-chamfer scoring of such a large sharp-cornered loop is sensitive to the stored winding / start vertex; signature matching keys only on size + aspect and avoids that.

The default SHALL be applied both when the class is first seeded into a library and, for libraries that already carry the class row, via a boot migration. The boot migration SHALL convert only rows still in the pristine `chamfer` / NULL state; an explicit signature configuration (a non-null `bbox_ratio`) set through the class-strategy API SHALL be preserved across restarts.

#### Scenario: Large-outline classes seed as signature

- **WHEN** a fresh library is created and its default classes are seeded
- **THEN** `Substrate`, `LidOuter`, and `LidInner` SHALL each report `match_strategy = "signature"` and `bbox_ratio = 0.0001`
- **AND** all other default classes SHALL report `match_strategy = "chamfer"` and `bbox_ratio = null`

#### Scenario: Existing library converts on boot

- **WHEN** a library predating this change carries a `Substrate` class row at `match_strategy = "chamfer"` / `bbox_ratio = NULL`
- **AND** the store is reopened
- **THEN** that row SHALL be converted to `("signature", 0.0001)`

#### Scenario: Explicit signature override is preserved

- **WHEN** an operator sets `Substrate` to `signature` with a `bbox_ratio` of `0.05` through the class-strategy API
- **AND** the store is reopened
- **THEN** `Substrate` SHALL still report `("signature", 0.05)` — the boot migration converts only pristine `chamfer` / NULL rows, never an explicit signature configuration
