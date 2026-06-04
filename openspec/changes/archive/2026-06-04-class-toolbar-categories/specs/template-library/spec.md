## ADDED Requirements

### Requirement: Per-class category registry

The system SHALL expose a data-driven registry `library.CLASS_CATEGORY: dict[str, str]` mapping a class **display ID** to a category key, plus `library.CLASS_CATEGORY_ORDER: list[tuple[str, str]]` giving each category key a human-readable label in render order. Every class in `DEFAULT_CLASSES` SHALL have a `CLASS_CATEGORY` entry, and every category key used in `CLASS_CATEGORY` SHALL appear in `CLASS_CATEGORY_ORDER`; the module SHALL enforce both invariants at import time so a newly-added default class cannot be left uncategorised.

The default categorisation SHALL be:

| Category key | Label | Members |
|---|---|---|
| `structure` | Structure | Substrate, DieArea, DAM, Lid, LidOuter, LidInner, Protrusion |
| `balls` | Balls & Bumps | C4Ball, BGABall |
| `smd` | SMD Pads | SMD-2T, SMD-3T, SMD-8T, SMD-14T |
| `marks` | Fiducials & Marks | FiducialCircle, FiducialCross, FiducialSquare, Pin-1, 2DBarcode |

`CLASS_CATEGORY_ORDER` SHALL list the keys in the order `structure`, `balls`, `smd`, `marks`. A class **absent** from `CLASS_CATEGORY` (e.g. a user-defined class) SHALL be treated as uncategorised — consumers SHALL group it under a trailing fallback rather than drop it.

#### Scenario: Every default class is categorised
- **WHEN** the module is imported
- **THEN** every display ID in `DEFAULT_CLASSES` is a key of `CLASS_CATEGORY`
- **AND** every value of `CLASS_CATEGORY` is a key in `CLASS_CATEGORY_ORDER`

#### Scenario: Categories are ordered structure then balls then smd then marks
- **WHEN** reading `CLASS_CATEGORY_ORDER`
- **THEN** its keys are exactly `["structure", "balls", "smd", "marks"]` in that order
- **AND** each entry carries a non-empty display label

#### Scenario: DAM is structural and fiducials and marks are merged
- **WHEN** reading `CLASS_CATEGORY`
- **THEN** `CLASS_CATEGORY["DAM"]` is `"structure"`
- **AND** `CLASS_CATEGORY["Pin-1"]` and `CLASS_CATEGORY["2DBarcode"]` are `"marks"`
- **AND** the three `Fiducial*` classes also map to `"marks"`

#### Scenario: Uncategorised class is not dropped
- **WHEN** a library contains a class with no `CLASS_CATEGORY` entry
- **THEN** the categorisation treats it as uncategorised (the toolbar groups it under a trailing fallback), never omitting it
