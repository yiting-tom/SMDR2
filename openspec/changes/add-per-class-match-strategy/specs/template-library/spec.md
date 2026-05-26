## ADDED Requirements

### Requirement: Per-class match strategy and bbox-ratio override

Each template class SHALL carry a `match_strategy ∈ {"chamfer", "signature"}`
(default `"chamfer"`) and an optional `bbox_ratio` (drawing-unit-ratio,
typically 0.05). The pair governs which matching pipeline the
matcher uses against templates filed under that class:

- `"chamfer"` (default): unchanged matcher pipeline (signature pre-filter
  using global ratios, scale window, PCA-aligned chamfer).
- `"signature"`: matcher uses signature gate alone. The class's
  `bbox_ratio` (or `0.05` when NULL under `signature`) replaces both
  `PATH_LENGTH_RATIO` and `RADIUS_RATIO` for that class.

`bbox_ratio` is operationally meaningful only under `signature`. Storing
`bbox_ratio` while `match_strategy == "chamfer"` is allowed (the value
persists) but the matcher SHALL ignore it.

The fields SHALL be:
- Stored on the `classes` table as a TEXT column `match_strategy` (default
  literal `'chamfer'`) and a REAL nullable column `bbox_ratio`.
- Settable via `PUT /api/libraries/{library_id}/classes/{class_name}/strategy`
  with body `{"strategy": "chamfer" | "signature", "bbox_ratio"?: number | null}`.
  When `strategy == "signature"` and `bbox_ratio` is omitted from the body,
  the server SHALL default `bbox_ratio` to `0.05`.
  When `strategy == "chamfer"`, the server SHALL clear `bbox_ratio` to NULL.
- Validated server-side: `strategy` MUST be one of the two allowed values
  (else HTTP 400); `bbox_ratio` MUST be a finite number in the open
  interval (0, 1] when provided (else HTTP 400).
- Exposed by `GET /api/libraries/{library_id}/classes` and the file-bound
  summary endpoint: each entry SHALL include `match_strategy` (string) and
  `bbox_ratio` (number or `null`).
- Migration-safe: existing libraries get `match_strategy = 'chamfer'` and
  `bbox_ratio = NULL` on every row when the columns are added, preserving
  pre-change behavior.

#### Scenario: Newly-created class defaults to chamfer
- **WHEN** a new class is added via `add_class`
- **THEN** the class's `match_strategy` is `"chamfer"` and `bbox_ratio` is `null`
- **AND** matching against templates in that class uses the unchanged chamfer pipeline

#### Scenario: Set strategy to signature with explicit bbox_ratio
- **WHEN** `PUT /api/libraries/{lib}/classes/Substrate/strategy` is called with body `{"strategy": "signature", "bbox_ratio": 0.05}`
- **THEN** the response is HTTP 200
- **AND** the class summary returns `match_strategy: "signature"`, `bbox_ratio: 0.05`

#### Scenario: Set strategy to signature without bbox_ratio defaults to 0.05
- **WHEN** `PUT /api/libraries/{lib}/classes/Substrate/strategy` is called with body `{"strategy": "signature"}`
- **THEN** the response is HTTP 200
- **AND** the class summary returns `match_strategy: "signature"`, `bbox_ratio: 0.05`

#### Scenario: Flipping back to chamfer clears bbox_ratio
- **WHEN** a class has `match_strategy: "signature"`, `bbox_ratio: 0.05`
- **AND** `PUT /api/libraries/{lib}/classes/Substrate/strategy` is called with body `{"strategy": "chamfer"}`
- **THEN** the class summary returns `match_strategy: "chamfer"`, `bbox_ratio: null`

#### Scenario: Unknown strategy is rejected
- **WHEN** `PUT /api/libraries/{lib}/classes/Substrate/strategy` is called with body `{"strategy": "fuzzy"}` or `{"strategy": null}`
- **THEN** the response is HTTP 400
- **AND** the stored fields are unchanged

#### Scenario: Invalid bbox_ratio is rejected
- **WHEN** `PUT /api/libraries/{lib}/classes/Substrate/strategy` is called with body `{"strategy": "signature", "bbox_ratio": 0}` or `{..., "bbox_ratio": -0.1}` or `{..., "bbox_ratio": 2}`
- **THEN** the response is HTTP 400
- **AND** the stored fields are unchanged

#### Scenario: Migration adds both columns to a pre-change DB
- **WHEN** an existing SQLite file pre-dating the columns is opened
- **THEN** migration adds `match_strategy TEXT DEFAULT 'chamfer'` and `bbox_ratio REAL NULL` columns
- **AND** every existing row has `match_strategy = 'chamfer'` and `bbox_ratio = NULL`
- **AND** matching behavior is identical to the pre-migration state
