## ADDED Requirements

### Requirement: Commit-time template deduplication by canonical signature

The library SHALL deduplicate templates at commit time using a canonical signature derived from each template's entity point sets. The signature SHALL be a pure function of the input point sets — translation-invariant, entity-order-invariant, and vertex-order-invariant; distinct under rotation, scale, and reflection. The signature SHALL bucket coordinates at a 10⁻⁴ mm grid (0.1 µm), parallel to the existing `_radius_bucket_key` precision.

The dedup scope SHALL be `(library_id, class_name, effective_product_id)`, where `effective_product_id` is the file's `product_id` when the class belongs to `PRODUCT_SCOPED_CLASSES`, and `None` otherwise. Templates with the same signature in a different class, different library, or different effective product SHALL be considered distinct and SHALL produce new rows.

When a commit's canonical signature matches an existing template's signature within the same scope, the library SHALL short-circuit: NO new row is appended to the in-memory cache, NO row is inserted into the persistent store, and the existing template's id SHALL be returned to the caller.

`Library.add_template_for_file(template, *, product_id)` SHALL return `tuple[Template, bool]` where the second element is `True` when the call hit an existing template and `False` when a new row was inserted. `Library.add_template(template)` SHALL delegate as today and propagate the same tuple.

#### Scenario: Same point sets committed twice produces one row

- **WHEN** a template T1 is committed under class C in library L
- **AND** a template T2 with point sets equal to T1's (up to vertex order) is committed under the same class C in the same library L (and same effective product, if C is product-scoped)
- **THEN** the second `add_template_for_file` call returns `(T1_existing, True)`
- **AND** `lib.count(C)` is unchanged after the second call
- **AND** only ONE row exists in the `templates` table for the `(L, C, effective_pid)` tuple

#### Scenario: Same signature in different class is two rows

- **WHEN** a template with point sets P is committed under class C1
- **AND** a template with point sets P is committed under class C2 in the same library
- **THEN** both commits return `already_existed = False`
- **AND** each commit produces a distinct template id

#### Scenario: Same signature in different library is two rows

- **WHEN** a template with point sets P is committed under class C in library L1
- **AND** a template with point sets P is committed under class C in library L2
- **THEN** both commits return `already_existed = False`

#### Scenario: Same signature in same product-scoped class but different product is two rows

- **WHEN** a template with point sets P is committed under product-scoped class C with product_id=P1
- **AND** a template with point sets P is committed under the same class C with product_id=P2
- **THEN** both commits return `already_existed = False`
- **AND** each product's matcher sees its own row

#### Scenario: Rotated copy is a new row

- **WHEN** a template T1 is committed under class C
- **AND** a 90°-rotated copy of T1 is committed under the same class C in the same scope
- **THEN** the second commit returns `already_existed = False`
- **AND** `lib.count(C)` increments by 1

#### Scenario: Translation-equivalent shape framed at a different on-canvas position deduplicates

- **WHEN** a template T1 is committed from a frame-select at one on-canvas location
- **AND** the operator frame-selects a different instance of the same shape elsewhere on the same DXF and commits under the same class
- **THEN** the second commit returns `already_existed = True`
- **AND** the response's `template_id` is T1's id

### Requirement: Commit endpoint surfaces deduplication outcome

`POST /api/files/{file_id}/commit` response payload SHALL include an `already_existed: bool` field reflecting whether the commit hit an existing template. When `already_existed` is `True`, the `template_id` SHALL be the existing row's id, NOT a freshly-generated id from the discarded request. The `count` field SHALL be the post-call total for the class — equal to the pre-call count when `already_existed` is `True`, and pre-call + 1 when `False`.

#### Scenario: Commit response payload shape

- **WHEN** an operator commits a template under class C and the signature is novel
- **THEN** the response body contains `{"template_id": "<new-uuid>", "class_name": "C", "library_id": ..., "count": <pre + 1>, "already_existed": false}`

#### Scenario: Commit response on dedup hit references existing id

- **WHEN** an operator double-commits the same selection
- **THEN** the second response body's `template_id` equals the first response's `template_id`
- **AND** the second response body has `already_existed: true`
- **AND** the second response body's `count` equals the first response's `count`

### Requirement: Pre-dedup duplicate templates produce startup WARNING

On `Library` construction, after templates are loaded from the store, the library SHALL group its loaded templates by `(class_name, template_signature)`. For each group with count > 1, the library SHALL emit ONE log line at `WARNING` level identifying the library_id, class_name, and count. The duplicates SHALL NOT be deleted or migrated — the invariant in "Commit-time template deduplication" applies only to commits going forward.

#### Scenario: Library with two same-signature rows emits one warning

- **WHEN** the store contains two templates in library L, class C, with identical canonical signatures (e.g. pre-dedup commits, or rows inserted directly via the store)
- **AND** `LibraryRegistry.get(L)` is called for the first time, triggering `Library` construction
- **THEN** a single log record at level WARNING is emitted whose message contains `library L`, `class C`, and `2 templates`
- **AND** both rows remain in the in-memory cache and in the persistent store
